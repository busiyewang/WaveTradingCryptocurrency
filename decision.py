"""综合交易决策:手册"三级共振决策台"的简化实现。

思路(手册第 3、7-9 章 + 工具3):
- 大级别定方向:上一级别的结构状态(现价相对中枢位置 + 最新笔方向)给出方向与强度;
- 本级别定买点:最近的一二三类买卖点是否仍"活跃"(位于最近两个笔端点范围内);
- 指标确认:KDJ 金叉死叉、RSI 背离、量价组合作为确认票;
- 冲突规则:信号方向与大级别方向相反 → 禁止操作(观望);
- 质量分 q = 方向强度×0.4 + 信号质量×0.4 + 确认度×0.2,映射仓位:
  q≥0.85→100%、≥0.7→70%、≥0.5→50%、≥0.35→30%、否则观望;
- 止损:结构止损(信号点下方 0.2%)与中枢止损(ZD 下方 0.2%)取更保守(离入场更近)者,
  另提示时间止损(12 根本级别K线未启动离场);
- 目标:第一目标=中枢 ZG/ZD,第二目标=近期高低点;盈亏比 <1:2 给出警告。
"""

# 方向级别映射:大级别定方向(参考手册级别链 1W→1D→4H→1H→15M→5M→1M)
LEVEL_UP = {"1m": "15m", "5m": "1H", "15m": "1H", "1H": "4H",
            "4H": "1D", "1D": "1W", "1W": "1W"}

STOP_BUFFER = 0.002  # 结构/中枢止损缓冲 0.2%


def _direction(summary):
    """由上一级别结构给出方向与强度。"""
    bi = summary.get("last_bi")
    zs = summary.get("last_zs")
    pos = summary.get("price_pos")
    if not bi or not zs:
        return {"dir": "neutral", "strength": 0.3, "desc": "结构数据不足,视为震荡"}
    up = bi["dir"] == "up"
    if pos == "above_zg":
        if up:
            return {"dir": "long", "strength": 1.0, "desc": "现价在中枢上方且笔向上,上涨趋势"}
        return {"dir": "long", "strength": 0.6, "desc": "现价在中枢上方,笔回调中(多头趋势内回调)"}
    if pos == "below_zd":
        if not up:
            return {"dir": "short", "strength": 1.0, "desc": "现价在中枢下方且笔向下,下跌趋势"}
        return {"dir": "short", "strength": 0.6, "desc": "现价在中枢下方,笔反弹中(空头趋势内反弹)"}
    return {"dir": "neutral", "strength": 0.3, "desc": "现价在中枢内部,震荡"}


def _signal_quality(p, beichi):
    """买卖点信号质量 0-1:直接采用买卖点评分(0-100,越高越可靠)。"""
    if p.get("score") is not None:
        return round(min(p["score"], 100) / 100, 2)
    # 兼容无评分的旧数据:按手册可靠度 3类≈2类>1类
    t = p["type"][1]
    if t == "3":
        return 0.9
    if t == "2":
        return 0.8
    for bc in reversed(beichi):
        if bc["k_idx"] == p["k_idx"]:
            return 0.65 if bc["kind"] == "trend" else 0.5
    return 0.5


def scalp(d1h, d5m, d1m):
    """超短线三级共振(手册场景二 + 工具3):
    1H 定方向(权重0.4)/ 5M 找二三类买卖点(0.4)/ 1M 背驰+分型+破前笔端点扳机(0.2)。
    5M 与 1H 冲突 → 禁止;q≥0.85→100%仓、≥0.7→70%、≥0.5→50%、≥0.35→30%。"""
    hd = _direction((d1h["chan"] or {}).get("summary") or {})
    ch5, ch1 = d5m["chan"], d1m["chan"]
    live = d5m["candles"][-1]["c"]
    rnd = lambda v: round(v, 6 if live < 10 else 2)
    base = {
        "dir_desc": hd["desc"], "dir": hd["dir"], "dir_strength": hd["strength"],
        "live": rnd(live),
        "notes": "手册:只做与1H同向的5M二/三类买点;杠杆3-10×逐仓;单笔风险0.5-1%;"
                 "止损距离须小于强平距离÷3;资金费率结算与重大数据发布时段避免开新仓;"
                 "日亏3%/周亏6%立即停手。",
    }

    fin5 = [b for b in ch5["bi"] if not b.get("unfinished")]
    sig = None
    if ch5["bsp"] and len(fin5) >= 2 and ch5["bsp"][-1]["k_idx"] >= fin5[-2]["end_idx"]:
        sig = ch5["bsp"][-1]
    if not sig:
        return {**base, "action": "wait", "q": 0, "sig5m": None, "trig1m": None,
                "reason": "5M 级别当前没有活跃买卖点信号,等待 5M 出现二/三类买卖点。"}

    side = "long" if sig["type"][0] == "B" else "short"
    if (side == "long" and hd["dir"] == "short") or \
       (side == "short" and hd["dir"] == "long"):
        return {**base, "action": "wait", "q": 0, "sig5m": sig, "trig1m": None,
                "reason": f"5M 信号 {sig['type']} 与 1H 方向冲突({hd['desc']}),"
                          f"手册规定:冲突禁止操作。"}
    q5 = _signal_quality(sig, ch5["beichi"])

    # 1M 扳机:同向背驰(分型已确认)+ 破前笔端点
    trig, trig_desc = 0.0, "1M 暂无同向活跃背驰,等待 1M 背驰+分型确认"
    fin1 = [b for b in ch1["bi"] if not b.get("unfinished")]
    if ch1["beichi"] and len(fin1) >= 2:
        bc = ch1["beichi"][-1]
        bc_side = "long" if bc["dir"] == "down" else "short"
        if bc["k_idx"] >= fin1[-2]["end_idx"] and bc_side == side:
            ref = fin1[-1]["start_price"]
            broke = live > ref if side == "long" else live < ref
            word = "底" if side == "long" else "顶"
            if broke:
                trig = 1.0
                trig_desc = (f"1M {word}背驰(面积比{bc['area_ratio']})+分型确认,"
                             f"且已破前笔端点 {rnd(ref)} → 扳机触发")
            else:
                trig = 0.6
                trig_desc = (f"1M {word}背驰(面积比{bc['area_ratio']})+分型确认,"
                             f"待破前笔端点 {rnd(ref)} 后入场")

    q = round(hd["strength"] * 0.4 + q5 * 0.4 + trig * 0.2, 3)
    pos_pct = 100 if q >= 0.85 else 70 if q >= 0.7 else 50 if q >= 0.5 else \
        30 if q >= 0.35 else 0
    action = side if pos_pct else "wait"

    # 止损三选一,取离入场最近(最保守)
    if side == "long":
        cands = [("结构止损(5M信号低点-0.2%)", sig["price"] * (1 - STOP_BUFFER))]
    else:
        cands = [("结构止损(5M信号高点+0.2%)", sig["price"] * (1 + STOP_BUFFER))]
    zs5 = (ch5.get("summary") or {}).get("last_zs")
    if zs5:
        if side == "long" and zs5["zd"] < live:
            cands.append(("中枢止损(5M ZD-0.2%)", zs5["zd"] * (1 - STOP_BUFFER)))
        if side == "short" and zs5["zg"] > live:
            cands.append(("中枢止损(5M ZG+0.2%)", zs5["zg"] * (1 + STOP_BUFFER)))
    if side == "long":
        valid = [c for c in cands if c[1] < live] or cands
        stop_name, stop = max(valid, key=lambda c: c[1])
    else:
        valid = [c for c in cands if c[1] > live] or cands
        stop_name, stop = min(valid, key=lambda c: c[1])

    # 分级止盈 40% / 30% / 30%(追踪)
    targets = []
    if zs5:
        edge = zs5["zg"] if side == "long" else zs5["zd"]
        if (side == "long" and edge > live) or (side == "short" and edge < live):
            targets.append(("5M中枢边界(了结40%)", rnd(edge)))
    zs1h = (d1h["chan"].get("summary") or {}).get("last_zs")
    if zs1h:
        edge = zs1h["zg"] if side == "long" else zs1h["zd"]
        if (side == "long" and edge > live) or (side == "short" and edge < live):
            targets.append(("1H中枢边界(再了结30%)", rnd(edge)))
    if not targets:
        ext = max(c["h"] for c in d5m["candles"][-200:]) if side == "long" \
            else min(c["l"] for c in d5m["candles"][-200:])
        targets.append(("5M近期极值(了结40%)", rnd(ext)))

    return {**base, "action": action, "q": q, "position": pos_pct,
            "sig5m": sig, "sig5m_quality": q5,
            "trig1m": trig_desc, "trig_score": trig,
            "entry": rnd(live), "stop": rnd(stop), "stop_name": stop_name,
            "time_stop": "时间止损:入场后 12 根 5M K线(1小时)未按预期启动即离场",
            "targets": targets,
            "trailing": "剩余 30% 移动止盈:多单跌破 1M 前笔低点 / 空单升破 1M 前笔高点离场",
            "reason": "" if pos_pct else f"综合质量分 {q} 不足 0.35,观望。"}


def decide(cur, hi, bar, hi_bar):
    ch = cur["chan"]
    sg = cur.get("signals") or {}
    candles = cur["candles"]
    live = candles[-1]["c"]
    fin = [b for b in ch["bi"] if not b.get("unfinished")]
    hd = _direction((hi["chan"] or {}).get("summary") or {})

    base = {
        "dir_level": hi_bar, "dir": hd["dir"], "dir_desc": hd["desc"],
        "rules": "手册硬约束:单笔风险0.5-1% · 杠杆3-10×逐仓 · 盈亏比≥1:2 · "
                 "同时持仓≤2 · 日亏3%/周亏6%停手",
    }

    # 活跃信号:最近一个买卖点须位于最近两个笔端点范围内
    active = None
    if ch["bsp"] and len(fin) >= 2:
        last = ch["bsp"][-1]
        if last["k_idx"] >= fin[-2]["end_idx"]:
            active = last
    if not active:
        note = ""
        if ch["bsp"]:
            p = ch["bsp"][-1]
            note = f"最近信号 {p['type']}@{p['price']} 已过期(其后结构已走出新笔)。"
        return {**base, "action": "wait", "q": 0,
                "reason": "本级别当前没有活跃的买卖点信号。" + note +
                          "等待新的背驰/买卖点出现再决策。"}

    side = "long" if active["type"][0] == "B" else "short"

    # 冲突:信号方向与大级别方向相反 → 手册规定禁止操作
    if (side == "long" and hd["dir"] == "short") or \
       (side == "short" and hd["dir"] == "long"):
        return {**base, "action": "wait", "q": 0, "signal": active,
                "reason": f"活跃信号 {active['type']} 为{'买' if side=='long' else '卖'}点,"
                          f"但{hi_bar}大级别方向相反({hd['desc']})。"
                          f"手册规定:信号与大级别冲突,禁止操作。"}

    # 指标确认票(3 票制)
    confirms, hits = [], 0
    kdj_states = (sg.get("kdj") or {}).get("states") or []
    cross_ok = any(("金叉" if side == "long" else "死叉") in s for s in kdj_states)
    if cross_ok:
        confirms.append("KDJ " + ("金叉" if side == "long" else "死叉"))
        hits += 1
    div = (sg.get("rsi") or {}).get("divergence")
    if div and div.get("side") == ("buy" if side == "long" else "sell"):
        confirms.append(f"RSI {div['type']}")
        hits += 1
    combo = (sg.get("volume") or {}).get("combo") or ""
    good_combo = {"long": ("价跌量缩", "价涨量增"), "short": ("价涨量缩", "价跌量增")}[side]
    if combo in good_combo:
        confirms.append(f"量价配合({combo})")
        hits += 1
    if not confirms:
        confirms.append("暂无指标确认")

    sq = _signal_quality(active, ch["beichi"])
    q = round(hd["strength"] * 0.4 + sq * 0.4 + (hits / 3) * 0.2, 3)
    if q >= 0.85:
        pos_pct = 100
    elif q >= 0.7:
        pos_pct = 70
    elif q >= 0.5:
        pos_pct = 50
    elif q >= 0.35:
        pos_pct = 30
    else:
        return {**base, "action": "wait", "q": q, "signal": active,
                "reason": f"信号 {active['type']} 与大级别不冲突,但综合质量分 {q} 过低"
                          f"(方向强度 {hd['strength']}、信号质量 {sq}、确认 {hits}/3),"
                          f"手册标准:q<0.35 观望。"}

    # 止损:结构止损 vs 中枢止损,取离入场更近(更保守)者
    zs = (ch.get("summary") or {}).get("last_zs")
    entry = live
    stops = []
    if side == "long":
        stops.append(("结构止损(信号低点下0.2%)", active["price"] * (1 - STOP_BUFFER)))
        if zs and zs["zd"] < entry:
            stops.append(("中枢止损(ZD下0.2%)", zs["zd"] * (1 - STOP_BUFFER)))
        stops = [s for s in stops if s[1] < entry]
        stop_name, stop = max(stops, key=lambda s: s[1]) if stops else ("结构止损", active["price"] * (1 - STOP_BUFFER))
        targets = []
        if zs and zs["zg"] > entry:
            targets.append(("中枢上沿 ZG", zs["zg"]))
        recent_hi = max(c["h"] for c in candles[-120:])
        if recent_hi > entry and (not targets or recent_hi > targets[0][1]):
            targets.append(("近期高点", recent_hi))
        risk = entry - stop
    else:
        stops.append(("结构止损(信号高点上0.2%)", active["price"] * (1 + STOP_BUFFER)))
        if zs and zs["zg"] > entry:
            stops.append(("中枢止损(ZG上0.2%)", zs["zg"] * (1 + STOP_BUFFER)))
        stops = [s for s in stops if s[1] > entry]
        stop_name, stop = min(stops, key=lambda s: s[1]) if stops else ("结构止损", active["price"] * (1 + STOP_BUFFER))
        targets = []
        if zs and zs["zd"] < entry:
            targets.append(("中枢下沿 ZD", zs["zd"]))
        recent_lo = min(c["l"] for c in candles[-120:])
        if recent_lo < entry and (not targets or recent_lo < targets[0][1]):
            targets.append(("近期低点", recent_lo))
        risk = stop - entry

    rnd = lambda v: round(v, 6 if entry < 10 else 2)

    rr = None
    warnings = []
    if targets and risk > 0:
        reward = abs(targets[0][1] - entry)
        rr = round(reward / risk, 2)
        if rr < 2:
            # 盈亏比≥1:2 是手册硬约束:不满足直接降级为观望,
            # 并给出满足 1:2 的入场价(entry* 满足 |目标-e|=2|e-止损| → e=(目标+2×止损)/3)
            better = (targets[0][1] + 2 * stop) / 3
            move = "回调" if side == "long" else "反弹"
            return {**base, "action": "wait", "q": q, "signal": active,
                    "stop": rnd(stop), "stop_name": stop_name,
                    "targets": [(n, rnd(v)) for n, v in targets], "rr": rr,
                    "better_entry": rnd(better),
                    "reason": f"信号 {active['type']}@{rnd(active['price'])} 有效(质量分 {q}),"
                              f"但现价 {rnd(entry)} 追单到第一目标的盈亏比仅 1:{rr},"
                              f"低于手册硬约束 1:2 → 观望,等{move}至 {rnd(better)} 附近再考虑"
                              f"(止损 {rnd(stop)},目标 {rnd(targets[0][1])})。"}
    if not targets:
        warnings.append("上方/下方无明确结构目标,谨慎追单。")
    return {
        **base,
        "action": side, "q": q, "position": pos_pct,
        "signal": active, "signal_quality": sq,
        "confirms": confirms, "confirm_hits": hits,
        "entry": rnd(entry), "stop": rnd(stop), "stop_name": stop_name,
        "time_stop": f"入场后 12 根 {bar} K线未按预期启动即离场(时间止损)",
        "targets": [(n, rnd(v)) for n, v in targets],
        "rr": rr, "warnings": warnings,
        "sizing": "仓位公式:R=账户权益×单笔风险%(0.5-1%),数量Q=R÷|入场-止损|,"
                  "保证金M=Q×入场价÷杠杆",
    }
