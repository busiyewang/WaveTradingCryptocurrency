"""多级别联动分析(区间套):大级别定方向 + 关键位,小级别找确认。

口径(与手册及单级别决策一致,评分统一"越高越好" 0-100):
- 大级别关键位 = 结构位为主(中枢 ZG/ZD、最近前高/前低笔端点),EMA20/40 仅作辅助;
- "正在测试"判定 = 现价与关键位距离 ≤ 容差 tol(0.6 × 大级别近20根平均振幅,自适应);
- 小级别确认信号 = 活跃(位于最近两个确认笔端点范围内)的 背驰/指标背离/买卖点;
  信号价格落在某个大级别关键位 ±tol 内 → "已验证";远离一切关键位 → 杂波,忽略;
- 硬性过滤与 decision.py 一致:与大级别方向冲突 → 禁止操作;盈亏比<1:2 → 观望并给等待价;
- 刹车印(持仓检查,手册"确认上涨/下跌结束"流程):
  背离预警 → 放量滞涨 → 收盘连续跌破(站上)小级别 EMA20 才算确认,背了又背不是离场理由。
"""

from decision import _direction, STOP_BUFFER

BAR_RANK = {"1m": 0, "5m": 1, "15m": 2, "1H": 3, "4H": 4, "1D": 5, "1W": 6}
# 推荐的大→小级别搭配(手册区间套惯例)
SMALL_DEFAULT = {"1W": "1D", "1D": "1H", "4H": "15m", "1H": "5m", "15m": "1m", "5m": "1m"}


def _rnd(v):
    if v is None:
        return None
    return round(v, 6 if abs(v) < 10 else 2)


def _ema_last(closes, n):
    k = 2.0 / (n + 1)
    e = closes[0]
    for v in closes[1:]:
        e = (v - e) * k + e
    return e


def _confirmed(payload):
    return [c for c in payload["candles"] if c["confirm"] == 1]


def _key_levels(big, live):
    """大级别关键位列表 + 自适应容差(相对现价的比例)。"""
    ch = big["chan"]
    cand = _confirmed(big)
    fin = [b for b in ch["bi"] if not b.get("unfinished")]
    levels = []
    z = (ch.get("summary") or {}).get("last_zs")
    if z:
        levels.append({"name": "中枢上沿 ZG", "price": z["zg"], "kind": "structure"})
        levels.append({"name": "中枢下沿 ZD", "price": z["zd"], "kind": "structure"})
    tops = [b for b in fin if b["dir"] == "up"]
    bots = [b for b in fin if b["dir"] == "down"]
    if tops:
        levels.append({"name": "前高(笔端点)", "price": tops[-1]["end_price"], "kind": "structure"})
    if bots:
        levels.append({"name": "前低(笔端点)", "price": bots[-1]["end_price"], "kind": "structure"})
    closes = [c["c"] for c in cand]
    if len(closes) >= 40:
        levels.append({"name": "EMA20(辅助)", "price": _ema_last(closes, 20), "kind": "ma"})
        levels.append({"name": "EMA40(辅助)", "price": _ema_last(closes, 40), "kind": "ma"})

    win = cand[-20:]
    tol = 0.6 * (sum((c["h"] - c["l"]) / c["c"] for c in win) / len(win)) if win else 0.005

    out = []
    for lv in levels:
        p = lv["price"]
        if not p or p <= 0:
            continue
        dist = (live - p) / live  # >0 = 关键位在现价下方
        out.append({
            "name": lv["name"], "kind": lv["kind"], "price": _rnd(p),
            "dist_pct": round(dist * 100, 2),
            "role": "support" if p <= live else "resistance",
            "testing": abs(dist) <= tol,
        })
    # 几乎重合的关键位合并(相距<0.25×tol),结构位优先保留——避免重复画线/重复验证
    out.sort(key=lambda x: x["price"])
    merged = []
    for lv in out:
        if merged and abs(lv["price"] - merged[-1]["price"]) / live <= 0.25 * tol:
            keep, drop = merged[-1], lv
            if keep["kind"] != "structure" and lv["kind"] == "structure":
                keep, drop = lv, merged[-1]
            keep = dict(keep)
            keep["name"] += " ≈ " + drop["name"]
            keep["testing"] = keep["testing"] or drop["testing"]
            merged[-1] = keep
        else:
            merged.append(lv)
    merged.sort(key=lambda x: -x["price"])
    return merged, tol


def _small_signals(small, levels, tol):
    """小级别活跃信号(背驰/指标背离/买卖点),并做关键位验证。"""
    ch = small["chan"]
    fin = [b for b in ch["bi"] if not b.get("unfinished")]
    if not fin:
        return []
    cutoff = fin[-2]["end_idx"] if len(fin) >= 2 else fin[-1]["start_idx"]

    items = []
    for b in ch.get("beichi") or []:
        if b["k_idx"] < cutoff:
            continue
        buy = b["dir"] == "down"
        items.append({"tag": ("底" if buy else "顶") + "背驰",
                      "side": "long" if buy else "short", "hidden": False,
                      "price": b["price"], "ts": b["ts"], "k_idx": b["k_idx"],
                      "score": b.get("score"), "locked": b.get("locked"),
                      "note": f"MACD面积比 {b['area_ratio']}"})
    for s in ch.get("beili") or []:
        if s["k_idx"] < cutoff:
            continue
        buy = s["type"] == "DB"
        hidden = s.get("subtype") == "hidden"
        items.append({"tag": ("隐" if hidden else "") + ("背离B" if buy else "背离S"),
                      "side": "long" if buy else "short", "hidden": hidden,
                      "price": s["price"], "ts": s["ts"], "k_idx": s["k_idx"],
                      "score": s.get("score"), "locked": s.get("locked"),
                      "note": f"{len(s.get('votes') or [])}票"})
    for p in ch.get("bsp") or []:
        if p["k_idx"] < cutoff:
            continue
        buy = p["type"][0] == "B"
        items.append({"tag": p["type"], "side": "long" if buy else "short", "hidden": False,
                      "price": p["price"], "ts": p["ts"], "k_idx": p["k_idx"],
                      "score": p.get("score"), "locked": p.get("locked"),
                      "note": f"共振{p.get('confirm_n')}票" if p.get("confirm_n") else ""})
    items.sort(key=lambda x: (x["k_idx"], x["ts"]))

    for it in items:
        hit = None
        for lv in levels:
            if abs(it["price"] - lv["price"]) / it["price"] <= tol:
                if hit is None or abs(it["price"] - lv["price"]) < abs(it["price"] - hit["price"]):
                    hit = lv
        it["verify_level"] = hit["name"] if hit else None
        it["verify_price"] = hit["price"] if hit else None
    return items


def _brakes(small, signals, big_dir):
    """刹车印检查(手册"确认趋势结束"流程)。
    视角跟随大级别方向(持仓方向按"顺大级别"假设);大级别中性时才按
    小级别最近确认笔方向——否则大级别上涨、小级别回调时会误显示"持空仓检查"。"""
    cand = _confirmed(small)
    if len(cand) < 30:
        return None
    ch = small["chan"]
    fin = [b for b in ch["bi"] if not b.get("unfinished")]
    if big_dir == "long":
        up, view = True, "跟随大级别多头方向"
    elif big_dir == "short":
        up, view = False, "跟随大级别空头方向"
    else:
        up = fin[-1]["dir"] == "up" if fin else cand[-1]["c"] >= cand[-20]["c"]
        view = "大级别中性,按小级别趋势"

    closes = [c["c"] for c in cand]
    ema20 = _ema_last(closes, 20)
    ema20_prev = _ema_last(closes[:-1], 20)
    last, prev = cand[-1], cand[-2]
    vols = [c["vol"] for c in cand]
    vol5 = sum(vols[-6:-1]) / 5.0
    vma20 = sum(vols[-20:]) / 20.0
    spike = vol5 > 0 and vols[-1] >= 3 * vol5
    rng = last["h"] - last["l"]
    body = abs(last["c"] - last["o"])
    shadow = (last["h"] - max(last["o"], last["c"])) if up else (min(last["o"], last["c"]) - last["l"])
    stall = spike and rng > 0 and (body / rng < 0.35 or shadow / rng > 0.5)
    shrink = vma20 > 0 and vols[-1] < 0.6 * vma20

    # 反向信号(常规背离/背驰/一二三类反向点),隐藏背离是中继不算刹车
    rev_side = "short" if up else "long"
    div = None
    for it in reversed(signals):
        if it["side"] == rev_side and not it.get("hidden"):
            div = it
            break

    above = last["c"] > ema20
    prev_above = prev["c"] > ema20_prev
    broke = (not above and not prev_above) if up else (above and prev_above)
    edge_now = (not above) if up else above  # 最新一根已在生命线错误一侧(未连续)

    if up:
        checks = [
            {"name": "刹车印① 顶部背离/背驰", "hit": bool(div),
             "note": f"{div['tag']} @ {_rnd(div['price'])}" if div else "价格新高,指标未出反向信号"},
            {"name": "刹车印② 放量滞涨(量≥前5根均量×3 + 小实体/长上影)", "hit": stall,
             "note": f"最新量/前5均量 = {round(vols[-1] / vol5, 2) if vol5 > 0 else '-'}"},
            {"name": "刹车印③ 收盘连续跌破小级别EMA20", "hit": broke,
             "note": f"EMA20 {_rnd(ema20)},最新收盘 {_rnd(last['c'])}" + ("(已在下方,待下根确认)" if edge_now and not broke else "")},
        ]
        if broke:
            case = ("C", "确认结束:收盘连续跌破小级别EMA20且未收回 → 清掉浮动仓,"
                         "大概率要去测大级别下方支撑。", "#f6465d")
        elif div and (stall or edge_now):
            case = ("B", "准备减仓:反向信号 + 放量滞涨/首次失守EMA20 → 可减30-40%浮动仓"
                         "锁定利润,底仓留着;跌破且收不回再清。", "#d29922")
        elif div or stall:
            case = ("A", "强势调整(预警):出现单个刹车印,但EMA20未破" +
                         ("且缩量回调" if shrink else "") + " → 持有不动,盯紧EMA20。"
                         "逼空行情背了又背常见,背离本身不是离场理由。", "#2ebd85")
        else:
            case = ("A", "无刹车印:小级别仍站在EMA20上方,趋势未破坏,安心持有。", "#2ebd85")
        title = f"持多仓检查({view})"
    else:
        checks = [
            {"name": "刹车印① 底部背离/背驰", "hit": bool(div),
             "note": f"{div['tag']} @ {_rnd(div['price'])}" if div else "价格新低,指标未出反向信号"},
            {"name": "刹车印② 放量杀跌滞跌(量≥前5根均量×3 + 小实体/长下影)", "hit": stall,
             "note": f"最新量/前5均量 = {round(vols[-1] / vol5, 2) if vol5 > 0 else '-'}"},
            {"name": "刹车印③ 收盘连续站上小级别EMA20", "hit": broke,
             "note": f"EMA20 {_rnd(ema20)},最新收盘 {_rnd(last['c'])}" + ("(已在上方,待下根确认)" if edge_now and not broke else "")},
        ]
        if broke:
            case = ("C", "确认结束:收盘连续站上小级别EMA20 → 空单浮动仓离场,"
                         "大概率要去测大级别上方压力。", "#f6465d")
        elif div and (stall or edge_now):
            case = ("B", "准备减仓:反向信号 + 放量滞跌/首次站上EMA20 → 空单可减30-40%"
                         "锁定利润;连续站上再清。", "#d29922")
        elif div or stall:
            case = ("A", "弱势反抽(预警):出现单个刹车印,但EMA20未被有效收复 → 空单持有,"
                         "盯紧EMA20。阴跌行情底背离钝化常见,背离本身不是回补理由。", "#2ebd85")
        else:
            case = ("A", "无刹车印:小级别仍被压在EMA20下方,空头趋势未破坏。", "#2ebd85")
        title = f"持空仓检查({view})"

    return {"title": title, "trend": "up" if up else "down", "checks": checks,
            "ema20": _rnd(ema20), "shrink": shrink,
            "case": case[0], "advice": case[1], "color": case[2]}


def _breakout(small, levels, big_bar, small_bar):
    """场景三:小级别放量K线收盘穿越大级别关键位。
    按讨论口径降级为"观察信号":单根小级别K线定不了大级别方向,只提示、
    明确要求等大级别收盘确认,绝不作为入场依据(防"用小级别推大级别"的禁区)。"""
    cand = _confirmed(small)
    if len(cand) < 7:
        return None
    last, prev = cand[-1], cand[-2]
    vols = [c["vol"] for c in cand]
    v5 = sum(vols[-6:-1]) / 5.0
    if v5 <= 0 or vols[-1] < 2 * v5:
        return None
    ratio = round(vols[-1] / v5, 1)
    for lv in levels:
        p = lv["price"]
        if last["c"] > last["o"] and prev["c"] < p <= last["c"]:
            return {"dir": "up", "level": lv["name"], "price": p, "vol_ratio": ratio,
                    "note": f"{small_bar} 放量阳线({ratio}×前5均量)收盘站上 {big_bar} "
                            f"{lv['name']} {p}。观察信号:等 {big_bar} 收盘确认再说,"
                            f"勿直接追——单根小级别K线定不了大级别方向。"}
        if last["c"] < last["o"] and prev["c"] > p >= last["c"]:
            return {"dir": "down", "level": lv["name"], "price": p, "vol_ratio": ratio,
                    "note": f"{small_bar} 放量阴线({ratio}×前5均量)收盘跌破 {big_bar} "
                            f"{lv['name']} {p}。观察信号:等 {big_bar} 收盘确认再说,"
                            f"勿直接追空——单根小级别K线定不了大级别方向。"}
    return None


def analyze(big, small, big_bar, small_bar):
    """主入口:big/small 为 server.build_payload 的完整数据。"""
    live = small["candles"][-1]["c"]
    hd = _direction((big["chan"] or {}).get("summary") or {})
    levels, tol = _key_levels(big, live)
    signals = _small_signals(small, levels, tol)
    brakes = _brakes(small, signals, hd["dir"])
    breakout = _breakout(small, levels, big_bar, small_bar)

    testing = [lv for lv in levels if lv["testing"]]
    testing.sort(key=lambda lv: (lv["kind"] != "structure", abs(lv["dist_pct"])))  # 结构位优先
    tested = testing[0] if testing else None

    verified = [s for s in signals if s["verify_level"]]
    act = verified[-1] if verified else None

    res = {"big_bar": big_bar, "small_bar": small_bar, "live": _rnd(live),
           "dir": hd["dir"], "dir_desc": hd["desc"], "dir_strength": hd["strength"],
           "tol_pct": round(tol * 100, 2), "levels": levels,
           "signals": signals, "brakes": brakes, "tested": tested,
           "breakout": breakout}

    tolp = round(tol * 100, 2)
    if not act:
        if tested:
            want = "底背驰 / 背离B / B1-B3" if tested["role"] == "support" else "顶背驰 / 背离S / S1-S3"
            res.update(action="wait", score=0, scenario="正在测试关键位",
                       reason=f"现价 {_rnd(live)} 正在测试 {big_bar} {tested['name']} {tested['price']}"
                              f"(距离 {tested['dist_pct']}%),但 {small_bar} 还没有确认信号。"
                              f"按口径等小级别出现 {want} + 分型 + 破前笔端点再动手,不要裸猜关键位。")
        elif signals:
            s = signals[-1]
            res.update(action="wait", score=0, scenario="杂波过滤",
                       reason=f"{small_bar} 最新信号 {s['tag']} @ {_rnd(s['price'])} 距任何 {big_bar} 关键位"
                              f"都超过容差 ±{tolp}%。价格远离大级别关键位的小级别信号按口径视为杂波,忽略。")
        else:
            res.update(action="wait", score=0, scenario="等待",
                       reason=f"现价未接触 {big_bar} 关键位,{small_bar} 也无活跃信号。"
                              f"等价格走到左表关键位附近,再看小级别确认。")
        return res

    side = act["side"]
    if (side == "long" and hd["dir"] == "short") or (side == "short" and hd["dir"] == "long"):
        res.update(action="wait", score=0, scenario="与大级别方向冲突", signal=act,
                   reason=f"{small_bar} {act['tag']} 在 {act['verify_level']} 得到位置验证,"
                          f"但 {big_bar} 大级别方向相反({hd['desc']})。"
                          f"手册规定:信号与大级别冲突,禁止操作。")
        return res

    scenario = ("场景一:大级别支撑 + 小级别底部确认" if side == "long"
                else "场景二:大级别压力 + 小级别顶部确认")
    align = 30 if hd["dir"] == side else 15  # 中性=15,顺方向=30
    sig_part = (act["score"] if act["score"] is not None else 50) / 100 * 40
    test_part = 30 if tested else 18  # 信号验证过关键位但现价已离开一段 → 少给
    score = round(min(100.0, align + sig_part + test_part), 1)

    # 目标位:结构位优先(均线会移动,拿它当目标会把盈亏比算得虚小)
    def _pick_target(cands):
        st = [p for p, k in cands if k == "structure"]
        pool = st or [p for p, _ in cands]
        return pool[0] if pool else None

    lvl_price = act["verify_price"]
    if side == "long":
        base = lvl_price if lvl_price < live else act["price"]
        stop = base * (1 - STOP_BUFFER)
        ups = sorted((lv["price"], lv["kind"]) for lv in levels if lv["price"] > live * 1.002)
        target = _pick_target(ups)
        risk, reward = live - stop, (target - live) if target else None
    else:
        base = lvl_price if lvl_price > live else act["price"]
        stop = base * (1 + STOP_BUFFER)
        dns = sorted(((lv["price"], lv["kind"]) for lv in levels if lv["price"] < live * 0.998),
                     reverse=True)
        target = _pick_target(dns)
        risk, reward = stop - live, (live - target) if target else None

    rr = round(reward / risk, 2) if (reward and risk > 0) else None
    lock_tail = "(未锁定·或移动,稳健等锁定)" if act.get("locked") is False else ""

    if rr is not None and rr < 2:
        better = (target + 2 * stop) / 3
        res.update(action="wait", score=score, scenario=scenario, signal=act,
                   stop=_rnd(stop), target=_rnd(target), rr=rr, better_entry=_rnd(better),
                   reason=f"{small_bar} {act['tag']} 在 {big_bar} {act['verify_level']} 得到验证{lock_tail},"
                          f"但现价 {_rnd(live)} 到下一关键位的盈亏比仅 1:{rr},低于手册硬约束 1:2 → 观望,"
                          f"等{'回调' if side == 'long' else '反弹'}到 {_rnd(better)} 附近再考虑。")
        return res

    warn = None if target else "顺方向一侧没有明确的大级别关键位作目标,谨慎追单。"
    res.update(action=side, score=score, scenario=scenario, signal=act,
               entry=_rnd(live), stop=_rnd(stop), target=_rnd(target), rr=rr, warning=warn,
               reason=f"{big_bar} {hd['desc']};{small_bar} {act['tag']}"
                      f"(评分 {act['score']})在 {big_bar} {act['verify_level']} "
                      f"{act['verify_price']} 得到位置验证{lock_tail}。")
    return res
