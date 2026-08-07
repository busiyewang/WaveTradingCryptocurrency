"""形态识别:双顶(M)/双底(W)+ 手册第 10 章七项检验与缠论对应。

七项检验(手册):①两顶/底时间间隔 10-20 根;②价差容忍 BTC±3%/其他±5%;
③中间回撤 ≥ 前段行情的 30%;④第二顶/底必须背驰(MACD 同向柱面积缩小);
⑤突破颈线量 ≥ MA(20)×1.5;⑥收盘价确认突破;⑦回踩不破颈线。

缠论对应(手册):双顶=第二次上攻中枢上沿失败+盘整背驰 → 对应二/三类卖点;
双底=第二底不创新低(或创新低背驰)→ 对应二类买点/一类买点,颈线突破回踩=三类买点。
"""

from chan import _seg_area
from indicators import macd_hist_list
import pandas as pd


def detect_double(bis, candles, inst):
    """检测双顶/双底。bis 为缠论笔(需含 unfinished 标记),candles 为已收盘K线升序。
    返回按时间排序的形态列表(最多保留最近 3 个)。"""
    fin = [b for b in bis if not b.get("unfinished")]
    if len(fin) < 3 or len(candles) < 30:
        return []
    tol = 0.03 if inst.upper().startswith("BTC") else 0.05
    hist = macd_hist_list(candles)
    vol = pd.Series([c["vol"] for c in candles], dtype=float)
    vol_ma20 = vol.rolling(20, min_periods=1).mean()

    out = []
    for i in range(len(fin) - 2):
        b1, b2, b3 = fin[i], fin[i + 1], fin[i + 2]
        if b1["dir"] == "up" and b3["dir"] == "up":
            out += _check_one(b1, b2, b3, "double_top", tol, hist, candles, vol, vol_ma20)
        elif b1["dir"] == "down" and b3["dir"] == "down":
            out += _check_one(b1, b2, b3, "double_bottom", tol, hist, candles, vol, vol_ma20)
    out.sort(key=lambda p: p["p2_idx"])
    return out[-3:]


def detect_triangle(bis, candles):
    """收敛三角形:最近两个顶依次降低 + 最近两个底依次抬高,幅度收敛。
    缠论对应(手册):三角形 = 中枢震荡收敛(次级别走势重叠、幅度递减),
    突破方向即离开中枢方向;向上突破后回踩上轨/ZG 不破 ≈ 三类买点,向下对称。
    返回 [] 或 [一个形态 dict](只报最近的一个)。"""
    fin = [b for b in bis if not b.get("unfinished")]
    if len(fin) < 4 or len(candles) < 30:
        return []
    tops = [(b["end_idx"], b["end_price"], b["end_ts"]) for b in fin if b["dir"] == "up"]
    bots = [(b["end_idx"], b["end_price"], b["end_ts"]) for b in fin if b["dir"] == "down"]
    if len(tops) < 2 or len(bots) < 2:
        return []
    t1, t2 = tops[-2], tops[-1]
    b1, b2 = bots[-2], bots[-1]
    # 顶降低、底抬高、仍有重叠、幅度收敛
    if not (t2[1] < t1[1] and b2[1] > b1[1] and t2[1] > b2[1]):
        return []
    if not (t2[1] - b2[1]) < (t1[1] - b1[1]) * 0.9:
        return []

    def line(pa, pb):
        k = (pb[1] - pa[1]) / (pb[0] - pa[0]) if pb[0] != pa[0] else 0.0
        return lambda i: pa[1] + k * (i - pa[0])

    upper, lower = line(t1, t2), line(b1, b2)
    vol = pd.Series([c["vol"] for c in candles], dtype=float)
    vol_ma20 = vol.rolling(20, min_periods=1).mean()

    chk_from = max(t2[0], b2[0]) + 1
    brk_dir, brk_idx = None, None
    for i in range(chk_from, len(candles)):
        if upper(i) <= lower(i):
            break  # 已过顶点仍未突破,形态失效区
        c = candles[i]["c"]
        if c > upper(i):
            brk_dir, brk_idx = "up", i
            break
        if c < lower(i):
            brk_dir, brk_idx = "down", i
            break

    last = len(candles) - 1
    end_i = brk_idx if brk_idx is not None else last
    if brk_dir is None:
        status = "收敛中(突破方向未定)"
        vol_ok = None
        note = ("缠论对应:中枢震荡收敛,幅度递减,变盘临近。"
                f"上破 {round(upper(last), 8)} 看多(回踩不破≈三买),"
                f"下破 {round(lower(last), 8)} 看空(回抽不上≈三卖);等突破方向表态。")
    else:
        vol_ok = float(vol.iloc[brk_idx]) >= 1.5 * float(vol_ma20.iloc[brk_idx])
        arrow = "向上突破" if brk_dir == "up" else "向下突破"
        status = f"已{arrow}" + ("(放量确认)" if vol_ok else "(量能不足,警惕假突破)")
        note = ("缠论对应:突破=离开中枢方向。" +
                ("回踩上轨/中枢上沿不破 ≈ 三类买点。" if brk_dir == "up"
                 else "回抽下轨/中枢下沿不上 ≈ 三类卖点。") +
                ("" if vol_ok else "手册要求突破量≥MA20×1.5,当前未达标。"))

    return [{
        "type": "triangle", "status": status, "break_dir": brk_dir,
        "vol_ok": vol_ok,
        "upper": [[t1[2], t1[1]], [candles[end_i]["ts"], round(upper(end_i), 8)]],
        "lower": [[b1[2], b1[1]], [candles[end_i]["ts"], round(lower(end_i), 8)]],
        "upper_now": round(upper(last), 8), "lower_now": round(lower(last), 8),
        "p2_ts": candles[end_i]["ts"],
        "note": note,
    }]


def _check_one(b1, b2, b3, kind, tol, hist, candles, vol, vol_ma20):
    top = kind == "double_top"
    p1, p2 = b1["end_price"], b3["end_price"]      # 两顶/两底(笔端点极值)
    neck = b2["end_price"]                          # 中间回撤极值 = 颈线
    height = (max(p1, p2) - neck) if top else (neck - min(p1, p2))
    if height <= 0:
        return []
    # 两顶/两底价差超出容忍(检验②)直接不构成形态,避免噪音
    price_diff = abs(p2 - p1) / p1
    if price_diff > tol:
        return []

    checks = []
    gap = b3["end_idx"] - b1["end_idx"]
    checks.append({"name": "间隔10-20根", "ok": 10 <= gap <= 20, "val": f"{gap}根"})
    checks.append({"name": f"价差≤{tol:.0%}", "ok": price_diff <= tol,
                   "val": f"{price_diff:.1%}"})
    swing = abs(p1 - b1["start_price"])
    retr = height / swing if swing > 0 else 0
    checks.append({"name": "中间回撤≥前段30%", "ok": retr >= 0.3, "val": f"{retr:.0%}"})
    d = "up" if top else "down"
    a1, a3 = _seg_area(hist, b1, d), _seg_area(hist, b3, d)
    bc_ok = a1 > 0 and a3 < a1
    checks.append({"name": "第二段背驰", "ok": bc_ok,
                   "val": f"面积比{a3 / a1:.2f}" if a1 > 0 else "-"})

    # 颈线突破检测(第二顶/底之后)
    brk = None
    for idx in range(b3["end_idx"] + 1, len(candles)):
        c = candles[idx]["c"]
        if (top and c < neck) or (not top and c > neck):
            brk = idx
            break
    if brk is None:
        status = "形成中(颈线未破)"
        checks.append({"name": "突破量≥MA20×1.5", "ok": False, "val": "未突破"})
        checks.append({"name": "收盘确认突破", "ok": False, "val": "未突破"})
        checks.append({"name": "回踩不破颈线", "ok": False, "val": "未突破"})
    else:
        status = "颈线已破(收盘确认)"
        v_ok = float(vol.iloc[brk]) >= 1.5 * float(vol_ma20.iloc[brk])
        checks.append({"name": "突破量≥MA20×1.5", "ok": v_ok,
                       "val": f"量比{float(vol.iloc[brk]) / max(float(vol_ma20.iloc[brk]), 1e-9):.2f}"})
        checks.append({"name": "收盘确认突破", "ok": True, "val": "已确认"})
        after = candles[brk + 1:]
        if after:
            back = any((c["c"] > neck) if top else (c["c"] < neck) for c in after)
            checks.append({"name": "回踩不破颈线", "ok": not back,
                           "val": "守住" if not back else "已收回形态内(失败风险)"})
        else:
            checks.append({"name": "回踩不破颈线", "ok": True, "val": "待观察"})

    passed = sum(1 for c in checks if c["ok"])
    if top:
        chan_note = ("缠论对应:第二顶上攻中枢上沿失败" +
                     ("且背驰" if bc_ok else "(未见背驰,谨慎)") +
                     " → 二类卖点;颈线破位后回抽不过颈线 → 三类卖点。")
    else:
        chan_note = ("缠论对应:第二底" +
                     ("背驰 → 一/二类买点" if bc_ok else "不创新低 → 二类买点") +
                     ";突破颈线后回踩不破 → 三类买点。")
    return [{
        "type": kind, "status": status,
        "p1_idx": b1["end_idx"], "p1_ts": b1["end_ts"], "p1_price": p1,
        "p2_idx": b3["end_idx"], "p2_ts": b3["end_ts"], "p2_price": p2,
        "neck": neck, "neck_ts": b2["end_ts"],
        "target": round(neck - height, 8) if top else round(neck + height, 8),  # 量度跌/涨幅
        "checks": checks, "passed": passed, "total": len(checks),
        "chan_note": chan_note,
    }]
