import unittest
from unittest.mock import patch

import multilevel


class DivergenceDecisionTests(unittest.TestCase):
    def payload(self, kind, struct_ok=True, buy=True):
        return {
            "candles": [{"ts": 0, "o": 101, "h": 102, "l": 99, "c": 101,
                         "vol": 1, "confirm": 1}],
            "chan": {"bi": [{"start_idx": 0, "end_idx": 10},
                            {"start_idx": 10, "end_idx": 20}],
                     "beichi": [{"kind": kind, "struct_ok": struct_ok,
                                 "dir": "down" if buy else "up", "k_idx": 20,
                                 "price": 100, "ts": 20, "score": 70,
                                 "locked": True, "area_ratio": 0.3}],
                     "beili": [], "bsp": [], "summary": {}},
        }

    def levels(self):
        return [{"name": "中枢下沿", "price": 100, "kind": "structure",
                 "testing": True, "dist_pct": -0.99, "role": "support"},
                {"name": "中枢上沿", "price": 120, "kind": "structure",
                 "testing": False, "dist_pct": 18.81, "role": "resistance"}]

    def test_observation_at_key_level_cannot_trigger_entry(self):
        for kind, structured in [("panzheng", True), ("momentum", False),
                                 ("trend", False), ("unknown", True)]:
            for buy in (True, False):
                with self.subTest(kind=kind, structured=structured, buy=buy):
                    small = self.payload(kind, structured, buy)
                    with patch("multilevel._key_levels", return_value=(self.levels(), 0.02)), \
                         patch("multilevel._brakes", return_value=None), \
                         patch("multilevel._breakout", return_value=None):
                        result = multilevel.analyze(small, small, "4H", "15m")
                    self.assertEqual(result["action"], "wait")
                    self.assertEqual(result["scenario"], "仅有观察信号")
                    signal = result["signal"]
                    self.assertFalse(signal["entry_eligible"])
                    self.assertEqual(signal["verify_level"], "中枢下沿")
                    self.assertIn("不构成趋势背驰", result["reason"])

    def test_latest_locked_point_stays_active_with_two_adjustable_strokes(self):
        small = self.payload("trend")
        small["chan"]["bi"] = [{"start_idx": 0, "end_idx": 20, "locked": True},
                                {"start_idx": 20, "end_idx": 30, "locked": False},
                                {"start_idx": 30, "end_idx": 40, "locked": False}]
        self.assertEqual(len(multilevel._small_signals(small, self.levels(), .02)), 1)
        small["chan"]["bi"][1]["locked"] = True
        self.assertEqual(multilevel._small_signals(small, self.levels(), .02), [])

    def test_trend_preserves_entry_path_and_category(self):
        small = self.payload("trend")
        big = {"chan": {"summary": {"last_bi": {"dir": "up"},
                                    "last_zs": {"zg": 99, "zd": 90},
                                    "price_pos": "above_zg"}}}
        with patch("multilevel._key_levels", return_value=(self.levels(), 0.02)), \
             patch("multilevel._brakes", return_value=None), \
             patch("multilevel._breakout", return_value=None):
            result = multilevel.analyze(big, small, "4H", "15m")
        self.assertEqual(result["action"], "long")
        self.assertEqual(result["signal"]["tag"], "趋势底背驰")
        self.assertTrue(result["signal"]["entry_eligible"])

    def test_observation_does_not_replace_valid_buy_point(self):
        small = self.payload("panzheng")
        small["chan"]["bsp"] = [{"type": "B3", "k_idx": 19, "price": 100,
                                   "ts": 19, "score": 75, "locked": True}]
        big = {"chan": {"summary": {"last_bi": {"dir": "up"},
                                    "last_zs": {"zg": 99, "zd": 90},
                                    "price_pos": "above_zg"}}}
        with patch("multilevel._key_levels", return_value=(self.levels(), 0.02)), \
             patch("multilevel._brakes", return_value=None), \
             patch("multilevel._breakout", return_value=None):
            result = multilevel.analyze(big, small, "4H", "15m")
        self.assertEqual(result["action"], "long")
        self.assertEqual(result["signal"]["tag"], "B3")


if __name__ == "__main__":
    unittest.main()
