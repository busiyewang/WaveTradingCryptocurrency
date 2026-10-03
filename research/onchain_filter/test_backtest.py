import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from research.onchain_filter import backtest as bt  # noqa: E402

H = bt.H


def bar(o, h, l, c, ts=0):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c}


def sig(i, side="long", stop=95.0, target=110.0, position=50, score=50, key="B2@1"):
    return {"inst": "X", "bar": "1H", "i": i, "t": i, "side": side, "position": position,
            "stop": stop, "target": target, "rr": 2, "type": "B2", "key": key,
            "score": score, "coverage": 1.0}


class SimulateTests(unittest.TestCase):
    def test_stop_first_when_both_hit(self):
        rows = [bar(100, 100, 100, 100), bar(100, 111, 94, 100)]
        r = bt.simulate(rows, [sig(0)], filtered=False)
        self.assertEqual(r["trades"][0]["why"], "stop")
        self.assertLess(r["trades"][0]["r"], -1)  # -1R 再扣成本

    def test_gap_through_stop_fills_at_open(self):
        rows = [bar(100, 100, 100, 100), bar(100, 101, 99, 100), bar(90, 91, 89, 90)]
        r = bt.simulate(rows, [sig(0)], filtered=False)
        self.assertEqual(r["trades"][0]["why"], "gap_stop")
        self.assertAlmostEqual(r["trades"][0]["r"], -2 - bt.COST * 190 / 5, places=3)

    def test_target_and_equity(self):
        rows = [bar(100, 100, 100, 100), bar(100, 111, 99, 110)]
        r = bt.simulate(rows, [sig(0)], filtered=False)
        self.assertEqual(r["trades"][0]["why"], "target")
        self.assertGreater(r["equity"], 1)

    def test_time_stop(self):
        rows = [bar(100, 100, 100, 100)] + [bar(100, 101, 99, 100.5)] * (bt.TIME_STOP + 2)
        r = bt.simulate(rows, [sig(0)], filtered=False)
        self.assertEqual(r["trades"][0]["why"], "time")

    def test_same_signal_only_once(self):
        rows = [bar(100, 100, 100, 100), bar(100, 111, 99, 110), bar(100, 111, 99, 110), bar(100, 111, 99, 110)]
        r = bt.simulate(rows, [sig(0), sig(1), sig(2)], filtered=False)
        self.assertEqual(len(r["trades"]), 1)

    def test_filter_skips_strong_headwind_and_reports_it(self):
        rows = [bar(100, 100, 100, 100), bar(100, 111, 99, 110)]
        r = bt.simulate(rows, [sig(0, position=50, score=10)], filtered=True)
        self.assertEqual(r["trades"], [])
        self.assertEqual(len(r["skipped"]), 1)

    def test_filter_downgrades_size(self):
        rows = [bar(100, 100, 100, 100), bar(100, 111, 99, 110)]
        r = bt.simulate(rows, [sig(0, position=70, score=30)], filtered=True)
        self.assertEqual(r["trades"][0]["position_used"], 50)

    def test_skipped_signal_can_enter_later_when_score_recovers(self):
        rows = [bar(100, 100, 100, 100), bar(100, 101, 99, 100), bar(100, 111, 99, 110)]
        r = bt.simulate(rows, [sig(0, score=10), sig(1, score=70)], filtered=True)
        self.assertEqual(len(r["trades"]), 1)
        self.assertEqual(r["skipped"], [])


class ScoreAtTests(unittest.TestCase):
    def data(self):
        oi = [[k * H, 100.0 + k, (100.0 + k) * 10] for k in range(0, 60)]
        ls = [[k * H, 1.0 + k / 100] for k in range(0, 60)]
        tk = [[k * H, 2.0, 1.0] for k in range(0, 60)]
        return {"oi": oi, "ls": ls, "taker": tk, "funding": [[10 * H, 0.0001]]}

    def test_uses_only_known_data(self):
        s = bt.ScoreAt(self.data())
        # t=30H:多空比/主动买卖 1H 区间需结束才可用 → 最新可用的是 ts=29H 的区间
        rows = {r["key"] for r in s.rows(30 * H)}
        self.assertEqual(rows, {"funding", "oi_price", "ls_ratio", "taker"})
        self.assertEqual(bt.ScoreAt(self.data()).rows(H // 2), [])  # 首个 1H 区间尚未结束:都不可用
        early = {r["key"] for r in s.rows(5 * H)}
        self.assertNotIn("funding", early)   # 首次结算在 10H
        self.assertNotIn("oi_price", early)  # 需要 24h 前的持仓

    def test_full_coverage_ratio(self):
        _, cov = bt.ScoreAt(self.data()).score(40 * H)
        self.assertEqual(cov, 1.0)


if __name__ == "__main__":
    unittest.main()
