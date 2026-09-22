"""独立的线性 USDT 合约风险测算；不读取评分仓位、不产生交易指令。"""
from decimal import Decimal, ROUND_FLOOR


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("风险参数必须是有限数")
    return result


def size_linear_contract(*, side, equity, free_margin, entry, stop, base_per_contract,
                         lot_size, min_size, max_size, risk_fraction="0.005",
                         portfolio_fraction="0.01", open_risk="0", open_positions=0,
                         max_positions=2, leverage="3", fee_rate="0.0005",
                         slippage_rate="0.0005", max_notional_fraction="1"):
    """entry=计划成交价，base_per_contract=已核实面值/乘数后的每张基础币数量。

    费用率为单边；止损滑点另计。这里只限制新单，不负责账户日/周熔断。
    返回的风险是模型估计上限，跳空/流动性不足时真实亏损可更大。
    """
    values = [equity, free_margin, entry, stop, base_per_contract, lot_size, min_size,
              max_size, risk_fraction, portfolio_fraction, open_risk, leverage,
              fee_rate, slippage_rate, max_notional_fraction]
    (equity, margin, entry, stop, base_qty, lot, minimum, maximum, fraction,
     total_fraction, existing_risk, lev, fee, slip, notional_fraction) = map(number, values)
    if side not in ("long", "short"):
        raise ValueError("side 必须为 long 或 short")
    if any(v <= 0 for v in (equity, entry, stop, base_qty, lot, minimum, maximum, lev)):
        raise ValueError("权益、价格、合约参数、杠杆必须为正")
    if maximum < minimum or margin < 0 or existing_risk < 0:
        raise ValueError("余额/已有风险/下单上下限不合法")
    if not (0 < fraction <= total_fraction <= 1 and 0 < notional_fraction <= 1):
        raise ValueError("风险比例/名义仓位上限不合法")
    if not (0 <= fee < Decimal("0.1") and 0 <= slip < Decimal("0.1")):
        raise ValueError("费用/滑点不合法")
    if type(open_positions) is not int or type(max_positions) is not int or open_positions < 0 or max_positions < 1:
        raise ValueError("持仓数量不合法")
    sign = Decimal(1 if side == "long" else -1)
    if sign * (entry - stop) <= 0:
        raise ValueError("止损位必须位于入场价亏损的一侧")
    if open_positions >= max_positions:
        return {"allowed": False, "reason": "position_limit"}
    budget = min(equity * fraction, equity * total_fraction - existing_risk)
    if budget <= 0:
        return {"allowed": False, "reason": "portfolio_risk_limit"}
    stop_fill = stop * (1 - sign * slip)
    loss_per_contract = base_qty * (sign * (entry - stop_fill) + fee * (entry + stop_fill))
    notional_per_contract = base_qty * entry
    # 占用保证金之外预留双边手续费；不是完整强平/维持保证金模型。
    margin_per_contract = notional_per_contract * (1 / lev + 2 * fee)
    raw = min(budget / loss_per_contract, margin / margin_per_contract,
              equity * notional_fraction / notional_per_contract, maximum)
    contracts = (raw / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    if contracts < minimum:
        return {"allowed": False, "reason": "below_minimum_size"}
    return {"allowed": True, "contracts": str(contracts),
            "base_quantity": str(contracts * base_qty), "risk_budget": str(budget),
            "estimated_loss": str(contracts * loss_per_contract),
            "notional": str(contracts * notional_per_contract),
            "margin_with_fee_reserve": str(contracts * margin_per_contract)}
