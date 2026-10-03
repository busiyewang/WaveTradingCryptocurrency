"""链上 · 资金环境(只读面板,不参与缠论决策)。

分工:
- Glassnode(日线,全市场口径):周期估值、交易所资金流、全市场资金费率/爆仓、STH成本线。
  当前 key 只开放 24h 分辨率,且限流较严(约每分钟个位数请求),因此后台线程
  按币种排队、每次请求间隔 GN_SPACING 秒,每 GN_TTL 秒复查一次。
- OKX 公开接口(5m/1H,仅 OKX 单所口径):当前资金费率、持仓量与价格、
  多空账户比、主动买卖比。每 OKX_TTL 秒刷新一次。

每个指标先归一化为"利多度" bull∈[0,1](0.5=中性),
对做多支持度 = Σw·bull / Σw,对做空支持度 = Σw·(1−bull) / Σw,均为 0-100、越高越好。
任一接口失败:保留上一次数据并标记已过期;从未成功则该行显示不可用及原因。
"""

import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

GN_BASE = "https://api.glassnode.com/v1/metrics/"
OKX_BASE = "https://www.okx.com/api/v5/"
GN_SPACING = 7.0          # Glassnode 请求间隔(秒),实测连发 4-5 次即 429
GN_TTL = 6 * 3600         # Glassnode 日线数据复查周期(发布时间不固定,6h 查一次)
GN_STALE_DAYS = 3         # 最新数据点早于 N 天视为过期
GN_HISTORY_DAYS = 120     # 请求窗口;当前套餐实际只返回最近约14天,超出部分被服务端截断
GN_KEEP_DAYS = 120        # 进程内累积保留的历史天数(每次拉取与旧数据按时间合并)
OKX_TTL = 300             # OKX 刷新周期(秒)
OKX_STALE = 900           # OKX 数据超过 15 分钟未更新视为过期

# 指标权重(可调):拥挤度与资金流对短线影响更直接,周期估值权重较低
WEIGHTS = {
    "funding": 1.0, "oi_price": 1.0, "ls_ratio": 0.7, "taker": 0.5,
    "netflow": 1.0, "usdt_ex": 0.5, "liq": 0.6,
    "cost_line": 0.8, "mvrv_z": 0.5, "nupl": 0.4, "sopr": 0.4,
}

# 支持度档位(对应第3步仓位规则:≥60 正常、40-60 中性、20-40 降一档、<20 降两档/暂停新开)
LEVELS = [(60, "顺风"), (40, "中性"), (20, "逆风"), (0, "强逆风")]


def level_name(score: float) -> str:
    for th, name in LEVELS:
        if score >= th:
            return name
    return LEVELS[-1][1]


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _load_key() -> str:
    key = os.environ.get("GLASSNODE_API_KEY", "").strip()
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GLASSNODE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------- 归一化规则
# 每个函数返回 (bull, state, note);bull 越大越利多。阈值写在这里便于复核。

def score_mvrv_z(z):
    if z <= 0:
        b = 0.8
    elif z <= 2:
        b = 0.6
    elif z <= 4:
        b = 0.5
    elif z <= 6:
        b = 0.3
    else:
        b = 0.15
    note = ("低于0:市值低于全体成本,历史大底区" if z <= 0 else
            "周期中低位,估值不贵" if z <= 2 else
            "周期中位" if z <= 4 else
            "估值偏高,注意周期顶部风险" if z <= 6 else "历史顶部区域")
    return b, note


def score_nupl(v):
    if v < 0:
        b, note = 0.8, "全网整体亏损(投降区),逆向利多"
    elif v < 0.25:
        b, note = 0.65, "浮盈较薄(希望/恐惧区)"
    elif v < 0.5:
        b, note = 0.5, "浮盈中等(乐观区)"
    elif v < 0.75:
        b, note = 0.35, "浮盈丰厚(信念区),获利了结压力上升"
    else:
        b, note = 0.15, "极度贪婪区,历史顶部特征"
    return b, note


def score_sopr(ma7, prev_ma7):
    rising = ma7 >= prev_ma7
    if ma7 >= 1:
        b = 0.6 if rising else 0.5
        note = "7日均值>1:链上整体获利卖出" + (",且在走高" if rising else ",但在回落")
    else:
        b = 0.4 if not rising else 0.45
        note = "7日均值<1:链上整体亏损卖出" + (",有所修复" if rising else ",仍在恶化")
    return b, note


def score_netflow(sum3, std_daily):
    """近3日交易所净流入(原生币)。流出=筹码离开交易所=利多。"""
    if not std_daily:
        return 0.5, "历史波动不足,无法判断"
    z = sum3 / (std_daily * 3 ** 0.5)
    b = _clamp(0.5 - 0.15 * z, 0.1, 0.9)
    if z <= -1:
        note = "近3日明显净流出,筹码离开交易所(利多)"
    elif z >= 1:
        note = "近3日明显净流入,潜在卖压(利空)"
    else:
        note = "近3日净流量在正常范围"
    return b, note


def score_usdt_ex(chg7_pct):
    """USDT 交易所余额 7 日变化:增加=场内买盘弹药增加。"""
    b = _clamp(0.5 + chg7_pct * 0.1, 0.2, 0.8)
    note = ("场内稳定币增加,买盘弹药充足" if chg7_pct > 1 else
            "场内稳定币减少,买盘弹药流失" if chg7_pct < -1 else "场内稳定币基本持平")
    return b, note


def score_funding(rate):
    """资金费率(每8小时,小数)。正=多头付费。极端正值=多头拥挤,逆向偏空。"""
    pct = rate * 100
    if pct <= -0.01:
        b, note = 0.75, "费率为负:空头拥挤,存在轧空可能"
    elif pct <= 0.005:
        b, note = 0.6, "费率偏低:多头不拥挤"
    elif pct <= 0.015:
        b, note = 0.5, "费率正常(基准约0.01%)"
    elif pct <= 0.03:
        b, note = 0.35, "费率偏热:多头偏拥挤"
    else:
        b, note = 0.2, "费率过热:多头极度拥挤,警惕多杀多"
    return b, note


def score_oi_price(d_price_pct, d_oi_pct):
    """24h 价格变化与持仓量变化组合。"""
    if abs(d_price_pct) < 0.5 or abs(d_oi_pct) < 1:
        return 0.5, "价格或持仓变化不明显"
    if d_price_pct > 0 and d_oi_pct > 0:
        return 0.65, "价↑仓↑:新多进场,上涨有资金推动"
    if d_price_pct > 0:
        return 0.45, "价↑仓↓:主要是空头平仓推动,突破可信度低"
    if d_oi_pct > 0:
        return 0.35, "价↓仓↑:新空进场,下跌有资金推动"
    return 0.55, "价↓仓↓:多头平仓去杠杆,抛压在释放"


def score_ls_ratio(pctile):
    """多空账户比在近期的分位(0-1)。散户越偏多越拥挤,逆向偏空。"""
    b = _clamp(0.5 - (pctile - 0.5) * 0.6, 0.2, 0.8)
    note = ("多头账户占比处于近期高位,散户偏多拥挤" if pctile >= 0.8 else
            "多头账户占比处于近期低位,散户偏空" if pctile <= 0.2 else "多空账户比处于近期中段")
    return b, note


def score_taker(ratio):
    """近1小时主动买/主动卖。"""
    b = _clamp(0.5 + (ratio - 1) * 0.5, 0.3, 0.7)
    note = ("主动买盘占优" if ratio > 1.1 else "主动卖盘占优" if ratio < 0.9 else "主动买卖均衡")
    return b, note


def score_liq(long_last, short_last, long_mean, short_mean):
    """全市场爆仓:多头集中爆仓=多头出清(逆向利多),空头集中爆仓=轧空后易回落。"""
    lr = long_last / long_mean if long_mean else 0
    sr = short_last / short_mean if short_mean else 0
    if lr >= 2 and long_last > short_last:
        return 0.65, f"多头爆仓达近期日均{lr:.1f}倍:多头出清,配合底背驰更可信"
    if sr >= 2 and short_last > long_last:
        return 0.35, f"空头爆仓达近期日均{sr:.1f}倍:轧空后易回落"
    return 0.5, "爆仓量在正常范围"


def score_cost_line(price, cost):
    d = (price - cost) / cost * 100
    if abs(d) <= 2:
        return 0.5, f"现价在成本线±2%内({d:+.1f}%),正在测试"
    if d > 0:
        return 0.65, f"现价高于成本线 {d:.1f}%:短期持有者整体盈利,成本线为支撑"
    return 0.35, f"现价低于成本线 {abs(d):.1f}%:短期持有者整体亏损,成本线为压力"


# ---------------------------------------------------------------- Glassnode

# (key, 路径, 资产)。资产为 None 表示用当前币;"USDT" 为全局共享
GN_METRICS = [
    ("mvrv_z", "market/mvrv_z_score", None),
    ("nupl", "indicators/net_unrealized_profit_loss", None),
    ("sopr", "indicators/sopr", None),
    ("netflow", "transactions/transfers_volume_exchanges_net", None),
    ("funding_gn", "derivatives/futures_funding_rate_perpetual", None),
    ("liq_long", "derivatives/futures_liquidated_volume_long_sum", None),
    ("liq_short", "derivatives/futures_liquidated_volume_short_sum", None),
    ("cost_sth", "market/price_realized_less_155_usd", None),
    ("cost_all", "market/price_realized_usd", None),
    ("usdt_ex", "distribution/balance_exchanges", "USDT"),
]


class _GnStore:
    """Glassnode 原始序列缓存 + 后台排队拉取。"""

    def __init__(self):
        self.series = {}     # (asset, key) -> {"rows": [...], "fetched_at": ts}
        self.errors = {}     # (asset, key) -> {"reason": str, "at": ts}
        self.checked = {}    # (asset, key) -> 上次尝试时间
        self.queue = queue.Queue()
        self.queued = set()
        self.busy = None
        self.lock = threading.Lock()
        self.thread = None

    def ensure(self, asset: str):
        """若该币有待刷新指标,排队后台拉取(不阻塞请求)。"""
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._worker, daemon=True)
                self.thread.start()
            if asset in self.queued:
                return
            if any(self._due(self._asset_of(asset, m), m[0]) for m in GN_METRICS):
                self.queued.add(asset)
                self.queue.put(asset)

    def pending(self, asset: str) -> bool:
        with self.lock:
            return asset in self.queued

    @staticmethod
    def _asset_of(asset, m):
        return m[2] or asset

    def _due(self, a, key):
        last = self.checked.get((a, key))
        if last is None:
            return True
        err = self.errors.get((a, key))
        if err and err.get("permanent"):
            return time.time() - last > 24 * 3600
        if err:  # 临时错误(限流/网络)15 分钟后重试
            return time.time() - last > 900
        return time.time() - last > GN_TTL

    def _worker(self):
        sess = requests.Session()
        while True:
            asset = self.queue.get()
            try:
                for m in GN_METRICS:
                    a = self._asset_of(asset, m)
                    if not self._due(a, m[0]):
                        continue
                    self._fetch(sess, a, m)
                    time.sleep(GN_SPACING)
            finally:
                with self.lock:
                    self.queued.discard(asset)

    def _fetch(self, sess, a, m):
        key, path, _ = m
        api_key = _load_key()
        k = (a, key)
        if not api_key:
            self._err(k, "未配置 GLASSNODE_API_KEY", permanent=True)
            return
        params = {"a": a, "i": "24h", "s": int(time.time()) - GN_HISTORY_DAYS * 86400}
        for attempt in range(2):
            try:
                r = sess.get(GN_BASE + path, params=params,
                             headers={"X-Api-Key": api_key}, timeout=25)
            except requests.RequestException as e:
                self._err(k, f"网络错误:{type(e).__name__}")
                return
            if r.status_code == 429 and attempt == 0:
                time.sleep(30)
                continue
            break
        if r.status_code == 200:
            rows = [x for x in r.json() if isinstance(x.get("v"), (int, float))]
            with self.lock:
                # 套餐只给近两周:与已缓存历史按时间合并,进程运行越久历史越长
                old = self.series.get(k, {}).get("rows", [])
                by_t = {x["t"]: x for x in old}
                by_t.update({x["t"]: x for x in rows})
                merged = [by_t[t] for t in sorted(by_t)][-GN_KEEP_DAYS:]
                self.series[k] = {"rows": merged, "fetched_at": time.time()}
                self.errors.pop(k, None)
                self.checked[k] = time.time()
            return
        body = r.text[:200]
        if r.status_code == 429:
            self._err(k, "Glassnode 限流(429),稍后自动重试")
        elif r.status_code == 400 and "invalid" in body:
            self._err(k, f"Glassnode 不支持 {a}", permanent=True)
        elif r.status_code in (401, 403):
            reason = "套餐不支持该指标" if "requiredPlan" in body or "not allowed" in body \
                else "Glassnode 认证失败"
            self._err(k, reason, permanent=True)
        else:
            self._err(k, f"Glassnode 接口错误 {r.status_code}")

    def _err(self, k, reason, permanent=False):
        with self.lock:
            self.errors[k] = {"reason": reason, "at": time.time(), "permanent": permanent}
            self.checked[k] = time.time()

    def get(self, a, key):
        with self.lock:
            return self.series.get((a, key)), self.errors.get((a, key))


_gn = _GnStore()


# ---------------------------------------------------------------- OKX

_okx_cache = {}   # inst -> {"at": ts, "data": {...}, "errors": {...}}
_okx_lock = threading.Lock()


def _okx_get(sess, path, params):
    r = sess.get(OKX_BASE + path, params=params, timeout=15)
    body = r.json()
    if body.get("code") != "0":
        raise RuntimeError(body.get("msg") or f"code {body.get('code')}")
    return body["data"]


def _fetch_okx(inst: str):
    sess = requests.Session()
    jobs = {
        "funding": ("public/funding-rate", {"instId": inst}),
        "oi_1h": ("rubik/stat/contracts/open-interest-history",
                  {"instId": inst, "period": "1H", "limit": "100"}),
        "ls_1h": ("rubik/stat/contracts/long-short-account-ratio-contract",
                  {"instId": inst, "period": "1H", "limit": "100"}),
        "taker_5m": ("rubik/stat/taker-volume-contract",
                     {"instId": inst, "period": "5m", "limit": "12"}),
    }
    data, errors = {}, {}
    # rubik 接口限速 5次/2s,4 个并发在限额内
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {k: ex.submit(_okx_get, sess, p, q) for k, (p, q) in jobs.items()}
        for k, f in futs.items():
            try:
                data[k] = f.result()
            except requests.RequestException as e:
                errors[k] = f"OKX 网络错误:{type(e).__name__}"
            except (RuntimeError, ValueError) as e:
                errors[k] = f"OKX 接口错误:{e}"
    return data, errors


def _okx_snapshot(inst: str):
    now = time.time()
    with _okx_lock:
        c = _okx_cache.get(inst)
        if c and now - c["at"] < OKX_TTL:
            return c
    data, errors = _fetch_okx(inst)
    with _okx_lock:
        old = _okx_cache.get(inst) or {"data": {}, "got": {}}
        merged, got = dict(old["data"]), dict(old.get("got", {}))
        for k, v in data.items():
            merged[k] = v
            got[k] = now
        c = {"at": now, "data": merged, "got": got, "errors": errors}
        _okx_cache[inst] = c
        return c


# ---------------------------------------------------------------- 组装

def _row(key, name, group, source, scope, value_text, bull_note, data_time,
         stale=False, note_extra="", series=None):
    bull, note = bull_note
    state = "利多" if bull >= 0.58 else "利空" if bull <= 0.42 else "中性"
    return {
        "key": key, "name": name, "group": group, "source": source, "scope": scope,
        "status": "ok", "value_text": value_text, "bull": round(bull, 3),
        "weight": WEIGHTS.get(key, 0), "state": state,
        "note": note + note_extra, "data_time": data_time, "stale": stale,
        "series": series or [],
    }


def _na(key, name, group, source, scope, reason):
    return {"key": key, "name": name, "group": group, "source": source,
            "scope": scope, "status": "na", "reason": reason,
            "weight": WEIGHTS.get(key, 0)}


def _gn_rows(asset):
    """读取缓存;返回 (rows_or_None, reason, data_time_ms, stale)。"""
    def get(key, a=None):
        s, err = _gn.get(a or asset, key)
        if s and s["rows"]:
            t = s["rows"][-1]["t"] * 1000
            stale = time.time() - s["rows"][-1]["t"] > GN_STALE_DAYS * 86400 + 86400
            if err:
                stale = True
            return s["rows"], None, t, stale
        if err:
            return None, err["reason"], None, False
        return None, "加载中(Glassnode 限流,逐项拉取)", None, False
    return get


def _ser(rows, n=60):
    """Glassnode 行 → [[ms, v], ...](升序,截取最近 n 个)。"""
    return [[x["t"] * 1000, x["v"]] for x in rows[-n:]]


def _std(xs):
    if len(xs) < 2:
        return 0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _fmt_big(v):
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:.2f}M"
    if a >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:.2f}"


def build(inst: str, last_price: Optional[float] = None):
    asset = inst.split("-")[0].upper()
    _gn.ensure(asset)
    gn = _gn_rows(asset)
    okx = _okx_snapshot(inst)
    od, oerr, ogot = okx["data"], okx.get("errors", {}), okx.get("got", {})
    now = time.time()

    def ostale(k):
        return k in oerr or now - ogot.get(k, now) > OKX_STALE

    rows = []
    G_CYC, G_FLOW, G_CROWD = "周期", "资金流", "拥挤度"

    # 现价:优先 K 线实时价,否则用 OKX 持仓 USD/币 推算
    price = last_price
    if price is None and od.get("oi_1h"):
        r0 = od["oi_1h"][0]
        if float(r0[2]):
            price = float(r0[3]) / float(r0[2])

    # ---- 拥挤度(OKX 日内 + Glassnode 全市场)
    fund_vals = []
    f_okx = od.get("funding")
    if f_okx:
        fr = float(f_okx[0]["fundingRate"])
        fund_vals.append(fr)
    gnf, reason_f, tf, stf = gn("funding_gn")
    if gnf:
        fund_vals.append(gnf[-1]["v"])
    if fund_vals:
        parts = []
        if f_okx:
            parts.append(f"OKX当前 {fr * 100:.4f}%")
        if gnf:
            parts.append(f"全市场日均 {gnf[-1]['v'] * 100:.4f}%")
        avg = sum(fund_vals) / len(fund_vals)
        src = "+".join((["OKX"] if f_okx else []) + (["Glassnode"] if gnf else []))
        scope = "OKX单所+全市场" if f_okx and gnf else ("OKX单所" if f_okx else "全市场")
        rows.append(_row("funding", "资金费率", G_CROWD, src, scope, " · ".join(parts),
                         score_funding(avg), int(ogot.get("funding", now) * 1000) if f_okx else tf,
                         series=_ser(gnf) if gnf else [],
                         stale=(ostale("funding") if f_okx else stf),
                         note_extra="" if gnf else f"(全市场:{reason_f})"))
    else:
        rows.append(_na("funding", "资金费率", G_CROWD, "OKX+Glassnode", "",
                        oerr.get("funding") or reason_f))

    oi = od.get("oi_1h")
    if oi and len(oi) >= 25:
        # 行格式 [ts, oi张, oi币, oiUsd],倒序
        def px(r):
            return float(r[3]) / float(r[2]) if float(r[2]) else 0
        now_r, ago_r, ago4 = oi[0], oi[24], oi[4]
        d_oi = (float(now_r[2]) / float(ago_r[2]) - 1) * 100
        d_px = (px(now_r) / px(ago_r) - 1) * 100
        d_oi4 = (float(now_r[2]) / float(ago4[2]) - 1) * 100
        d_px4 = (px(now_r) / px(ago4) - 1) * 100
        rows.append(_row("oi_price", "持仓 / 价格(24h)", G_CROWD, "OKX", "OKX单所",
                         f"价 {d_px:+.2f}% · 仓 {d_oi:+.2f}%(4h:价 {d_px4:+.2f}% 仓 {d_oi4:+.2f}%)",
                         score_oi_price(d_px, d_oi), int(now_r[0]), stale=ostale("oi_1h"),
                         series=[[int(r[0]), float(r[2])] for r in reversed(oi)]))
    else:
        rows.append(_na("oi_price", "持仓 / 价格(24h)", G_CROWD, "OKX", "OKX单所",
                        oerr.get("oi_1h") or "OKX 持仓历史不足"))

    ls = od.get("ls_1h")
    if ls and len(ls) >= 20:
        vals = [float(r[1]) for r in ls]
        cur = vals[0]
        pct = sum(1 for v in vals if v <= cur) / len(vals)
        rows.append(_row("ls_ratio", "多空账户比", G_CROWD, "OKX", "OKX单所",
                         f"{cur:.2f}(近{len(vals)}小时分位 {pct * 100:.0f}%)",
                         score_ls_ratio(pct), int(ls[0][0]), stale=ostale("ls_1h"),
                         series=[[int(r[0]), float(r[1])] for r in reversed(ls)]))
    else:
        rows.append(_na("ls_ratio", "多空账户比", G_CROWD, "OKX", "OKX单所",
                        oerr.get("ls_1h") or "OKX 多空比数据不足"))

    tk = od.get("taker_5m")
    if tk:
        buy = sum(float(r[1]) for r in tk)
        sell = sum(float(r[2]) for r in tk)
        if sell > 0:
            ratio = buy / sell
            rows.append(_row("taker", "主动买/卖(1h)", G_CROWD, "OKX", "OKX单所",
                             f"{ratio:.2f}", score_taker(ratio), int(tk[0][0]),
                             stale=ostale("taker_5m"),
                             series=[[int(r[0]), float(r[1]) / float(r[2]) if float(r[2]) else 1]
                                     for r in reversed(tk)]))
    if not any(r["key"] == "taker" for r in rows):
        rows.append(_na("taker", "主动买/卖(1h)", G_CROWD, "OKX", "OKX单所",
                        oerr.get("taker_5m") or "OKX 主动买卖数据不足"))

    ll, rl, tl, sl = gn("liq_long")
    lsh, rs, _, _ = gn("liq_short")
    if ll and lsh and len(ll) >= 8 and len(lsh) >= 8:
        # 均值不含最新日;套餐只给约两周,累积后最多取30日
        lm = sum(x["v"] for x in ll[-31:-1]) / len(ll[-31:-1])
        sm = sum(x["v"] for x in lsh[-31:-1]) / len(lsh[-31:-1])
        rows.append(_row("liq", "爆仓量(日)", G_CROWD, "Glassnode", "全市场",
                         f"多 {_fmt_big(ll[-1]['v'])} · 空 {_fmt_big(lsh[-1]['v'])} {asset}",
                         score_liq(ll[-1]["v"], lsh[-1]["v"], lm, sm), tl, stale=sl,
                         series=_ser(ll)))
    else:
        rows.append(_na("liq", "爆仓量(日)", G_CROWD, "Glassnode", "全市场", rl or rs))

    # ---- 资金流
    nf, rn, tn, sn = gn("netflow")
    if nf and len(nf) >= 8:
        vals = [x["v"] for x in nf]
        sum3 = sum(vals[-3:])
        rows.append(_row("netflow", "交易所净流入(3日)", G_FLOW, "Glassnode", "全市场",
                         f"{_fmt_big(sum3)} {asset}(最新日 {_fmt_big(vals[-1])})",
                         score_netflow(sum3, _std(vals[:-3] or vals)), tn, stale=sn,
                         series=_ser(nf)))
    else:
        rows.append(_na("netflow", "交易所净流入(3日)", G_FLOW, "Glassnode", "全市场", rn))

    us, ru, tu, su = gn("usdt_ex", "USDT")
    if us and len(us) >= 8:
        chg = (us[-1]["v"] / us[-8]["v"] - 1) * 100
        rows.append(_row("usdt_ex", "USDT 交易所余额", G_FLOW, "Glassnode", "全市场",
                         f"{_fmt_big(us[-1]['v'])}(7日 {chg:+.2f}%)",
                         score_usdt_ex(chg), tu, stale=su, series=_ser(us)))
    else:
        rows.append(_na("usdt_ex", "USDT 交易所余额", G_FLOW, "Glassnode", "全市场", ru))

    # ---- 周期
    cs, rcs, tcs, scs = gn("cost_sth")
    ca, rca, tca, sca = gn("cost_all")
    cost_line = None
    if cs:
        cost_line = {"name": "STH成本线", "price": cs[-1]["v"], "data_time": tcs, "stale": scs}
    elif ca:
        cost_line = {"name": "全体成本(实现价格)", "price": ca[-1]["v"], "data_time": tca, "stale": sca}
    if cost_line and price:
        rows.append(_row("cost_line", cost_line["name"], G_CYC, "Glassnode", "链上",
                         f"{cost_line['price']:,.2f}", score_cost_line(price, cost_line["price"]),
                         cost_line["data_time"], stale=cost_line["stale"],
                         note_extra="" if cs else "(该币无STH成本线,用全体成本代替)",
                         series=_ser(cs or ca)))
    else:
        if ca or cs:
            reason = "缺少现价"
        elif rcs and rcs.startswith("Glassnode 不支持"):
            reason = f"无STH成本线;全体成本:{rca}"  # 回退指标的状态更有用
        else:
            reason = rcs
        rows.append(_na("cost_line", "STH成本线", G_CYC, "Glassnode", "链上", reason))

    mz, rm, tm, smz = gn("mvrv_z")
    if mz:
        rows.append(_row("mvrv_z", "MVRV Z-Score", G_CYC, "Glassnode", "链上",
                         f"{mz[-1]['v']:.2f}", score_mvrv_z(mz[-1]["v"]), tm, stale=smz, series=_ser(mz)))
    else:
        rows.append(_na("mvrv_z", "MVRV Z-Score", G_CYC, "Glassnode", "链上", rm))

    nu, rnu, tnu, snu = gn("nupl")
    if nu:
        rows.append(_row("nupl", "NUPL", G_CYC, "Glassnode", "链上",
                         f"{nu[-1]['v']:.3f}", score_nupl(nu[-1]["v"]), tnu, stale=snu, series=_ser(nu)))
    else:
        rows.append(_na("nupl", "NUPL", G_CYC, "Glassnode", "链上", rnu))

    so, rso, tso, sso = gn("sopr")
    if so and len(so) >= 8:
        v = [x["v"] for x in so]
        ma7, prev = sum(v[-7:]) / 7, sum(v[-8:-1]) / 7
        rows.append(_row("sopr", "SOPR(7日均)", G_CYC, "Glassnode", "链上",
                         f"{ma7:.4f}", score_sopr(ma7, prev), tso, stale=sso, series=_ser(so)))
    else:
        rows.append(_na("sopr", "SOPR(7日均)", G_CYC, "Glassnode", "链上", rso))

    return {
        "code": 0, "inst": inst, "asset": asset,
        "fetched_at": int(now * 1000),
        "okx_at": int(okx["at"] * 1000),
        "gn_pending": _gn.pending(asset),
        "score": aggregate(rows),
        "rows": rows,
        "cost_line": cost_line,
        "levels": [{"min": th, "name": n} for th, n in LEVELS],
    }


def aggregate(rows):
    """对做多/做空支持度(0-100,越高越好)。过期数据权重减半。"""
    num = den = 0.0
    used = 0
    for r in rows:
        if r.get("status") != "ok" or not r.get("weight"):
            continue
        w = r["weight"] * (0.5 if r.get("stale") else 1)
        num += w * r["bull"]
        den += w
        used += 1
    # 该币/套餐永久不支持的指标不计入分母(山寨币没有链上数据不应拖低覆盖率)
    total = sum(1 for r in rows if r.get("weight")
                and not (r.get("status") == "na" and "不支持" in (r.get("reason") or "")))
    if not den:
        return {"long": None, "short": None, "coverage": f"0/{total}"}
    long_s = round(num / den * 100)
    short_s = 100 - long_s
    return {"long": long_s, "short": short_s,
            "long_level": level_name(long_s), "short_level": level_name(short_s),
            "coverage": f"{used}/{total}"}


# ---------------------------------------------------------------- 资金副图(第2步)
# 按K线周期提供 OKX 持仓量快照与资金费率结算历史,前端按时间 as-of 对齐到每根K线。
# 口径:OKX 单所。持仓用币本位 oiCcy,避免价格涨跌把 USD 持仓"虚增/虚减"。

# K线周期 → OKX rubik 持仓周期(1m 无对应,用 5m;1W 用 1D)
OI_PERIOD = {"1m": "5m", "5m": "5m", "15m": "15m", "1H": "1H", "4H": "4H",
             "1D": "1D", "1W": "1D"}
OI_MAX_PAGES = 13          # 每页 100 点,覆盖 1200 根K线
FUND_MAX_PAGES = 4         # OKX 资金费率历史只保留约 3 个月(实测约 290 次结算)
_hist = {}                 # key -> {"pts": {ts: v}, "at": 上次刷新首页, "exhausted": bool}
_hist_locks = {}
_hist_guard = threading.Lock()


def _hist_lock(key):
    with _hist_guard:
        return _hist_locks.setdefault(key, threading.Lock())


def _okx_hist(key, fetch_page, since_ms, max_pages):
    """通用历史缓存:首页每 OKX_TTL 刷新一次;不够早则向前分页回补,已到底则不再请求。
    fetch_page(cursor) → [(ts, v), ...] 倒序,cursor=None 为最新一页。"""
    with _hist_lock(key):
        h = _hist.setdefault(key, {"pts": {}, "at": 0, "exhausted": False})
        sess = requests.Session()
        now = time.time()
        if now - h["at"] >= OKX_TTL:
            for ts, v in fetch_page(sess, None):
                h["pts"][ts] = v
            h["at"] = now
        pages = 0
        while (not h["exhausted"] and h["pts"] and min(h["pts"]) > since_ms
               and pages < max_pages):
            rows = fetch_page(sess, min(h["pts"]))
            time.sleep(0.45)  # rubik 限速 5次/2s
            pages += 1
            fresh = [(t, v) for t, v in rows if t not in h["pts"]]
            if not fresh:
                h["exhausted"] = True
                break
            for ts, v in fresh:
                h["pts"][ts] = v
        return sorted(h["pts"].items()), h["at"]


def fundflow(inst: str, bar: str, since_ms: int):
    period = OI_PERIOD.get(bar, "1H")

    def oi_page(sess, cursor):
        q = {"instId": inst, "period": period, "limit": "100"}
        if cursor:
            q["end"] = str(cursor)
        return [(int(r[0]), float(r[2])) for r in _okx_get(sess, "rubik/stat/contracts/open-interest-history", q)]

    def fund_page(sess, cursor):
        q = {"instId": inst, "limit": "100"}
        if cursor:
            q["after"] = str(cursor)
        return [(int(r["fundingTime"]), float(r["realizedRate"] or r["fundingRate"]))
                for r in _okx_get(sess, "public/funding-rate-history", q)]

    out = {"code": 0, "inst": inst, "bar": bar, "oi_period": period, "scope": "OKX单所",
           "oi": [], "funding": [], "funding_now": None, "errors": {}}
    try:
        pts, at = _okx_hist(("oi", inst, period), oi_page, since_ms, OI_MAX_PAGES)
        out["oi"] = [[t, v] for t, v in pts if t >= since_ms - 86400000]
        out["oi_at"] = int(at * 1000)
    except (requests.RequestException, RuntimeError, ValueError) as e:
        out["errors"]["oi"] = f"OKX 持仓历史获取失败:{e}"
    try:
        pts, at = _okx_hist(("fund", inst), fund_page, since_ms, FUND_MAX_PAGES)
        out["funding"] = [[t, v] for t, v in pts if t >= since_ms - 86400000]
    except (requests.RequestException, RuntimeError, ValueError) as e:
        out["errors"]["funding"] = f"OKX 资金费率历史获取失败:{e}"
    snap = _okx_snapshot(inst)  # 当前(下一次结算)费率,复用面板缓存
    f = snap["data"].get("funding")
    if f:
        out["funding_now"] = {"rate": float(f[0]["fundingRate"]),
                              "settle_at": int(f[0]["fundingTime"])}
    return out


# ---------------------------------------------------------------- 决策过滤(第3步)
# 只降不升:≥40 不变(≥60 顺风也不加仓)、20-39 降一档、<20 降两档;降到 30% 以下即观望。
POSITION_TIERS = [100, 70, 50, 30]
MIN_COVERAGE = 0.5        # 有效指标不足一半时不调整(分数波动大、不可信)


def adjust_position(position: int, side_score, coverage_ratio: float):
    """纯函数,线上决策与回测共用。返回 {position, steps, level, note}。"""
    if side_score is None or coverage_ratio < MIN_COVERAGE:
        return {"position": position, "steps": 0, "level": None,
                "note": "链上有效指标不足一半,未调整仓位"}
    level = level_name(side_score)
    steps = 0 if side_score >= 40 else (1 if side_score >= 20 else 2)
    if steps == 0:
        note = f"链上{level}({side_score}),按缠论仓位执行" + (",不加仓" if side_score >= 60 else "")
        return {"position": position, "steps": 0, "level": level, "note": note}
    idx = POSITION_TIERS.index(position) if position in POSITION_TIERS else len(POSITION_TIERS) - 1
    new_idx = idx + steps
    new_pos = POSITION_TIERS[new_idx] if new_idx < len(POSITION_TIERS) else 0
    word = "降一档" if steps == 1 else "降两档"
    note = (f"链上{level}({side_score}),仓位{word}:{position}% → {new_pos}%" if new_pos
            else f"链上{level}({side_score}),仓位{word}后为 0 → 暂停该方向新开仓")
    return {"position": new_pos, "steps": steps, "level": level, "note": note}


def _coverage_ratio(score):
    try:
        used, total = (int(x) for x in str(score.get("coverage", "0/0")).split("/"))
        return used / total if total else 0.0
    except ValueError:
        return 0.0


def apply_to_decision(dec: dict, inst: str, last_price: Optional[float] = None) -> dict:
    """把链上支持度作用到缠论决策上。任何异常都原样返回决策(附说明),不影响主流程。"""
    dec = dict(dec)
    try:
        oc = build(inst, last_price)
    except Exception as e:  # 链上失败绝不阻断决策
        dec["onchain"] = {"applied": False, "note": f"链上数据不可用({type(e).__name__}),未调整"}
        return dec
    score = oc["score"]
    cov = _coverage_ratio(score)
    info = {"applied": False, "long": score.get("long"), "short": score.get("short"),
            "coverage": score.get("coverage"), "gn_pending": oc.get("gn_pending")}
    side = dec.get("action")
    if side not in ("long", "short"):
        info["note"] = "缠论观望,链上仅作环境参考"
        dec["onchain"] = info
        return dec
    adj = adjust_position(dec["position"], score.get(side), cov)
    info.update({"applied": True, "side": side, "side_score": score.get(side),
                 "level": adj["level"], "steps": adj["steps"],
                 "position_before": dec["position"], "position_after": adj["position"],
                 "note": adj["note"]})
    dec["onchain"] = info
    if adj["steps"] == 0:
        return dec
    if adj["position"] == 0:
        sig = dec.get("signal") or {}
        reason = (f"信号 {sig.get('type', '')}@{sig.get('price', '')} 有效(质量分 {dec.get('q')},"
                  f"盈亏比 1:{dec.get('rr')}),但{adj['note']}。"
                  f"链上只降不升,环境好转或信号更强时再评估。")
        keep = {k: dec[k] for k in ("dir_level", "dir", "dir_desc", "rules", "q", "signal",
                                    "stop", "stop_name", "targets", "rr", "entry") if k in dec}
        return {**keep, "action": "wait", "reason": reason, "onchain": info}
    dec["position"] = adj["position"]
    dec["warnings"] = list(dec.get("warnings") or []) + [adj["note"]]
    return dec
