import copy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant.config import BAR_MS
from tradingview.export_reference import main, parse_start, reference_rows


def fixture(big_count=43, small_count=688):
    def rows(count, bar, close):
        return [{"ts": i * BAR_MS[bar], "o": close, "h": 120, "l": 80,
                 "c": close, "vol": 100, "confirm": 1} for i in range(count)]
    return {"inst": "ETH-USDT-SWAP", "4H": rows(big_count, "4H", 111),
            "15m": rows(small_count, "15m", 100)}


def analysis(rows, bar):
    # Mock only structure discovery; the real quant evaluator and deduplication run.
    if bar == "4H":
        return {"bi": [], "summary": {"last_bi": {"dir": "up"},
                "last_zs": {"zd": 90, "zg": 110}, "price_pos": "above_zg"}}
    return {"bi": [{"start_idx": 0, "end_idx": 1, "locked": True},
                   {"start_idx": 1, "end_idx": 8, "locked": False}],
            "bsp": [{"type": "B2", "k_idx": 1, "ts": rows[1]["ts"],
                     "price": 98, "locked": True}]}


class TradingViewReferenceTests(unittest.TestCase):
    def test_same_close_htf_is_only_visible_at_next_small_close(self):
        with patch("tradingview.export_reference.chan.analyze", side_effect=analysis) as analyze:
            rows = list(reference_rows(fixture(), 0))
        by_close = {row["small_closed_at"]: row for row in rows}
        boundary = 41 * BAR_MS["4H"]
        self.assertEqual(by_close[boundary]["big_closed_at"], boundary - BAR_MS["4H"])
        self.assertEqual(by_close[boundary + BAR_MS["15m"]]["big_closed_at"], boundary)
        self.assertTrue(all(row["big_closed_at"] <= row["small_open_ts"] for row in rows))
        self.assertEqual(rows[0]["small_count"], 641)
        big_calls = [call for call in analyze.call_args_list if call.args[1] == "4H"]
        self.assertEqual([len(call.args[0]) for call in big_calls], [40, 41, 42])

    def test_fixed_start_keeps_all_history_and_omits_unclosed_tails(self):
        data = fixture(big_count=44, small_count=705)
        data["4H"][-1]["confirm"] = 0
        data["15m"][-1]["confirm"] = 0
        start = BAR_MS["4H"]
        seen = []

        def inspect(rows, bar):
            seen.append((bar, len(rows), rows[0]["ts"], rows[-1]["ts"]))
            self.assertTrue(all(row["confirm"] == 1 and row["ts"] >= start for row in rows))
            return analysis(rows, bar)

        with patch("tradingview.export_reference.chan.analyze", side_effect=inspect):
            output = list(reference_rows(data, start))
        self.assertTrue(output)
        self.assertTrue(all(first == start for _, _, first, _ in seen))
        self.assertEqual(output[-1]["small_count"], 688)
        self.assertEqual(output[-1]["small_closed_at"], 44 * BAR_MS["4H"])
        self.assertEqual(output[-1]["big_closed_at"], 43 * BAR_MS["4H"])
        self.assertEqual(output[0]["active_cutoff_ts"], start + BAR_MS["15m"])
        self.assertEqual(output[0]["selected_signal"], {
            "type": "B2", "ts": start + BAR_MS["15m"], "price": 98, "locked": True})

    def test_raw_eligible_and_once_only_candidate_are_distinct(self):
        with patch("tradingview.export_reference.chan.analyze", side_effect=analysis):
            rows = list(reference_rows(fixture(), 0))
        self.assertTrue(all(row["raw_action"] == "long" for row in rows))
        self.assertEqual(rows[0]["action"], "long")
        self.assertTrue(rows[0]["emitted"])
        self.assertTrue(all(row["action"] == "wait" and not row["emitted"] for row in rows[1:]))
        self.assertEqual(rows[1]["reason"], "candidate_already_emitted")
        self.assertGreater(rows[0]["net_rr"], 2)

    def test_invalid_input_is_rejected_before_iteration(self):
        for case in ("gap_before_start", "missing_future_big", "unsupported_inst", "warmup"):
            with self.subTest(case=case):
                data = copy.deepcopy(fixture())
                start = 0
                if case == "gap_before_start":
                    data["15m"].pop(1)
                    start = BAR_MS["4H"]
                elif case == "missing_future_big":
                    data["4H"] = data["4H"][:40]
                elif case == "unsupported_inst":
                    data["inst"] = "SOL-USDT-SWAP"
                else:
                    start = 4 * BAR_MS["4H"]
                with self.assertRaises(ValueError):
                    reference_rows(data, start)

    def test_start_requires_explicit_utc(self):
        self.assertEqual(parse_start("1970-01-01T04:00:00Z"), BAR_MS["4H"])
        self.assertEqual(parse_start("1970-01-01T00:00:00+00:00"), 0)
        for value in ("2026-01-01", "2026-01-01T00:00:00+08:00", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_start(value)

    def test_origin_requires_4h_boundary_and_exact_first_candles(self):
        with self.assertRaisesRegex(ValueError, "UTC 4H"):
            reference_rows(fixture(), BAR_MS["15m"])
        for bar in ("4H", "15m"):
            with self.subTest(bar=bar):
                data = fixture()
                data[bar] = data[bar][1:]
                with self.assertRaisesRegex(ValueError, "缺少固定起点"):
                    reference_rows(data, 0)

    def test_cli_cost_parameters_reach_real_evaluator_and_jsonl(self):
        with tempfile.TemporaryDirectory(prefix="tv-reference-test-") as temp:
            source = Path(temp) / "input.json"
            output = Path(temp) / "reference.jsonl"
            source.write_text(json.dumps(fixture(big_count=41, small_count=641)), encoding="utf-8")
            with patch("tradingview.export_reference.chan.analyze", side_effect=analysis), redirect_stderr(io.StringIO()):
                status = main([str(source), "--start", "1970-01-01T00:00:00Z",
                               "--output", str(output), "--fee-rate", "0",
                               "--slippage-rate", "0", "--min-net-rr", "3"])
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(status, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fee_rate"], 0)
        self.assertEqual(rows[0]["slippage_rate"], 0)
        self.assertEqual(rows[0]["min_net_rr"], 3)
        self.assertEqual(rows[0]["estimated_entry"], 100)
        self.assertEqual(rows[0]["action"], "long")


if __name__ == "__main__":
    unittest.main()
