import copy
from decimal import Decimal
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from quant.config import StrategyConfig
from quant.data import closed_series
from quant.demo import dataset
from quant.replay import run_replay
from quant.risk import size_linear_contract
from quant.storage import ResearchStore
from quant.strategy import evaluate, signal_key


def structural_replay_dataset():
    """Real OHLCV with two descending centers, a weaker final low, and a holding pullback.

    The first 512 candles warm MACD up without adding unrelated strokes. Ten
    candles per leg make confirmed strokes visible after the replay's 4H warmup.
    This fixture is local to the causality test; the shared demo stays unchanged.
    """
    points = [140, 120, 135, 122, 134, 121, 130, 100,
              110, 101, 109, 102, 108, 101, 106, 90, 105, 95, 110, 103, 112]
    prices = [140 - (512 - i) * 0.001 for i in range(512)]
    for start, end in zip(points, points[1:]):
        prices.extend(start + (end - start) * j / 10 for j in range(10))
    prices.append(points[-1])
    while len(prices) < 768:
        prices.append(prices[-1] + 0.01)
    rows = []
    for index, price in enumerate(prices):
        close = price * 10 + 600
        opening = rows[-1]["c"] if rows else close
        rows.append({"ts": index * 900000, "o": opening, "c": close,
                     "h": max(opening, close) + 0.1, "l": min(opening, close) - 0.1,
                     "vol": 100, "confirm": 1})
    big = []
    for index in range(0, len(rows), 16):
        group = rows[index:index + 16]
        big.append({"ts": group[0]["ts"], "o": group[0]["o"], "c": group[-1]["c"],
                    "h": max(c["h"] for c in group), "l": min(c["l"] for c in group),
                    "vol": sum(c["vol"] for c in group), "confirm": 1})
    return {"inst": "ETH-USDT-SWAP", "15m": rows, "4H": big}


class DataTests(unittest.TestCase):
    def test_unclosed_tail_excluded(self):
        rows = dataset()["15m"][:3]
        rows[-1]["confirm"] = 0
        self.assertEqual(len(closed_series(rows, "15m")), 2)

    def test_reject_bad_market_data(self):
        for change in ("duplicate", "gap", "nan", "bad_ohlc", "unclosed_middle"):
            with self.subTest(change=change):
                rows = dataset()["15m"][:3]
                if change == "duplicate":
                    rows[1]["ts"] = rows[0]["ts"]
                elif change == "gap":
                    rows.pop(1)
                elif change == "nan":
                    rows[1]["c"] = float("nan")
                elif change == "bad_ohlc":
                    rows[1]["h"] = 1
                else:
                    rows[1]["confirm"] = 0
                with self.assertRaises(ValueError):
                    closed_series(rows, "15m")


class StructureIntegrationTests(unittest.TestCase):
    def test_signal_and_segment_events_are_available_at_reported_close(self):
        import chan

        rows = structural_replay_dataset()["15m"]
        final = chan.analyze(rows, "15m")
        cached = {}
        checked = 0
        for collection in ("bi", "zhongshu", "zhongshu_display", "beichi", "beili", "bsp", "xianduan"):
            for record in final[collection]:
                for key in ("known", "span_known", "locked"):
                    index = record.get(key + "_idx")
                    if index is not None:
                        self.assertEqual(record[key + "_at"], rows[index]["ts"] + 900000)
                if collection not in ("beichi", "beili", "bsp", "xianduan") or not record["locked"]:
                    continue
                at = record["locked_idx"]
                if at not in cached:
                    cached[at] = chan.analyze(rows[:at + 1], "15m")
                matches = [r for r in cached[at][collection]
                           if r.get("k_idx", r.get("end_idx")) == record.get("k_idx", record.get("end_idx"))
                           and r.get("type", r.get("dir")) == record.get("type", record.get("dir"))]
                self.assertTrue(matches, (collection, at))
                self.assertTrue(matches[0]["locked"], (collection, at))
                self.assertEqual(matches[0].get("kind"), record.get("kind"))
                checked += 1
        self.assertGreaterEqual(checked, 4)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.config = StrategyConfig()
        self.big = {"bi": [], "summary": {"last_bi": {"dir": "up"},
                    "last_zs": {"zd": 90, "zg": 110}, "price_pos": "above_zg"}}
        self.small = {"bi": [{"end_idx": 1}, {"end_idx": 8}],
                      "bsp": [{"type": "B2", "k_idx": 1, "ts": 900000,
                               "price": 98, "locked": True}]}
        self.big_rows = [{"h": 120, "l": 80, "c": 111}] * 20
        self.small_rows = [{"c": 100}]

    def outcome(self):
        return evaluate("ETH-USDT-SWAP", self.big_rows, self.small_rows,
                        self.big, self.small, self.config)

    def test_valid_candidate_and_costs(self):
        result = self.outcome()
        self.assertEqual(result["action"], "long")
        self.assertGreater(result["estimated_entry"], 100)
        self.assertLess(result["net_rr"], (110 - 100) / (100 - 98 * 0.998))

    def test_newly_locked_point_survives_two_later_strokes_then_expires(self):
        self.small["bi"] = [{"end_idx": 1, "locked": True},
                            {"end_idx": 8, "locked": False},
                            {"end_idx": 14, "locked": False}]
        self.assertEqual(self.outcome()["action"], "long")
        self.small["bi"][1]["locked"] = True
        self.small["bi"].append({"end_idx": 20, "locked": False})
        self.assertEqual(self.outcome()["reason"], "no_active_signal")

    def test_hard_blocks(self):
        original_big, original_small = copy.deepcopy(self.big), copy.deepcopy(self.small)
        for case, expected in [("unlocked", "signal_unlocked"), ("missing_lock", "signal_unlocked"),
                               ("neutral", "direction_neutral_or_conflict"),
                               ("target", "no_structure_target"), ("invalid", "structure_invalidated"),
                               ("rr", "net_rr_below_minimum")]:
            with self.subTest(case=case):
                self.big, self.small = copy.deepcopy(original_big), copy.deepcopy(original_small)
                if case == "unlocked":
                    self.small["bsp"][0]["locked"] = False
                elif case == "missing_lock":
                    self.small["bsp"][0].pop("locked")
                elif case == "neutral":
                    self.big["summary"]["price_pos"] = "inside"
                elif case == "target":
                    self.big["summary"]["last_zs"]["zg"] = 99
                elif case == "invalid":
                    self.small["bsp"][0]["price"] = 102
                else:
                    self.big["summary"]["last_zs"]["zg"] = 101
                self.assertEqual(self.outcome()["reason"], expected)
                self.assertEqual(self.outcome()["action"], "wait")

    def test_short_symmetry(self):
        self.big["summary"].update(last_bi={"dir": "down"}, price_pos="below_zd")
        self.small["bsp"][0].update(type="S3", price=102)
        result = self.outcome()
        self.assertEqual(result["action"], "short")
        self.assertGreater(result["stop"], result["estimated_entry"])
        self.assertLess(result["target"], result["estimated_entry"])

    def test_signal_key_ignores_window_index(self):
        signal = self.small["bsp"][0]
        key = signal_key("ETH-USDT-SWAP", "15m", signal)
        signal["k_idx"] = 99
        self.assertEqual(key, signal_key("ETH-USDT-SWAP", "15m", signal))
        signal["ts"] += 900000
        self.assertNotEqual(key, signal_key("ETH-USDT-SWAP", "15m", signal))


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(side="long", equity="10000", free_margin="10000", entry="2000",
                         stop="1960", base_per_contract="0.1", lot_size="0.01",
                         min_size="0.01", max_size="10000")

    def test_budget_and_rounding(self):
        result = size_linear_contract(**self.args)
        self.assertTrue(result["allowed"])
        self.assertLessEqual(Decimal(result["estimated_loss"]), Decimal("50"))
        self.assertEqual(Decimal(result["contracts"]) % Decimal("0.01"), 0)
        self.assertLess(Decimal(result["base_quantity"]), Decimal("1.25"))

    def test_leverage_does_not_multiply_risk_quantity(self):
        low = size_linear_contract(**self.args, leverage="3")
        high = size_linear_contract(**self.args, leverage="10")
        self.assertEqual(low["contracts"], high["contracts"])
        self.assertLess(Decimal(high["margin_with_fee_reserve"]), Decimal(low["margin_with_fee_reserve"]))

    def test_limits_and_invalid_stops(self):
        self.assertFalse(size_linear_contract(**self.args, open_risk="100")["allowed"])
        self.assertFalse(size_linear_contract(**self.args, open_positions=2)["allowed"])
        self.assertFalse(size_linear_contract(**dict(self.args, free_margin="0"))["allowed"])
        with self.assertRaises(ValueError):
            size_linear_contract(**dict(self.args, stop="2100"))
        with self.assertRaises(ValueError):
            size_linear_contract(**dict(self.args, equity="NaN"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.store = ResearchStore("sqlite:///:memory:")
        self.store.migrate()
        self.addCleanup(self.store.close)

    def test_migration_repeat_and_step_atomicity(self):
        self.store.migrate()
        self.store.start_run("r", {}, "ETH-USDT-SWAP", {})
        outcome = {"action": "wait", "reason": "test"}
        self.store.record_step("r", 10, [], outcome)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_step("r", 10, [("key", {"ts": 1, "locked": True})], outcome)
        self.assertEqual(self.store.execute("SELECT COUNT(*) FROM quant_signal_observations").fetchone()[0], 0)

    def test_first_observed_not_extreme_and_preserved(self):
        self.store.start_run("r", {}, "ETH-USDT-SWAP", {})
        for at, locked in [(10, False), (20, True), (30, True)]:
            self.store.record_step("r", at, [("key", {"ts": 1, "locked": locked})],
                                   {"action": "wait", "reason": "test"})
        timing = self.store.report("r")["signal_timings"][0]
        self.assertEqual((timing["extreme_at"], timing["first_observed_at"], timing["first_locked_observed_at"]),
                         (1, 10, 20))


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.store = ResearchStore("sqlite:///:memory:")
        self.store.migrate()
        self.addCleanup(self.store.close)

    def events(self, run_id):
        return self.store.execute("SELECT observed_at, snapshot_json FROM quant_evaluations WHERE run_id = ? ORDER BY observed_at",
                                  (run_id,)).fetchall()

    def test_real_algorithm_prefix_invariance(self):
        import chan

        full = structural_replay_dataset()
        structure = chan.analyze(full["15m"], "15m")
        self.assertTrue(any(s["kind"] == "trend" for s in structure["beichi"]))
        self.assertEqual({s["type"] for s in structure["bsp"]}, {"B1", "B2", "S3"})
        prefix = {"inst": full["inst"], "15m": full["15m"][:704], "4H": full["4H"][:44]}
        short = run_replay(prefix, self.store)
        long = run_replay(full, self.store)
        early = self.events(short["run_id"])
        extended = self.events(long["run_id"])
        self.assertGreater(len(early), 0)
        self.assertEqual(early, extended[:len(early)])
        self.assertFalse(long["summary"]["pnl_available"])
        for at, snapshot in extended:
            event = json.loads(snapshot)
            self.assertLessEqual(event["big_closed_at"], at)
        self.assertTrue(long["signal_timings"])
        for timing in long["signal_timings"]:
            self.assertGreater(timing["first_observed_at"], timing["extreme_at"])

    def test_only_closed_prefix_and_candidate_dedup(self):
        seen = []
        def analysis(rows, bar):
            seen.append((bar, rows[-1]["ts"]))
            return {"bsp": []}
        fixed = {"action": "long", "reason": "eligible_research_candidate", "signal_key": "same"}
        with patch("quant.replay.chan.analyze", side_effect=analysis), patch("quant.replay.evaluate", side_effect=lambda *a: dict(fixed)):
            report = run_replay(dataset(), self.store)
        self.assertEqual(report["summary"]["candidates"], {"long": 1})
        self.assertEqual(report["summary"]["reasons"]["candidate_already_emitted"], 128)
        # 第一决策 40 根 4H 收盘时，只能看到第 640 根 15m，不能访问第 641 根。
        self.assertEqual(seen[0], ("4H", 39 * 14400000))
        self.assertEqual(seen[1], ("15m", 639 * 900000))

    def test_stale_big_data_rejected(self):
        source = dataset()
        source["4H"] = source["4H"][:-1]
        with self.assertRaises(ValueError):
            run_replay(source, self.store)

    def test_failed_run_is_recorded(self):
        with patch("quant.replay.chan.analyze", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                run_replay(dataset(), self.store)
        self.assertEqual(self.store.execute("SELECT status FROM quant_runs").fetchone()[0], "failed")


@unittest.skipUnless(os.environ.get("QUANT_TEST_DATABASE_URL"), "未提供专用 MySQL 测试库")
class MySQLIntegrationTests(unittest.TestCase):
    def test_migration_roundtrip_and_rollback(self):
        # 仅针对用户明确配置的独立测试库；数据保留供检查，不删除任何表。
        store = ResearchStore(os.environ["QUANT_TEST_DATABASE_URL"])
        self.addCleanup(store.close)
        self.assertTrue(store.mysql)
        store.migrate()
        store.migrate()
        run_id = uuid.uuid4().hex
        store.start_run(run_id, {"test": "中文与事务"}, "ETH-USDT-SWAP", {})
        outcome = {"action": "wait", "reason": "test"}
        store.record_step(run_id, 10, [], outcome)
        with self.assertRaises(Exception):
            store.record_step(run_id, 10, [("key", {"ts": 1, "locked": True})], outcome)
        self.assertEqual(store.report(run_id)["signal_timings"], [])
        store.finish_run(run_id, {"test": True})
        self.assertEqual(store.report(run_id)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
