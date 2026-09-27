"""Export a local Python reference for comparing a TradingView port, not proof of parity.

Usage::

    .venv/bin/python tradingview/export_reference.py INPUT.json \
        --start 2026-01-01T00:00:00Z --output OUTPUT.jsonl

INPUT uses the quant research format: {"inst": "BTC-USDT-SWAP" (or
"ETH-USDT-SWAP"), "15m": [...], "4H": [...]}. Each candle has integer UTC
millisecond opening ``ts``, numeric ``o/h/l/c/vol``, and integer ``confirm``.
``closed_series`` validates the entire supplied series before trimming: candles
must be aligned, ascending and contiguous; duplicates, gaps, invalid OHLCV, and
unclosed candles except the final candle are errors. The unclosed tail is omitted.
No missing candle is synthesized. --start must be a UTC 4H opening boundary.
Both series retain opening ts >= --start and must contain that exact first
candle; missing history at the origin is an error. The fixed left edge never
rolls forward, even beyond config.history_limit.

After 80 small and 40 big closed candles are available, each JSONL line is one
15m close. To model request.security(expression[1], lookahead_on), only 4H
candles with close <= that 15m candle's OPEN are visible. A 4H candle closing
at a 15m close becomes available at the NEXT 15m close. Missing overlap or an
early-ending 4H series is an error. Timestamps in output are UTC milliseconds;
``active_cutoff_ts`` and selected-signal ``ts`` are candle OPEN timestamps.

``raw_action/raw_reason`` report quant.strategy.evaluate before deduplication;
``action/reason`` suppress repeated eligible signal keys exactly as quant.replay
does, and ``emitted`` identifies a first eligible candidate. The selected_signal
object is null or contains type/ts/price/locked. Stop, target and net_rr can be
null when the evaluator stops at an earlier filter. Candidates are not trades:
there is no position, execution, PnL, database, network, or account interaction.
The existing chan.analyze and quant.strategy.evaluate are reused without porting
their algorithms here. Fixed history and HTF timing differ from quant.replay.

Each line also records the retained history start, config version, costs, and
row counts. --fee-rate and --slippage-rate are decimal fractions (0.0005 means
0.05%); --min-net-rr is the minimum net reward/risk ratio, at least 2. Match
these values and the source candles when comparing Pine settings. This exporter
does not assert the Pine implementation produces equal results.
An interrupted/failed export can leave a partial JSONL file; the CLI exits
nonzero and prints the error, so only a successful run is a complete reference.
"""

import argparse
from bisect import bisect_right
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

# Support the documented direct-file command as well as module imports in tests.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chan
from decision import active_signal_cutoff
from quant.config import BAR_MS, StrategyConfig
from quant.data import closed_series
from quant.strategy import evaluate


def parse_start(value):
    """Parse an explicit UTC ISO-8601 timestamp into integer milliseconds."""
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (TypeError, ValueError):
        raise ValueError("--start 必须为 UTC ISO 时间，例如 2026-01-01T00:00:00Z")
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("--start 必须明确使用 UTC 时区（Z 或 +00:00）")
    if parsed.microsecond % 1000:
        raise ValueError("--start 时间精度不得小于毫秒")
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    milliseconds = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    if milliseconds < 0:
        raise ValueError("--start 不得早于 Unix epoch")
    return milliseconds


def _prepare(dataset, start_ts, config):
    if type(start_ts) is not int or start_ts < 0:
        raise ValueError("start_ts 必须为非负整数 UTC 毫秒")
    if start_ts % BAR_MS[config.big_bar]:
        raise ValueError("固定起点必须为 UTC 4H 开盘边界（00/04/08/12/16/20 时）")
    if not isinstance(dataset, dict) or dataset.get("inst") not in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
        raise ValueError("参考导出只支持 BTC-USDT-SWAP / ETH-USDT-SWAP")
    series = {}
    for bar in (config.big_bar, config.small_bar):
        rows = dataset.get(bar)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("{} 必须为 K 线对象数组".format(bar))
        series[bar] = [row for row in closed_series(rows, bar) if row["ts"] >= start_ts]
        if not series[bar] or series[bar][0]["ts"] != start_ts:
            raise ValueError("{} 缺少固定起点的已收盘 K 线，请补齐历史".format(bar))
    big_rows, small_rows = series[config.big_bar], series[config.small_bar]
    if len(big_rows) < config.warmup_big or len(small_rows) < config.warmup_small:
        raise ValueError("固定起点之后的历史数据不足以完成 4H / 15m 预热")
    big_duration = BAR_MS[config.big_bar]
    big_closes = [row["ts"] + big_duration for row in big_rows]
    if small_rows[-1]["ts"] < big_closes[config.warmup_big - 1]:
        raise ValueError("按 4H 延后一根 15m 可见规则，大小周期预热后没有交集")
    if small_rows[-1]["ts"] >= big_closes[-1] + big_duration:
        raise ValueError("大级别数据提前结束，请补齐后再导出")
    return big_rows, small_rows, big_closes


def reference_rows(dataset, start_ts, config=None, progress=None):
    """Return a validated, lazy stream of post-warmup close snapshots.

    Validation runs immediately, before a caller opens an output file. ``progress``
    receives the emitted row count every 100 steps. ``history_limit`` is ignored:
    all analyses use the entire available prefix from the fixed retained start.
    """
    config = config or StrategyConfig()
    big_rows, small_rows, big_closes = _prepare(dataset, start_ts, config)
    return _iterate(dataset["inst"], start_ts, big_rows, small_rows, big_closes, config, progress)


def _iterate(inst, start_ts, big_rows, small_rows, big_closes, config, progress):
    emitted_keys = set()
    last_big_count, big_analysis = -1, None
    steps = 0
    for count in range(config.warmup_small, len(small_rows) + 1):
        current = small_rows[count - 1]
        big_count = bisect_right(big_closes, current["ts"])
        if big_count < config.warmup_big:
            continue
        visible_big = big_rows[:big_count]
        visible_small = small_rows[:count]
        if big_count != last_big_count:
            big_analysis = chan.analyze(visible_big, config.big_bar)
            last_big_count = big_count
        small_analysis = chan.analyze(visible_small, config.small_bar)
        outcome = evaluate(inst, visible_big, visible_small, big_analysis, small_analysis, config)
        action, reason = outcome["action"], outcome["reason"]
        first_candidate = False
        if action != "wait":
            if outcome["signal_key"] in emitted_keys:
                action, reason = "wait", "candidate_already_emitted"
            else:
                emitted_keys.add(outcome["signal_key"])
                first_candidate = True
        selected = outcome.get("signal")
        finished = [leg for leg in small_analysis["bi"] if not leg.get("unfinished")]
        cutoff_idx = active_signal_cutoff(finished)
        cutoff_ts = None
        if isinstance(cutoff_idx, int) and 0 <= cutoff_idx < len(visible_small):
            cutoff_ts = visible_small[cutoff_idx]["ts"]
        steps += 1
        yield {
            "inst": inst,
            "version": config.version,
            "reference_mode": "fixed_start_htf_previous_closed",
            "start_ts": start_ts,
            "small_history_start_ts": small_rows[0]["ts"],
            "big_history_start_ts": big_rows[0]["ts"],
            "small_open_ts": current["ts"],
            "small_closed_at": current["ts"] + BAR_MS[config.small_bar],
            "big_closed_at": big_closes[big_count - 1],
            "small_count": count,
            "big_count": big_count,
            "selected_signal": None if selected is None else {
                "type": selected["type"], "ts": selected["ts"],
                "price": selected["price"], "locked": selected.get("locked") is True,
            },
            "signal_key": outcome.get("signal_key"),
            "active_cutoff_ts": cutoff_ts,
            "raw_action": outcome["action"],
            "raw_reason": outcome["reason"],
            "action": action,
            "reason": reason,
            "emitted": first_candidate,
            "direction": outcome.get("direction"),
            "reference_price": outcome.get("reference_price"),
            "estimated_entry": outcome.get("estimated_entry"),
            "stop": outcome.get("stop"),
            "target": outcome.get("target"),
            "net_rr": outcome.get("net_rr"),
            "fee_rate": config.fee_rate,
            "slippage_rate": config.slippage_rate,
            "min_net_rr": config.min_net_rr,
        }
        if progress and steps % 100 == 0:
            progress(steps)


def main(argv=None):
    defaults = StrategyConfig()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="本地 quant 格式 JSON 数据集")
    parser.add_argument("--start", required=True, help="固定历史起点，例如 2026-01-01T00:00:00Z")
    parser.add_argument("--output", required=True, type=Path, help="JSONL 对照输出路径")
    parser.add_argument("--fee-rate", type=float, default=defaults.fee_rate,
                        help="单边费率小数，默认 0.0005（0.05%%）")
    parser.add_argument("--slippage-rate", type=float, default=defaults.slippage_rate,
                        help="单边滑点小数，默认 0.0005（0.05%%）")
    parser.add_argument("--min-net-rr", type=float, default=defaults.min_net_rr,
                        help="净盈亏比下限，默认 2，不能低于 2")
    args = parser.parse_args(argv)
    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("输入与输出路径必须不同")
        start_ts = parse_start(args.start)
        config = StrategyConfig(fee_rate=args.fee_rate, slippage_rate=args.slippage_rate,
                                min_net_rr=args.min_net_rr)
        with args.input.open(encoding="utf-8") as source:
            dataset = json.load(source)
        rows = reference_rows(dataset, start_ts, config=config, progress=lambda steps: print(
            "已导出 {} 根收盘快照".format(steps), file=sys.stderr, flush=True))
        count = 0
        with args.output.open("w", encoding="utf-8") as destination:
            for row in rows:
                destination.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                count += 1
        print("完成：{} 根参考快照 → {}".format(count, args.output), file=sys.stderr)
    except (ValueError, OSError) as exc:
        parser.exit(1, "导出失败：{}\n".format(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
