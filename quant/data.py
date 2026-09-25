"""OKX 风格 K 线输入；ts 是开盘时间，内部统一 UTC 毫秒。"""
import math

from .config import BAR_MS


def closed_series(rows, bar):
    duration = BAR_MS[bar]
    result = []
    previous = None
    saw_unclosed = False
    for source in rows:
        ts = source.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0 or ts % duration:
            raise ValueError("ts 必须为按周期对齐的非负整数毫秒")
        if previous is not None and ts != previous + duration:
            raise ValueError("K 线必须升序且连续，不得重复或缺失")
        previous = ts
        confirm = source.get("confirm")
        if type(confirm) is not int or confirm not in (0, 1):
            raise ValueError("confirm 必须明确为整数 0 或 1")
        if saw_unclosed:
            raise ValueError("未收盘 K 线只能是最后一根")
        saw_unclosed = confirm == 0
        row = {"ts": ts, "confirm": confirm}
        for field in ("o", "h", "l", "c", "vol"):
            value = source.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("OHLCV 必须是数值")
            if not math.isfinite(value) or value < 0 or (field != "vol" and value == 0):
                raise ValueError("OHLC 必须为正有限数，成交量必须非负有限")
            row[field] = float(value)
        if row["l"] > min(row["o"], row["c"]) or row["h"] < max(row["o"], row["c"]):
            raise ValueError("OHLC 高低区间不合法")
        if confirm == 1:
            result.append(row)
    if not result:
        raise ValueError("没有已收盘 K 线")
    return result
