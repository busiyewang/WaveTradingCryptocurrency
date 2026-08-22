"""缠论 K 线分析工具 — 本地服务。

启动:.venv/bin/python server.py   →  浏览器打开 http://127.0.0.1:8420
数据:OKX 公开行情接口(无需 API key),内存缓存,点"刷新"重拉。
扩展余地:如需实时推送,可在此文件加 websocket(如 flask-sock),前端 app.js
的 loadData() 改为订阅即可,chan.py / indicators.py 无需改动。
"""

import time

import requests
from flask import Flask, jsonify, request, send_from_directory

import chan
import decision
import indicators
import multilevel
import patterns

OKX_BASE = "https://www.okx.com"
# OKX bar 参数大小写敏感:分钟小写,小时/日/周大写
VALID_BARS = ["1m", "5m", "15m", "1H", "4H", "1D", "1W"]
TARGET_COUNT = 600  # 目标K线根数(300 一页,分页补齐)

COMMON_INSTRUMENTS = [
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
    "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "BNB-USDT-SWAP",
    "RESOLV-USDT-SWAP",
]

app = Flask(__name__, static_folder="static", static_url_path="/static")

# 内存缓存:{(inst, bar): {"ts": 拉取时间, "data": 响应dict}}
_cache: dict[tuple, dict] = {}


def _parse_rows(rows):
    """OKX 返回行 [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm](倒序)。"""
    out = []
    for r in rows:
        out.append({
            "ts": int(r[0]), "o": float(r[1]), "h": float(r[2]),
            "l": float(r[3]), "c": float(r[4]), "vol": float(r[5]),
            "confirm": int(r[8]),
        })
    return out


def fetch_okx_candles(inst_id: str, bar: str, target: int = TARGET_COUNT):
    """拉取K线并按时间升序返回。首页用 /market/candles,更早数据用
    /market/history-candles + after 分页。"""
    sess = requests.Session()
    url = f"{OKX_BASE}/api/v5/market/candles"
    resp = sess.get(url, params={"instId": inst_id, "bar": bar, "limit": "300"},
                    timeout=15)
    body = resp.json()
    if body.get("code") != "0":
        raise RuntimeError(f"OKX 返回错误: {body.get('msg') or body.get('code')}")
    candles = _parse_rows(body["data"])  # 倒序:最新在前

    hist_url = f"{OKX_BASE}/api/v5/market/history-candles"
    while len(candles) < target and candles:
        oldest_ts = candles[-1]["ts"]
        r = sess.get(hist_url, params={"instId": inst_id, "bar": bar,
                                       "after": str(oldest_ts), "limit": "100"},
                     timeout=15)
        b = r.json()
        if b.get("code") != "0" or not b.get("data"):
            break
        candles.extend(_parse_rows(b["data"]))
        time.sleep(0.12)  # history-candles 限速 20次/2s,稍作间隔

    candles.reverse()  # 转升序
    return candles


def build_payload(inst_id: str, bar: str):
    candles = fetch_okx_candles(inst_id, bar)
    if not candles:
        raise RuntimeError("未获取到K线数据,请检查币种代码")
    # 缠论分析与指标只用已完结K线,避免最后一根未收盘造成假分型/指标抖动
    confirmed = [c for c in candles if c["confirm"] == 1]
    analysis = chan.analyze(confirmed, bar)
    # 现价与位置用最新(含未收盘)价格,对交易者更有意义
    s = analysis.get("summary") or {}
    live = candles[-1]["c"]
    s["last_price"] = live
    if s.get("last_zs"):
        z = s["last_zs"]
        s["price_pos"] = "above_zg" if live > z["zg"] else \
                         ("below_zd" if live < z["zd"] else "inside")
    inds = indicators.compute_all(candles)
    signals = indicators.analyze_signals(confirmed, analysis.get("bi"))
    pats = patterns.detect_triangle(analysis.get("bi") or [], confirmed)
    return {
        "patterns": pats,
        "code": 0, "inst": inst_id, "bar": bar,
        "fetched_at": int(time.time() * 1000),
        "count": len(candles),
        "candles": candles,
        "indicators": inds,
        "chan": analysis,
        "signals": signals,
    }


@app.get("/api/kline")
def api_kline():
    inst = request.args.get("inst", "ETH-USDT-SWAP").upper().strip()
    bar = request.args.get("bar", "4H")
    force = request.args.get("force", "0") == "1"
    if bar not in VALID_BARS:
        return jsonify({"code": 1, "msg": f"不支持的周期: {bar}"}), 400
    try:
        data = _get_data(inst, bar, force)
        # 交易决策:结合上一级别方向(级别相同则用自身)
        hi_bar = decision.LEVEL_UP.get(bar, bar)
        hi_data = data if hi_bar == bar else _get_data(inst, hi_bar, force)
        dec = decision.decide(data, hi_data, bar, hi_bar)
    except requests.RequestException as e:
        return jsonify({"code": 1, "msg": f"网络错误: {e}"}), 502
    except RuntimeError as e:
        return jsonify({"code": 1, "msg": str(e)}), 400
    return jsonify({**data, "decision": dec})


def _get_data(inst: str, bar: str, force: bool):
    key = (inst, bar)
    if not force and key in _cache:
        return _cache[key]["data"]
    data = build_payload(inst, bar)
    _cache[key] = {"ts": time.time(), "data": data}
    return data


@app.get("/api/multi")
def api_multi():
    """多级别联动:大级别定方向+关键位,小级别找确认(区间套)。"""
    inst = request.args.get("inst", "ETH-USDT-SWAP").upper().strip()
    big = request.args.get("big", "4H")
    small = request.args.get("small") or multilevel.SMALL_DEFAULT.get(big, "15m")
    force = request.args.get("force", "0") == "1"
    if big not in VALID_BARS or small not in VALID_BARS:
        return jsonify({"code": 1, "msg": f"不支持的周期: {big}/{small}"}), 400
    if multilevel.BAR_RANK[small] >= multilevel.BAR_RANK[big]:
        return jsonify({"code": 1, "msg": "小级别必须低于大级别"}), 400
    try:
        big_data = _get_data(inst, big, force)
        small_data = _get_data(inst, small, force)
        multi = multilevel.analyze(big_data, small_data, big, small)
    except requests.RequestException as e:
        return jsonify({"code": 1, "msg": f"网络错误: {e}"}), 502
    except RuntimeError as e:
        return jsonify({"code": 1, "msg": str(e)}), 400
    return jsonify({"code": 0, "inst": inst, "big_bar": big, "small_bar": small,
                    "fetched_at": int(time.time() * 1000),
                    "big": big_data, "small": small_data, "multi": multi})


@app.get("/multi")
def multi_page():
    return send_from_directory("static", "multi.html")


@app.get("/api/instruments")
def api_instruments():
    return jsonify({"code": 0, "instruments": COMMON_INSTRUMENTS})


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    print("缠论K线分析工具: http://127.0.0.1:8420")
    app.run(host="127.0.0.1", port=8420, debug=False)
