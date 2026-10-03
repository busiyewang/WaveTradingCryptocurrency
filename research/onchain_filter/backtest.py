"""链上(OKX 资金面)过滤回测:主页缠论决策 有过滤 vs 无过滤。

做法
- 逐根重放主页决策:每根已收盘K线用最近 WINDOW 根重建本级别缠论 + 信号,上一级别
  (decision.LEVEL_UP)用当时已收盘的大级别K线重建,调用 decision.decide —— 与线上同一份代码。
- 支持度只用当时已可知的 OKX 历史:资金费率(最近一次已结算)、1H 持仓与价格的 24h 变化、
  1H 多空账户比在近 100 小时的分位、最近一小时主动买/卖比;rubik 1H 数据按区间结束后才可用。
  打分规则直接调用 onchain.score_* / aggregate / adjust_position,与线上一致。
  Glassnode 套餐只给近两周日线,无法回测,所以这里检验的是"OKX 部分"的过滤效果。
- 撮合(两组各自独立持仓,同一时间最多一笔):
  决策为做多/做空 → 下一根开盘入场;止损=决策止损,止盈=第一目标;
  同一根同时触及止损和目标按止损先成交;开盘跳空越过止损按开盘价成交;
  持仓满 TIME_STOP 根仍未出场按收盘价离场(决策里的时间止损)。
  单边成本 COST(手续费 0.05% + 滑点 0.02%);资金费未计入。
  每笔风险 = 权益 × 1% × 决策仓位%。过滤组按 adjust_position 降档,降到 0 则不开仓。

运行(项目根目录):
  .venv/bin/python research/onchain_filter/backtest.py              # 下载(有缓存)+回测
  .venv/bin/python research/onchain_filter/backtest.py --refresh    # 强制重新下载
结果写入 var/onchain_filter/results.json,终端打印汇总。
"""

import argparse
import json
import os
import sys
import time
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import requests  # noqa: E402

import chan  # noqa: E402
import decision  # noqa: E402
import indicators  # noqa: E402
import onchain  # noqa: E402

OKX = "https://www.okx.com/api/v5/"
OUT_DIR = os.path.join(ROOT, "var", "onchain_filter")
INSTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP",
         "DOGE-USDT-SWAP", "BNB-USDT-SWAP"]
BARS = ["15m", "1H", "4H"]
BAR_MS = {"15m": 9e5, "1H": 36e5, "4H": 144e5, "1D": 864e5}
WINDOW = 600          # 每步分析窗口(研究默认 600 根)
TIME_STOP = 12        # 时间止损根数
COST = 0.0007         # 单边成本:手续费 0.05% + 滑点 0.02%
RISK = 0.01           # 每笔风险占权益(再乘决策仓位%)
H = 3_600_000


# ---------------------------------------------------------------- 下载

def _get(sess, path, params):
    for attempt in range(4):
        try:
            body = sess.get(OKX + path, params=params, timeout=20).json()
        except (requests.RequestException, ValueError):
            time.sleep(1 + attempt)
            continue
        if body.get("code") == "0":
            return body["data"]
        time.sleep(1 + attempt)  # 限流等错误:退避重试
    raise RuntimeError(f"OKX 请求失败 {path} {params}")


def _candles(sess, inst, bar, since_ms):
    rows = []
    data = _get(sess, "market/candles", {"instId": inst, "bar": bar, "limit": "300"})
    rows.extend(data)
    while rows and int(rows[-1][0]) > since_ms:
        data = _get(sess, "market/history-candles",
                    {"instId": inst, "bar": bar, "after": rows[-1][0], "limit": "100"})
        if not data:
            break
        rows.extend(data)
        time.sleep(0.12)
    out = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
            "c": float(r[4]), "vol": float(r[5]), "confirm": int(r[8])} for r in rows]
    out = [c for c in out if c["confirm"] == 1]
    out.sort(key=lambda c: c["ts"])
    return out


def _rubik(sess, path, inst, pages=20):
    rows, end = [], None
    for _ in range(pages):
        q = {"instId": inst, "period": "1H", "limit": "100"}
        if end:
            q["end"] = end
        data = _get(sess, path, q)
        if not data:
            break
        rows.extend(data)
        end = data[-1][0]
        time.sleep(0.45)
    uniq = {int(r[0]): r for r in rows}
    return [uniq[t] for t in sorted(uniq)]


def _funding(sess, inst):
    rows, after = [], None
    for _ in range(6):
        q = {"instId": inst, "limit": "100"}
        if after:
            q["after"] = after
        data = _get(sess, "public/funding-rate-history", q)
        if not data:
            break
        rows.extend(data)
        after = data[-1]["fundingTime"]
        time.sleep(0.25)
    uniq = {int(r["fundingTime"]): float(r["realizedRate"] or r["fundingRate"]) for r in rows}
    return [[t, uniq[t]] for t in sorted(uniq)]


def download(inst, refresh=False):
    path = os.path.join(OUT_DIR, f"{inst}.json")
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    sess = requests.Session()
    oi = _rubik(sess, "rubik/stat/contracts/open-interest-history", inst)
    ls = _rubik(sess, "rubik/stat/contracts/long-short-account-ratio-contract", inst)
    tk = _rubik(sess, "rubik/stat/taker-volume-contract", inst)
    funding = _funding(sess, inst)
    # 回测起点:1H 持仓/多空比最早可得 + 100 小时(多空比分位需要)
    if not oi or not ls:
        raise RuntimeError(f"{inst} 缺少 OKX 1H 持仓/多空比历史")
    start = max(int(oi[0][0]), int(ls[0][0])) + 100 * H
    data = {"inst": inst, "downloaded_at": int(time.time() * 1000), "start": start,
            "oi": [[int(r[0]), float(r[2]), float(r[3])] for r in oi],      # ts, 币, USD
            "ls": [[int(r[0]), float(r[1])] for r in ls],
            "taker": [[int(r[0]), float(r[1]), float(r[2])] for r in tk],  # ts, 买, 卖
            "funding": funding, "candles": {}}
    for bar in sorted(set(BARS) | {decision.LEVEL_UP[b] for b in BARS}):
        warm = (WINDOW + 50) * BAR_MS[bar]
        data["candles"][bar] = _candles(sess, inst, bar, start - warm)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


# ---------------------------------------------------------------- 历史支持度(只用当时已可知数据)

class ScoreAt:
    def __init__(self, data):
        self.oi = data["oi"]
        self.oi_t = [r[0] for r in self.oi]
        # rubik 1H 区间数据按区间结束(ts+1h)才可用,避免前视
        self.ls = data["ls"]
        self.ls_t = [r[0] + H for r in self.ls]
        self.tk = data["taker"]
        self.tk_t = [r[0] + H for r in self.tk]
        self.fd = data["funding"]
        self.fd_t = [r[0] for r in self.fd]

    def rows(self, t):
        rows = []
        i = bisect_right(self.fd_t, t) - 1
        if i >= 0:
            rows.append(self._row("funding", onchain.score_funding(self.fd[i][1])))
        i = bisect_right(self.oi_t, t - H) - 1          # 快照在下一小时开始后才确认可用
        j = bisect_right(self.oi_t, t - 25 * H) - 1
        if i >= 0 and j >= 0 and t - H - self.oi_t[i] <= 2 * H:
            now, ago = self.oi[i], self.oi[j]
            px = lambda r: r[2] / r[1] if r[1] else 0  # noqa: E731
            if px(ago) and ago[1]:
                d_oi = (now[1] / ago[1] - 1) * 100
                d_px = (px(now) / px(ago) - 1) * 100
                rows.append(self._row("oi_price", onchain.score_oi_price(d_px, d_oi)))
        i = bisect_right(self.ls_t, t)
        if i >= 20:
            vals = [r[1] for r in self.ls[max(0, i - 100):i]]
            cur = vals[-1]
            pct = sum(1 for v in vals if v <= cur) / len(vals)
            rows.append(self._row("ls_ratio", onchain.score_ls_ratio(pct)))
        i = bisect_right(self.tk_t, t) - 1
        if i >= 0 and self.tk[i][2] > 0:
            rows.append(self._row("taker", onchain.score_taker(self.tk[i][1] / self.tk[i][2])))
        return rows

    @staticmethod
    def _row(key, bull_note):
        return {"key": key, "status": "ok", "bull": bull_note[0], "stale": False,
                "weight": onchain.WEIGHTS[key]}

    def score(self, t):
        rows = self.rows(t)
        agg = onchain.aggregate(rows)
        return agg, len(rows) / 4


# ---------------------------------------------------------------- 重放

def _payload(window, bar, live):
    analysis = chan.analyze(window, bar)
    s = analysis.get("summary") or {}
    s["last_price"] = live
    if s.get("last_zs"):
        z = s["last_zs"]
        s["price_pos"] = "above_zg" if live > z["zg"] else ("below_zd" if live < z["zd"] else "inside")
    return {"candles": window, "chan": analysis,
            "signals": indicators.analyze_signals(window, analysis.get("bi"))}


def _hi_payload(hi_analysis, live):
    """大级别分析按大K线收盘缓存,价格位置随当前小级别收盘价更新(与线上一致)。"""
    s = dict((hi_analysis["chan"].get("summary") or {}))
    if s.get("last_zs"):
        z = s["last_zs"]
        s["price_pos"] = "above_zg" if live > z["zg"] else ("below_zd" if live < z["zd"] else "inside")
    return {**hi_analysis, "chan": {**hi_analysis["chan"], "summary": s}}


def replay(job):
    inst, bar = job
    with open(os.path.join(OUT_DIR, f"{inst}.json"), encoding="utf-8") as f:
        data = json.load(f)
    hi_bar = decision.LEVEL_UP[bar]
    rows = data["candles"][bar]
    hi_rows = data["candles"][hi_bar]
    dur, hi_dur = BAR_MS[bar], BAR_MS[hi_bar]
    hi_close = [c["ts"] + hi_dur for c in hi_rows]
    scorer = ScoreAt(data)
    start = data["start"]

    signals = []          # 每次决策给出做多/做空的时刻(两组共用,再各自撮合)
    hi_cache = (-1, None)
    for i in range(WINDOW, len(rows) - 1):
        t = rows[i]["ts"] + dur          # 本根收盘时刻 = 决策时刻
        if t < start:
            continue
        n_hi = bisect_right(hi_close, t)
        if n_hi < 60:
            continue
        if hi_cache[0] != n_hi:
            hw = hi_rows[max(0, n_hi - WINDOW):n_hi]
            hi_cache = (n_hi, _payload(hw, hi_bar, hw[-1]["c"]))
        live = rows[i]["c"]
        cur = _payload(rows[i - WINDOW + 1:i + 1], bar, live)
        dec = decision.decide(cur, _hi_payload(hi_cache[1], live), bar, hi_bar)
        if dec.get("action") not in ("long", "short"):
            continue
        agg, cov = scorer.score(t)
        side = dec["action"]
        signals.append({"inst": inst, "bar": bar, "i": i, "t": t, "side": side, "position": dec["position"],
                        "stop": dec["stop"], "target": dec["targets"][0][1], "rr": dec["rr"],
                        "type": dec["signal"]["type"], "key": f"{dec['signal']['type']}@{dec['signal']['ts']}",
                        "score": agg.get(side), "coverage": cov})
    base = simulate(rows, signals, filtered=False)
    filt = simulate(rows, signals, filtered=True)
    return {"inst": inst, "bar": bar, "signals": len(signals), "base": base, "filtered": filt,
            "period": [start, rows[-1]["ts"] + dur]}


def simulate(rows, signals, filtered):
    by_i = {}
    for s in signals:
        by_i.setdefault(s["i"], s)
    equity, peak, max_dd = 1.0, 1.0, 0.0
    trades, skipped, used = [], {}, set()
    i, n = 0, len(rows)
    pos = None
    while i < n:
        if pos:
            c = rows[i]
            side, entry, stop, tgt = pos["side"], pos["entry"], pos["stop"], pos["target"]
            exit_px, why = None, None
            if side == "long":
                if c["o"] <= stop:
                    exit_px, why = c["o"], "gap_stop"
                elif c["l"] <= stop:
                    exit_px, why = stop, "stop"
                elif c["h"] >= tgt:
                    exit_px, why = max(tgt, c["o"]), "target"
            else:
                if c["o"] >= stop:
                    exit_px, why = c["o"], "gap_stop"
                elif c["h"] >= stop:
                    exit_px, why = stop, "stop"
                elif c["l"] <= tgt:
                    exit_px, why = min(tgt, c["o"]), "target"
            if exit_px is None and i - pos["entry_i"] + 1 >= TIME_STOP:
                exit_px, why = c["c"], "time"
            if exit_px is not None:
                sgn = 1 if side == "long" else -1
                risk = abs(entry - stop)
                r_net = (sgn * (exit_px - entry) - COST * (entry + exit_px)) / risk
                equity *= 1 + RISK * pos["position"] / 100 * r_net
                peak = max(peak, equity)
                max_dd = max(max_dd, 1 - equity / peak)
                trades.append({**pos["sig"], "r": round(r_net, 3), "why": why,
                               "position_used": pos["position"]})
                pos = None
            i += 1
            continue
        s = by_i.get(i - 1)  # 上一根收盘的决策,本根开盘入场
        # 同一信号只开一次仓;被过滤跳过的信号若之后支持度回升且仍活跃,可再入场(与线上一致)
        if s and s["key"] not in used:
            size = s["position"]
            if filtered:
                size = onchain.adjust_position(size, s["score"], s["coverage"])["position"]
            entry = rows[i]["o"]
            ok_side = (entry > s["stop"]) if s["side"] == "long" else (entry < s["stop"])
            if not ok_side:
                pass  # 开盘已越过止损:放弃
            elif size == 0:
                skipped.setdefault(s["key"], s)
            else:
                used.add(s["key"])
                pos = {"side": s["side"], "entry": entry, "stop": s["stop"], "target": s["target"],
                       "position": size, "entry_i": i, "sig": s}
                continue  # 本根开盘入场,同一根内即检查止损/目标
        i += 1
    never = [v for k, v in skipped.items() if k not in used]  # 最终也没入场的信号
    return {"trades": trades, "skipped": never, "equity": equity, "max_dd": max_dd}


# ---------------------------------------------------------------- 汇总

def stats(trades, equity=None, max_dd=None):
    n = len(trades)
    if not n:
        return {"n": 0}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r <= 0]
    out = {"n": n, "win_rate": round(len(wins) / n * 100, 1),
           "avg_r": round(sum(rs) / n, 3),
           "pf": round(sum(wins) / sum(losses), 2) if losses and sum(losses) else None}
    if equity is not None:
        out["return_pct"] = round((equity - 1) * 100, 2)
        out["max_dd_pct"] = round(max_dd * 100, 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    for inst in INSTS:
        t0 = time.time()
        download(inst, args.refresh)
        print(f"数据就绪 {inst} ({time.time() - t0:.0f}s)", flush=True)
    jobs = [(inst, bar) for inst in INSTS for bar in BARS]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(replay, jobs))

    report = {"generated_at": int(time.time() * 1000),
              "params": {"window": WINDOW, "time_stop": TIME_STOP, "cost_one_side": COST,
                         "risk": RISK, "tiers": onchain.POSITION_TIERS, "insts": INSTS, "bars": BARS},
              "runs": []}
    all_base, all_filt, all_skip, all_down = [], [], [], []
    for r in results:
        b, f = r["base"], r["filtered"]
        down = [t for t in f["trades"] if t["position_used"] < t["position"]]
        report["runs"].append({
            "inst": r["inst"], "bar": r["bar"], "period": r["period"], "signals": r["signals"],
            "base": stats(b["trades"], b["equity"], b["max_dd"]),
            "filtered": stats(f["trades"], f["equity"], f["max_dd"]),
            "skipped": {"n": len(f["skipped"])},
            "downgraded": stats(down),
        })
        all_base += b["trades"]
        all_filt += f["trades"]
        all_skip += f["skipped"]
        all_down += down
    # 被跳过的信号在无过滤组里的实际结果(判断过滤是否避开了亏损单)
    base_by_key = {(t["inst"], t["bar"], t["key"]): t for t in all_base}
    skipped_outcomes = [base_by_key[k] for k in ((s["inst"], s["bar"], s["key"]) for s in all_skip)
                        if k in base_by_key]
    buckets = {}
    for t in all_base:
        sc = t.get("score")
        k = "无分数" if sc is None else ("≥60" if sc >= 60 else "40-59" if sc >= 40 else "20-39" if sc >= 20 else "<20")
        buckets.setdefault(k, []).append(t)
    report["pooled"] = {
        "base": stats(all_base), "filtered": stats(all_filt),
        "skipped_in_base": stats(skipped_outcomes), "skipped_signals": len(all_skip),
        "downgraded_trades": stats(all_down),
        "by_score_bucket": {k: stats(v) for k, v in sorted(buckets.items())},
        # 等权合成:各组合收益率简单平均(组合间不复利、不共享资金)
        "avg_return_base": round(sum(x["base"].get("return_pct", 0) for x in report["runs"]) / len(report["runs"]), 2),
        "avg_return_filtered": round(sum(x["filtered"].get("return_pct", 0) for x in report["runs"]) / len(report["runs"]), 2),
        "avg_maxdd_base": round(sum(x["base"].get("max_dd_pct", 0) for x in report["runs"]) / len(report["runs"]), 2),
        "avg_maxdd_filtered": round(sum(x["filtered"].get("max_dd_pct", 0) for x in report["runs"]) / len(report["runs"]), 2),
    }
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "trades.json"), "w", encoding="utf-8") as f:
        json.dump({"base": all_base, "filtered": all_filt, "skipped": all_skip}, f, ensure_ascii=False)

    print("\n组合            信号  | 无过滤: 笔数 胜率 平均R 收益% 回撤% | 过滤: 笔数 胜率 平均R 收益% 回撤% | 跳过 降档")
    for x in report["runs"]:
        b, f = x["base"], x["filtered"]
        fmt = lambda s: (f"{s['n']:>3} {s.get('win_rate', 0):>5} {s.get('avg_r', 0):>6} "  # noqa: E731
                         f"{s.get('return_pct', 0):>6} {s.get('max_dd_pct', 0):>5}") if s["n"] else "  0     -      -      -     -"
        print(f"{x['inst'][:4]:<5}{x['bar']:<4} {x['signals']:>6}  | {fmt(b)} | {fmt(f)} | "
              f"{x['skipped']['n']:>3} {x['downgraded']['n']:>3}")
    print("\n汇总:", json.dumps(report["pooled"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
