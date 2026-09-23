from dataclasses import dataclass
from math import isfinite


BAR_MS = {"15m": 15 * 60 * 1000, "4H": 4 * 60 * 60 * 1000}


@dataclass(frozen=True)
class StrategyConfig:
    version: str = "chan-b23-v3-strokes-centers"
    big_bar: str = "4H"
    small_bar: str = "15m"
    history_limit: int = 600
    warmup_big: int = 40
    warmup_small: int = 80
    # 研究假设，非账户实际费率；真实回测需替换并纳入历史资金费。
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0005
    min_net_rr: float = 2.0

    def __post_init__(self):
        if (self.big_bar, self.small_bar) != ("4H", "15m"):
            raise ValueError("第一版只支持 4H / 15m")
        if not (40 <= self.warmup_big <= self.history_limit):
            raise ValueError("warmup_big 必须在 40 与 history_limit 之间")
        if not (80 <= self.warmup_small <= self.history_limit):
            raise ValueError("warmup_small 必须在 80 与 history_limit 之间")
        for value in (self.fee_rate, self.slippage_rate):
            if not isfinite(value) or not 0 <= value < 0.1:
                raise ValueError("费用与滑点必须为 [0, 0.1) 的有限数")
        if not isfinite(self.min_net_rr) or self.min_net_rr < 2:
            raise ValueError("净盈亏比门槛不得低于 2")
