import unittest
from unittest.mock import patch

import onchain


class ScoreRuleTests(unittest.TestCase):
    def test_funding_is_contrarian(self):
        hot, _ = onchain.score_funding(0.0005)     # 0.05%/8h 过热
        neg, _ = onchain.score_funding(-0.0002)    # 负费率
        normal, _ = onchain.score_funding(0.0001)  # 基准 0.01%
        self.assertLess(hot, 0.5)
        self.assertGreater(neg, 0.5)
        self.assertEqual(normal, 0.5)

    def test_oi_price_quadrants(self):
        self.assertGreater(onchain.score_oi_price(2, 3)[0], 0.5)   # 价↑仓↑
        self.assertLess(onchain.score_oi_price(2, -3)[0], 0.5)     # 价↑仓↓
        self.assertLess(onchain.score_oi_price(-2, 3)[0], 0.5)     # 价↓仓↑
        self.assertGreater(onchain.score_oi_price(-2, -3)[0], 0.5)  # 价↓仓↓
        self.assertEqual(onchain.score_oi_price(0.1, 5)[0], 0.5)   # 价格几乎没动

    def test_netflow_outflow_is_bullish(self):
        self.assertGreater(onchain.score_netflow(-3000, 1000)[0], 0.5)
        self.assertLess(onchain.score_netflow(3000, 1000)[0], 0.5)
        self.assertEqual(onchain.score_netflow(100, 0)[0], 0.5)

    def test_cost_line_band(self):
        self.assertEqual(onchain.score_cost_line(101, 100)[0], 0.5)
        self.assertGreater(onchain.score_cost_line(110, 100)[0], 0.5)
        self.assertLess(onchain.score_cost_line(90, 100)[0], 0.5)

    def test_bull_scores_stay_in_range(self):
        for b in (onchain.score_ls_ratio(0)[0], onchain.score_ls_ratio(1)[0],
                  onchain.score_taker(10)[0], onchain.score_taker(0)[0],
                  onchain.score_usdt_ex(50)[0], onchain.score_usdt_ex(-50)[0]):
            self.assertTrue(0 <= b <= 1)


class AggregateTests(unittest.TestCase):
    def row(self, key, bull, stale=False, status="ok"):
        return {"key": key, "status": status, "bull": bull, "stale": stale,
                "weight": onchain.WEIGHTS[key]}

    def test_long_short_are_complementary(self):
        s = onchain.aggregate([self.row("funding", 0.2), self.row("netflow", 0.4)])
        self.assertEqual(s["long"] + s["short"], 100)
        self.assertEqual(s["long"], 30)
        self.assertEqual(s["long_level"], "逆风")
        self.assertEqual(s["short_level"], "顺风")

    def test_unavailable_rows_are_skipped_and_counted(self):
        rows = [self.row("funding", 0.8), {"key": "nupl", "status": "na",
                                          "weight": onchain.WEIGHTS["nupl"]}]
        s = onchain.aggregate(rows)
        self.assertEqual(s["long"], 80)
        self.assertEqual(s["coverage"], "1/2")

    def test_unsupported_rows_excluded_from_coverage(self):
        rows = [self.row("funding", 0.8),
                {"key": "nupl", "status": "na", "reason": "Glassnode 不支持 SOL",
                 "weight": onchain.WEIGHTS["nupl"]},
                {"key": "mvrv_z", "status": "na", "reason": "加载中",
                 "weight": onchain.WEIGHTS["mvrv_z"]}]
        self.assertEqual(onchain.aggregate(rows)["coverage"], "1/2")

    def test_stale_rows_weigh_half(self):
        s = onchain.aggregate([self.row("funding", 1.0), self.row("oi_price", 0.0, stale=True)])
        self.assertEqual(s["long"], 67)  # 1.0 / (1.0 + 0.5)

    def test_no_data(self):
        s = onchain.aggregate([])
        self.assertIsNone(s["long"])

    def test_levels(self):
        self.assertEqual(onchain.level_name(60), "顺风")
        self.assertEqual(onchain.level_name(59), "中性")
        self.assertEqual(onchain.level_name(39), "逆风")
        self.assertEqual(onchain.level_name(19), "强逆风")


class HistoryCacheTests(unittest.TestCase):
    """_okx_hist:首页按 TTL 刷新、向前分页回补、到底后不再请求。"""

    def setUp(self):
        onchain._hist.clear()
        self.sleep = patch("onchain.time.sleep", lambda *_: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def fake_pages(self, total, step=10):
        # 共 total 个点,ts = 0, step, 2*step...;每页 3 个,倒序
        data = list(range(0, total * step, step))
        calls = []

        def page(_sess, cursor):
            calls.append(cursor)
            older = [t for t in data if cursor is None or t < cursor]
            return [(t, float(t)) for t in sorted(older, reverse=True)[:3]]
        return page, calls

    def test_backfills_until_since(self):
        page, calls = self.fake_pages(10)
        pts, _ = onchain._okx_hist("k", page, since_ms=40, max_pages=10)
        self.assertLessEqual(pts[0][0], 40)
        self.assertEqual([t for t, _ in pts], sorted(t for t, _ in pts))

    def test_stops_when_history_exhausted(self):
        page, calls = self.fake_pages(5)
        onchain._okx_hist("k", page, since_ms=-1, max_pages=10)
        n = len(calls)
        onchain._okx_hist("k", page, since_ms=-1, max_pages=10)
        self.assertEqual(len(calls), n)  # 已到底且首页未过期:不再请求

    def test_respects_max_pages(self):
        page, calls = self.fake_pages(100)
        onchain._okx_hist("k", page, since_ms=0, max_pages=2)
        self.assertEqual(len(calls), 3)  # 首页 + 2 页回补

    def test_first_page_refreshes_after_ttl(self):
        page, calls = self.fake_pages(3)
        onchain._okx_hist("k", page, since_ms=10 ** 12, max_pages=1)
        onchain._hist["k"]["at"] -= onchain.OKX_TTL + 1
        onchain._okx_hist("k", page, since_ms=10 ** 12, max_pages=1)
        self.assertEqual(calls, [None, None])



class PositionFilterTests(unittest.TestCase):
    def test_tailwind_never_increases(self):
        r = onchain.adjust_position(50, 90, 1.0)
        self.assertEqual((r["position"], r["steps"]), (50, 0))
        self.assertIn("不加仓", r["note"])

    def test_neutral_unchanged(self):
        self.assertEqual(onchain.adjust_position(70, 40, 1.0)["position"], 70)

    def test_headwind_one_step(self):
        for before, after in [(100, 70), (70, 50), (50, 30), (30, 0)]:
            self.assertEqual(onchain.adjust_position(before, 39, 1.0)["position"], after)

    def test_strong_headwind_two_steps(self):
        for before, after in [(100, 50), (70, 30), (50, 0), (30, 0)]:
            self.assertEqual(onchain.adjust_position(before, 19, 1.0)["position"], after)

    def test_low_coverage_or_missing_score_no_change(self):
        self.assertEqual(onchain.adjust_position(70, 5, 0.4)["steps"], 0)
        self.assertEqual(onchain.adjust_position(70, None, 1.0)["steps"], 0)

    def decision(self, pos=50):
        return {"action": "long", "position": pos, "q": 0.6, "rr": 2.5, "entry": 100,
                "stop": 95, "stop_name": "结构止损", "targets": [("ZG", 112)],
                "signal": {"type": "B2", "price": 96}, "warnings": [], "dir": "long",
                "dir_level": "1D", "dir_desc": "", "rules": ""}

    def fake_build(self, long_score, coverage="8/11"):
        return {"score": {"long": long_score, "short": 100 - long_score, "coverage": coverage},
                "gn_pending": False}

    def test_apply_downgrades_and_warns(self):
        with patch("onchain.build", return_value=self.fake_build(30)):
            d = onchain.apply_to_decision(self.decision(70), "ETH-USDT-SWAP")
        self.assertEqual(d["action"], "long")
        self.assertEqual(d["position"], 50)
        self.assertEqual(d["onchain"]["position_before"], 70)
        self.assertTrue(any("降一档" in w for w in d["warnings"]))

    def test_apply_to_zero_becomes_wait(self):
        with patch("onchain.build", return_value=self.fake_build(10)):
            d = onchain.apply_to_decision(self.decision(50), "ETH-USDT-SWAP")
        self.assertEqual(d["action"], "wait")
        self.assertNotIn("position", d)
        self.assertIn("暂停", d["reason"])
        self.assertEqual(d["onchain"]["position_after"], 0)

    def test_short_uses_short_score(self):
        dec = dict(self.decision(70), action="short")
        with patch("onchain.build", return_value=self.fake_build(80)):  # 对做空 20
            d = onchain.apply_to_decision(dec, "ETH-USDT-SWAP")
        self.assertEqual(d["position"], 50)

    def test_build_failure_leaves_decision_untouched(self):
        with patch("onchain.build", side_effect=RuntimeError("boom")):
            d = onchain.apply_to_decision(self.decision(70), "ETH-USDT-SWAP")
        self.assertEqual(d["position"], 70)
        self.assertFalse(d["onchain"]["applied"])

    def test_wait_decision_only_annotated(self):
        with patch("onchain.build", return_value=self.fake_build(10)):
            d = onchain.apply_to_decision({"action": "wait", "reason": "x"}, "ETH-USDT-SWAP")
        self.assertEqual(d["action"], "wait")
        self.assertEqual(d["reason"], "x")


if __name__ == "__main__":
    unittest.main()
