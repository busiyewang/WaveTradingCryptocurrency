"""缠论结构算法:包含处理 → 分型 → 笔(新笔) → 中枢 → 背驰 → 一二三类买卖点。

规则依据《缠论MACD币圈实战手册.html》:
- 包含处理:向上取"高高",向下取"低低",方向由前一根未被包含 K 线决定。
- 顶分型 = 中间K高点最高且低点也最高(处理后K线);底分型反之。
- 新笔:两端点之间(含端点)≥5 根处理后K线;端点取分型中间K的极值;分型顶底交替,
  同类型取更极端者。
- 中枢:≥3 个连续段(相邻笔近似)重叠,ZG=min(各段高点),ZD=max(各段低点),ZG>ZD,可延伸。
- 背驰:进入段 vs 离开段的 MACD 柱同向面积和,后/前 < 0.7 有效;
  跨≥2 个同向依次移动的中枢 = 趋势背驰,否则盘整背驰。
- 买卖点:1买=下跌末段背驰低点;2买=其后回调不破前低;3买=离开中枢回踩不跌回 ZG 之下。
"""

from indicators import macd, macd_hist_list
import pandas as pd

# 背驰评分器级别权重(手册 LVL_W;15m 手册未给,取 0.8 插值)
LEVEL_WEIGHT = {"1m": 0.55, "5m": 0.7, "15m": 0.8, "1H": 0.9,
                "4H": 1.0, "1D": 1.0, "1W": 1.0}


# ---------------------------------------------------------------- 包含处理

def merge_klines(candles):
    """K线包含处理。返回处理后K线列表:
    {h, l, hi_idx, lo_idx, idx_start, idx_end}
    hi_idx/lo_idx 为极值所在原始K线下标(分型定位用)。"""
    merged = []
    direction = 1  # 前一根未被包含K线决定;初始默认向上
    for i, k in enumerate(candles):
        h, l = k["h"], k["l"]
        if not merged:
            merged.append({"h": h, "l": l, "hi_idx": i, "lo_idx": i,
                           "idx_start": i, "idx_end": i})
            continue
        last = merged[-1]
        contains = (last["h"] >= h and last["l"] <= l) or \
                   (h >= last["h"] and l <= last["l"])
        if contains:
            if direction >= 0:  # 向上:高高
                if h > last["h"]:
                    last["h"], last["hi_idx"] = h, i
                if l > last["l"]:
                    last["l"], last["lo_idx"] = l, i
            else:  # 向下:低低
                if h < last["h"]:
                    last["h"], last["hi_idx"] = h, i
                if l < last["l"]:
                    last["l"], last["lo_idx"] = l, i
            last["idx_end"] = i
        else:
            direction = 1 if h > last["h"] else -1
            merged.append({"h": h, "l": l, "hi_idx": i, "lo_idx": i,
                           "idx_start": i, "idx_end": i})
    return merged


# ---------------------------------------------------------------- 分型

def find_fenxing(merged, candles):
    """顶/底分型。返回 [{type, mk_idx, k_idx, price, ts}]。"""
    out = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b["h"] > a["h"] and b["h"] > c["h"] and b["l"] > a["l"] and b["l"] > c["l"]:
            out.append({"type": "top", "mk_idx": i, "k_idx": b["hi_idx"],
                        "price": b["h"], "ts": candles[b["hi_idx"]]["ts"]})
        elif b["l"] < a["l"] and b["l"] < c["l"] and b["h"] < a["h"] and b["h"] < c["h"]:
            out.append({"type": "bottom", "mk_idx": i, "k_idx": b["lo_idx"],
                        "price": b["l"], "ts": candles[b["lo_idx"]]["ts"]})
    return out


# ---------------------------------------------------------------- 笔(新笔规则)

def build_bi(fenxing, candles):
    """新笔:端点分型顶底交替、同类型取更极端者、两端点间(含端点)≥5 根处理后K线。
    返回 (bi_list, endpoints)。bi: {dir, start_idx, end_idx, start_price, end_price,
    start_ts, end_ts, start_mk, end_mk, mk_count, unfinished}"""
    eps = []  # 确认的端点分型序列
    for fx in fenxing:
        if not eps:
            eps.append(fx)
            continue
        last = eps[-1]
        if fx["type"] == last["type"]:
            # 同类型取更极端者
            more_extreme = (fx["price"] > last["price"]) if fx["type"] == "top" \
                else (fx["price"] < last["price"])
            if more_extreme:
                # 新分型时间更靠后,与前一端点的距离只增不减,直接替换
                eps[-1] = fx
        else:
            if _can_form_bi(last, fx):
                eps.append(fx)
            # 距离不足:忽略该分型,等待后续更极端或更远的分型

    bis = []
    for i in range(len(eps) - 1):
        a, b = eps[i], eps[i + 1]
        bis.append({
            "dir": "up" if b["type"] == "top" else "down",
            "start_idx": a["k_idx"], "end_idx": b["k_idx"],
            "start_price": a["price"], "end_price": b["price"],
            "start_ts": candles[a["k_idx"]]["ts"], "end_ts": candles[b["k_idx"]]["ts"],
            "start_mk": a["mk_idx"], "end_mk": b["mk_idx"],
            "mk_count": b["mk_idx"] - a["mk_idx"] + 1,
            "unfinished": False,
        })

    # 进行中的未完成笔:从最后端点到之后的极值K线(虚线展示用)
    if eps:
        last_ep = eps[-1]
        tail = candles[last_ep["k_idx"] + 1:]
        if len(tail) >= 1:
            base = last_ep["k_idx"] + 1
            if last_ep["type"] == "top":  # 向下进行中
                j = min(range(len(tail)), key=lambda x: tail[x]["l"])
                price, dir_ = tail[j]["l"], "down"
            else:
                j = max(range(len(tail)), key=lambda x: tail[x]["h"])
                price, dir_ = tail[j]["h"], "up"
            end_idx = base + j
            if end_idx > last_ep["k_idx"]:
                bis.append({
                    "dir": dir_,
                    "start_idx": last_ep["k_idx"], "end_idx": end_idx,
                    "start_price": last_ep["price"], "end_price": price,
                    "start_ts": candles[last_ep["k_idx"]]["ts"],
                    "end_ts": candles[end_idx]["ts"],
                    "start_mk": last_ep["mk_idx"], "end_mk": -1,
                    "mk_count": 0, "unfinished": True,
                })
    return bis, eps


def _can_form_bi(fx_a, fx_b):
    """新笔条件:两端点间(含端点)≥5 根处理后K线,且顶价>底价。"""
    if fx_b["mk_idx"] - fx_a["mk_idx"] + 1 < 5:
        return False
    top = fx_a if fx_a["type"] == "top" else fx_b
    bot = fx_b if fx_a["type"] == "top" else fx_a
    return top["price"] > bot["price"]


# ---------------------------------------------------------------- 中枢

def _bi_high(b):
    return max(b["start_price"], b["end_price"])


def _bi_low(b):
    return min(b["start_price"], b["end_price"])


def build_zhongshu(bis, candles):
    """相邻笔近似次级别段。连续 ≥3 笔重叠成中枢:ZG=min(高点), ZD=max(低点), ZG>ZD;
    后续笔与 [ZD,ZG] 有重叠则延伸并入。返回
    [{zg, zd, gg, dd, bi_start, bi_end, bi_count, start_idx, end_idx, start_ts, end_ts}]"""
    fin = [b for b in bis if not b["unfinished"]]
    out = []
    i = 0
    while i + 2 < len(fin):
        b1, b2, b3 = fin[i], fin[i + 1], fin[i + 2]
        zg = min(_bi_high(b1), _bi_high(b2), _bi_high(b3))
        zd = max(_bi_low(b1), _bi_low(b2), _bi_low(b3))
        if zg > zd:
            j = i + 3
            # 延伸并入,最多 9 段(超过应升级为更大级别中枢,手册惯例)
            while j < len(fin) and j - i < 9 and \
                    _bi_high(fin[j]) >= zd and _bi_low(fin[j]) <= zg:
                j += 1
            seg = fin[i:j]
            out.append({
                "zg": zg, "zd": zd,
                "gg": max(_bi_high(b) for b in seg),
                "dd": min(_bi_low(b) for b in seg),
                "bi_start": bis.index(b1), "bi_end": bis.index(seg[-1]),
                "bi_count": len(seg),
                "start_idx": b1["start_idx"], "end_idx": seg[-1]["end_idx"],
                "start_ts": b1["start_ts"], "end_ts": seg[-1]["end_ts"],
            })
            i = j
        else:
            i += 1
    return out


# ---------------------------------------------------------------- 背驰

def _seg_area(hist, b, direction):
    """一笔区间内 MACD 同向柱面积:down 取负柱绝对值和,up 取正柱和。"""
    s = 0.0
    for v in hist[b["start_idx"]: b["end_idx"] + 1]:
        if direction == "down" and v < 0:
            s += -v
        elif direction == "up" and v > 0:
            s += v
    return s


def detect_beichi(bis, zss, candles, bar):
    """背驰:比较前后两个同向笔(前段=进入段近似,后段=离开段)。
    门槛:后段创新低/新高;有效条件:MACD 同向柱面积比 后/前 < 0.7。
    趋势/盘整背驰用中枢判别:后段之前存在 ≥2 个同向依次移动的中枢 = 趋势背驰。
    返回 [{kind, dir, k_idx, price, ts, area_ratio, score, score_detail, bi_idx}]"""
    close = pd.Series([c["c"] for c in candles], dtype=float)
    dif_s, _, _ = macd(close)
    dif = dif_s.tolist()
    hist = macd_hist_list(candles)
    lvl_w = LEVEL_WEIGHT.get(bar, 1.0)

    out = []
    for i in range(2, len(bis)):
        bin_, bout = bis[i - 2], bis[i]
        if bin_["dir"] != bout["dir"] or bin_["unfinished"] or bout["unfinished"]:
            continue
        d = bout["dir"]
        # 门槛:后段须创新低/新高
        if d == "down" and not _bi_low(bout) < _bi_low(bin_):
            continue
        if d == "up" and not _bi_high(bout) > _bi_high(bin_):
            continue
        a_in = _seg_area(hist, bin_, d)
        a_out = _seg_area(hist, bout, d)
        if a_in <= 0:
            continue
        ratio = a_out / a_in
        if ratio >= 0.7:
            continue  # 手册:<0.7 才是有效背驰

        # 趋势 vs 盘整:后段结束前的最近两个中枢是否同向依次移动
        kind = "panzheng"
        prior = [z for z in zss if z["end_idx"] <= bout["end_idx"]]
        if len(prior) >= 2:
            z1, z2 = prior[-2], prior[-1]
            if (d == "down" and z1["zd"] >= z2["zg"]) or \
               (d == "up" and z1["zg"] <= z2["zd"]):
                kind = "trend"

        score, detail = _score_beichi(ratio, kind, bin_, bout, dif, candles, lvl_w)
        out.append({
            "kind": kind, "dir": d,
            "k_idx": bout["end_idx"], "price": bout["end_price"],
            "ts": bout["end_ts"], "area_ratio": round(ratio, 4),
            "score": score, "score_detail": detail,
            "bi_idx": i,
        })
    return out


def _score_beichi(ratio, kind, bin_, bout, dif, candles, lvl_w):
    """手册背驰评分器:面积比分段(≤0.4→30/≤0.6→26/≤0.7→20/≤0.85→11/<1→4/≥1→0)
    + DIF极值20 + 区间套20(单周期无法验证,恒0) + 趋势15 + 结构12 + 斜率8 + 量能7
    + 反向分型8,总权重120 归一到 100,乘级别权重。"""
    if ratio <= 0.4:
        s_area = 30
    elif ratio <= 0.6:
        s_area = 26
    elif ratio <= 0.7:
        s_area = 20
    elif ratio <= 0.85:
        s_area = 11
    elif ratio < 1.0:
        s_area = 4
    else:
        s_area = 0

    d = bout["dir"]
    din = dif[bin_["start_idx"]: bin_["end_idx"] + 1]
    dout = dif[bout["start_idx"]: bout["end_idx"] + 1]
    if d == "down":
        s_dif = 20 if min(dout) > min(din) else 0   # DIF 低点抬高
    else:
        s_dif = 20 if max(dout) < max(din) else 0   # DIF 高点降低

    s_sub = 0  # 区间套:单周期无法自动验证,保守记 0
    s_trend = 15 if kind == "trend" else 0
    s_struct = 12 if bout["mk_count"] >= 9 else 0  # 后段结构完整(处理后K≥9近似)

    bars_in = bin_["end_idx"] - bin_["start_idx"] + 1
    bars_out = bout["end_idx"] - bout["start_idx"] + 1
    slope_in = abs(bin_["end_price"] - bin_["start_price"]) / max(bars_in, 1)
    slope_out = abs(bout["end_price"] - bout["start_price"]) / max(bars_out, 1)
    s_slope = 8 if (bars_out >= bars_in and slope_out < slope_in) else 0

    vol_in = sum(c["vol"] for c in candles[bin_["start_idx"]: bin_["end_idx"] + 1])
    vol_out = sum(c["vol"] for c in candles[bout["start_idx"]: bout["end_idx"] + 1])
    s_vol = 7 if vol_out < vol_in else 0

    s_fx = 8 if not bout["unfinished"] else 0  # 笔已被反向分型确认

    raw = s_area + s_dif + s_sub + s_trend + s_struct + s_slope + s_vol + s_fx
    score = round(raw / 120 * 100 * lvl_w, 1)
    detail = {"area": s_area, "dif": s_dif, "sub": "未验证", "trend": s_trend,
              "struct": s_struct, "slope": s_slope, "vol": s_vol, "fx": s_fx}
    return score, detail


# ---------------------------------------------------------------- 买卖点

def _grade(score):
    return "高" if score >= 75 else ("中" if score >= 60 else
                                     ("一般" if score >= 45 else "弱"))


def find_bsp(bis, zss, bcs, candles, bar):
    """一二三类买卖点(带评分,0-100,越高越可靠)。
    评分 = 类型基础分(手册可靠度 3类≈2类>1类)+ 结构质量 + 量能配合,
    最后乘级别权重(级别越大越可靠)。
    返回 [{type, k_idx, price, ts, note, score, grade}]。"""
    out = []
    fin = bis
    lvl_w = LEVEL_WEIGHT.get(bar, 1.0)

    def vol_rate(b):
        """笔区间的单位K线均量(归一化,便于不同长度笔比较)。"""
        seg = candles[b["start_idx"]: b["end_idx"] + 1]
        return sum(c["vol"] for c in seg) / max(len(seg), 1)

    def emit(type_, k_idx, price, ts, note, raw):
        score = min(100.0, round(raw * lvl_w, 1))
        out.append({"type": type_, "k_idx": k_idx, "price": price, "ts": ts,
                    "note": note, "score": score, "grade": _grade(score)})

    # 1类:有效背驰的端点。评分主要继承背驰评分(已含面积比/趋势盘整/结构等)
    for bc in bcs:
        bc_raw = bc["score"] / lvl_w if lvl_w > 0 else bc["score"]  # 还原未加权分
        raw1 = 40 + bc_raw * 0.4
        kind_cn = "趋势" if bc["kind"] == "trend" else "盘整"
        if bc["dir"] == "down":
            emit("B1", bc["k_idx"], bc["price"], bc["ts"],
                 f"{kind_cn}底背驰,面积比{bc['area_ratio']},背驰评分{bc['score']}", raw1)
        else:
            emit("S1", bc["k_idx"], bc["price"], bc["ts"],
                 f"{kind_cn}顶背驰,面积比{bc['area_ratio']},背驰评分{bc['score']}", raw1)

        # 2类:1类点之后,次级别回调/反弹不破前极值
        i = bc["bi_idx"]
        if i + 2 < len(fin) and not fin[i + 2]["unfinished"]:
            b_imp, b_back = fin[i + 1], fin[i + 2]
            imp_rng = abs(b_imp["end_price"] - b_imp["start_price"])
            back_rng = abs(b_back["end_price"] - b_back["start_price"])
            retr = back_rng / imp_rng if imp_rng > 0 else 1.0
            raw2 = 50 + (18 if retr <= 0.382 else 10 if retr <= 0.618 else 4)
            raw2 += bc_raw * 0.15                       # 联动前面1类点的背驰强度
            if vol_rate(b_back) < vol_rate(b_imp):
                raw2 += 8                               # 回调/反弹缩量
            extra = f"回撤{retr:.0%}" + (",缩量" if vol_rate(b_back) < vol_rate(b_imp) else "")
            if bc["dir"] == "down" and b_back["dir"] == "down" \
                    and b_back["end_price"] > bc["price"]:
                emit("B2", b_back["end_idx"], b_back["end_price"], b_back["end_ts"],
                     f"1买后回调不破前低 {bc['price']}({extra})", raw2)
            if bc["dir"] == "up" and b_back["dir"] == "up" \
                    and b_back["end_price"] < bc["price"]:
                emit("S2", b_back["end_idx"], b_back["end_price"], b_back["end_ts"],
                     f"1卖后反弹不过前高 {bc['price']}({extra})", raw2)

    # 3类:离开中枢后回踩/回抽不回中枢
    for zs in zss:
        k = zs["bi_end"] + 1
        if k + 1 >= len(fin):
            continue
        b_leave, b_back = fin[k], fin[k + 1]
        if b_back["unfinished"]:
            continue
        zs_h = zs["zg"] - zs["zd"]
        leave_rng = abs(b_leave["end_price"] - b_leave["start_price"])
        power = leave_rng / zs_h if zs_h > 0 else 0     # 离开段突破力度
        shrink = vol_rate(b_back) < vol_rate(b_leave)   # 回踩/回抽缩量

        def raw3(depth):
            r = 55
            r += 15 if depth <= 0.01 else (8 if depth <= 0.03 else 3)   # 回踩浅
            r += 15 if power >= 1.5 else (8 if power >= 0.8 else 3)     # 突破有力
            r += 8 if shrink else 0
            return r

        if b_leave["dir"] == "up" and _bi_high(b_leave) > zs["zg"] \
                and b_back["dir"] == "down" and b_back["end_price"] > zs["zg"]:
            depth = (b_back["end_price"] - zs["zg"]) / zs["zg"]
            emit("B3", b_back["end_idx"], b_back["end_price"], b_back["end_ts"],
                 f"回踩不破中枢上沿 ZG={zs['zg']}" + (",缩量" if shrink else ""),
                 raw3(abs(depth)))
        if b_leave["dir"] == "down" and _bi_low(b_leave) < zs["zd"] \
                and b_back["dir"] == "up" and b_back["end_price"] < zs["zd"]:
            depth = (zs["zd"] - b_back["end_price"]) / zs["zd"]
            emit("S3", b_back["end_idx"], b_back["end_price"], b_back["end_ts"],
                 f"回抽不上中枢下沿 ZD={zs['zd']}" + (",缩量" if shrink else ""),
                 raw3(abs(depth)))

    out.sort(key=lambda p: p["k_idx"])
    return out


# ---------------------------------------------------------------- 汇总

def summarize(bis, zss, bcs, bsp, candles):
    last_price = candles[-1]["c"] if candles else None
    s = {"last_price": last_price, "last_bi": None, "last_zs": None,
         "price_pos": None, "last_beichi": bcs[-1] if bcs else None,
         "last_bsp": bsp[-1] if bsp else None}
    fin = [b for b in bis if not b["unfinished"]]
    if fin:
        b = fin[-1]
        s["last_bi"] = {"dir": b["dir"], "start_price": b["start_price"],
                        "end_price": b["end_price"], "end_ts": b["end_ts"]}
    if zss:
        z = zss[-1]
        extending = z["bi_end"] >= len(fin) - 1 if fin else False
        s["last_zs"] = {"zg": z["zg"], "zd": z["zd"], "gg": z["gg"], "dd": z["dd"],
                        "extending": extending}
        if last_price is not None:
            s["price_pos"] = "above_zg" if last_price > z["zg"] else \
                             ("below_zd" if last_price < z["zd"] else "inside")
    return s


def analyze(candles, bar):
    """主入口。candles 为升序、仅含已完结K线。"""
    if len(candles) < 10:
        return {"fenxing": [], "bi": [], "zhongshu": [], "beichi": [],
                "bsp": [], "summary": {}}
    merged = merge_klines(candles)
    fenxing = find_fenxing(merged, candles)
    bis, _ = build_bi(fenxing, candles)
    zss = build_zhongshu(bis, candles)
    bcs = detect_beichi(bis, zss, candles, bar)
    bsp = find_bsp(bis, zss, bcs, candles, bar)
    # 锁定状态:信号所在笔端点之后已有新的确认笔 → 锁定;
    # 位于最新确认笔端点上的信号未锁定,若出现更极端分型,端点和信号仍会移动
    fin = [b for b in bis if not b["unfinished"]]
    last_end = fin[-1]["end_idx"] if fin else -1
    for p in bsp:
        p["locked"] = p["k_idx"] < last_end
    for b in bcs:
        b["locked"] = b["k_idx"] < last_end
    summary = summarize(bis, zss, bcs, bsp, candles)
    return {"fenxing": fenxing, "bi": bis, "zhongshu": zss,
            "beichi": bcs, "bsp": bsp, "summary": summary}
