"""确定性的合成行情，用于验证工程流程，不代表真实市场表现。"""
import math

from .config import BAR_MS


def dataset():
    rows = []
    duration = BAR_MS["15m"]
    for index in range(16 * 48):
        def price(t):
            return 2000 + t * 0.03 + 40 * math.sin(t / 13) + 15 * math.sin(t / 4)
        opening, close = price(index), price(index + 1)
        rows.append({"ts": index * duration, "o": opening, "h": max(opening, close) + 2,
                     "l": min(opening, close) - 2, "c": close,
                     "vol": 100 + 20 * math.sin(index / 7), "confirm": 1})
    big = []
    for index in range(0, len(rows), 16):
        group = rows[index:index + 16]
        big.append({"ts": group[0]["ts"], "o": group[0]["o"], "h": max(c["h"] for c in group),
                    "l": min(c["l"] for c in group), "c": group[-1]["c"],
                    "vol": sum(c["vol"] for c in group), "confirm": 1})
    return {"inst": "ETH-USDT-SWAP", "15m": rows, "4H": big}
