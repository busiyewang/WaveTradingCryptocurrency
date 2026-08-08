"""形态识别:收敛三角形(手册第 10 章形态与缠论对应)。

突破量的检验沿用手册标准:突破 K 线量 ≥ MA(20)×1.5,且以收盘价确认。
"""

import pandas as pd


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
