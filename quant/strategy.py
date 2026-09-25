"""量化专用过滤层，不直接执行界面 decision/multilevel 的 action。"""
from hashlib import sha256

from decision import STOP_BUFFER, _direction, active_signal_cutoff


def signal_key(inst, bar, signal):
    # 不使用滚动窗口内 k_idx，窗口左移不应产生新的交易信号。
    raw = "{}:{}:{}:{}".format(inst, bar, signal["type"], signal["ts"])
    return sha256(raw.encode()).hexdigest()


def structure_levels(analysis):
    levels = []
    zs = analysis.get("summary", {}).get("last_zs")
    if zs:
        levels.extend([zs["zg"], zs["zd"]])
    finished = [b for b in analysis["bi"] if not b.get("unfinished")]
    for direction in ("up", "down"):
        legs = [b for b in finished if b["dir"] == direction]
        if legs:
            levels.append(legs[-1]["end_price"])
    return sorted(set(p for p in levels if p > 0))


def evaluate(inst, big_rows, small_rows, big, small, config):
    """仅用当前收盘快照；返回研究候选，不代表可直接提交的订单。"""
    result = {"action": "wait", "reason": "no_active_signal", "signal_key": None}
    finished = [b for b in small["bi"] if not b.get("unfinished")]
    if len(finished) < 2:
        return result
    active = [s for s in small["bsp"]
              if s["k_idx"] >= active_signal_cutoff(finished)
              and s["type"] in ("B2", "B3", "S2", "S3")]
    if not active:
        return result
    signal = max(active, key=lambda s: (s["ts"], s["type"]))
    result.update(signal_key=signal_key(inst, config.small_bar, signal), signal=signal)
    if signal.get("locked") is not True:
        return dict(result, reason="signal_unlocked")
    side = "long" if signal["type"].startswith("B") else "short"
    direction = _direction(big.get("summary") or {})["dir"]
    result["direction"] = direction
    if direction != side:
        return dict(result, reason="direction_neutral_or_conflict")

    levels = structure_levels(big)
    window = big_rows[-20:]
    tolerance = 0.6 * sum((c["h"] - c["l"]) / c["c"] for c in window) / len(window)
    near = [p for p in levels if abs(signal["price"] - p) / signal["price"] <= tolerance]
    if not near:
        return dict(result, reason="away_from_structure")

    sign = 1 if side == "long" else -1
    reference = small_rows[-1]["c"]
    stop = signal["price"] * (1 - sign * STOP_BUFFER)
    # 费用模型保守估算开仓、止损及目标退出；资金费尚未模拟。
    entry = reference * (1 + sign * config.slippage_rate)
    stop_fill = stop * (1 - sign * config.slippage_rate)
    result.update(reference_price=reference, estimated_entry=entry, stop=stop,
                  verified_level=min(near, key=lambda p: abs(p - signal["price"])))
    if sign * (reference - stop) <= 0:
        return dict(result, reason="structure_invalidated")
    targets = [p for p in levels if sign * (p - entry) > 0]
    if not targets:
        return dict(result, reason="no_structure_target")
    target = min(targets, key=lambda p: abs(p - entry))
    target_fill = target * (1 - sign * config.slippage_rate)
    unit_loss = sign * (entry - stop_fill) + config.fee_rate * (entry + stop_fill)
    unit_gain = sign * (target_fill - entry) - config.fee_rate * (entry + target_fill)
    rr = unit_gain / unit_loss
    result.update(target=target, net_rr=rr)
    if rr < config.min_net_rr:
        return dict(result, reason="net_rr_below_minimum")
    return dict(result, action=side, reason="eligible_research_candidate")
