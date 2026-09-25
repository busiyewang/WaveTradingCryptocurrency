"""逐根信号回放。没有撮合/PnL 模型，不能把候选数解释成成交数。"""
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import uuid

import chan

from .config import BAR_MS, StrategyConfig
from .data import closed_series
from .storage import encode
from .strategy import evaluate, signal_key


def source_fingerprint():
    root = Path(__file__).resolve().parent.parent
    paths = [root / name for name in ("chan.py", "chan_strokes.py", "chan_centers.py",
                                     "chan_segments.py", "indicators.py", "decision.py")]
    paths += sorted((root / "quant").glob("*.py"))
    paths += sorted((root / "quant" / "migrations").glob("*.sql"))
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in paths}


def run_replay(dataset, store, config=None, progress=None):
    config = config or StrategyConfig()
    inst = dataset.get("inst")
    if inst not in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
        raise ValueError("第一版研究范围为 BTC-USDT-SWAP / ETH-USDT-SWAP")
    series = {bar: closed_series(dataset[bar], bar) for bar in (config.big_bar, config.small_bar)}
    big_rows, small_rows = series[config.big_bar], series[config.small_bar]
    if len(big_rows) < config.warmup_big or len(small_rows) < config.warmup_small:
        raise ValueError("历史数据不足以完成预热")
    big_duration, small_duration = BAR_MS[config.big_bar], BAR_MS[config.small_bar]
    big_closes = [c["ts"] + big_duration for c in big_rows]
    small_last_close = small_rows[-1]["ts"] + small_duration
    if small_last_close < big_closes[config.warmup_big - 1]:
        raise ValueError("大小周期的预热后时间范围没有交集")
    if small_last_close >= big_closes[-1] + big_duration:
        raise ValueError("大级别数据提前结束，请补齐后再回放")

    run_id = uuid.uuid4().hex
    manifest = {"kind": "signal_replay", "inst": inst, "config": asdict(config),
                "data_sha256": sha256(encode(series).encode()).hexdigest(),
                "source_sha256": source_fingerprint(),
                "time_semantics": "UTC milliseconds; decision_at = small candle close",
                "execution_model": "none; candidates are not fills; funding excluded"}
    store.start_run(run_id, manifest, inst, series)
    reasons = Counter()
    candidates = Counter()
    emitted = set()
    steps = 0
    last_big_count, big_analysis = -1, None
    try:
        for count in range(config.warmup_small, len(small_rows) + 1):
            at = small_rows[count - 1]["ts"] + small_duration
            big_count = bisect_right(big_closes, at)
            if big_count < config.warmup_big:
                continue
            visible_big = big_rows[max(0, big_count - config.history_limit):big_count]
            visible_small = small_rows[max(0, count - config.history_limit):count]
            if big_count != last_big_count:
                big_analysis = chan.analyze(visible_big, config.big_bar)
                last_big_count = big_count
            small_analysis = chan.analyze(visible_small, config.small_bar)
            outcome = evaluate(inst, visible_big, visible_small, big_analysis, small_analysis, config)
            key = outcome.get("signal_key")
            if outcome["action"] != "wait":
                if key in emitted:
                    outcome.update(action="wait", reason="candidate_already_emitted")
                else:
                    emitted.add(key)
                    candidates[outcome["action"]] += 1
            # 同一极值/类型可能从多个中枢路径产出，只记录同一时刻的一个快照。
            observations = {signal_key(inst, config.small_bar, s): s for s in small_analysis["bsp"]}
            outcome.update(observed_at=at, eligible_after=at,
                           big_closed_at=big_closes[big_count - 1],
                           small_closed_at=at)
            store.record_step(run_id, at, observations.items(), outcome)
            reasons[outcome["reason"]] += 1
            steps += 1
            if progress and steps % 100 == 0:
                progress(steps)
        summary = {"steps": steps, "candidates": dict(candidates), "reasons": dict(reasons),
                   "pnl_available": False,
                   "note": "只回放信号与过滤原因；首次观察/锁定是回放范围内首次看到的时间。"}
        store.finish_run(run_id, summary)
    except BaseException as exc:
        store.finish_run(run_id, {"steps": steps, "error_type": type(exc).__name__}, failed=True)
        raise
    return store.report(run_id)
