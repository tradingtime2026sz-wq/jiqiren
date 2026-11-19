"""收盘对账的纯函数测试：期权代码、卡面结果、三验机器式、按卡上进法的期权倍数。"""
import unittest

from tiger_review import recon


def bar(hm, o, h, l, c):
    return {"time_key": f"2026-11-09 {hm}:00", "open": o, "high": h, "low": l, "close": c, "volume": 1}


class ReconTest(unittest.TestCase):
    def test_option_code(self):
        self.assertEqual(recon.option_code("QQQ", "2026-11-09", "CALL", 662.0), "US.QQQ261109C662000")
        self.assertEqual(recon.option_code("IREN", "2026-11-06", "PUT", 20.5), "US.IREN261106P20500")

    def test_face_result_stops_at_flat_by(self):
        rows = [bar("09:31", 4.0, 4.2, 3.9, 4.1), bar("12:00", 8.0, 9.0, 7.5, 8.5), bar("15:30", 10.0, 10.0, 9.0, 10.0),
                bar("15:45", 30.0, 40.0, 30.0, 40.0)]
        f = recon.face_result(rows, "15:30")
        self.assertEqual((f["open_px"], f["hold_mult"], f["best_mult"], f["best_at"]), (4.0, 2.5, 2.5, "15:30"))

    def test_checks_from_text(self):
        facts = {"QQQ": {"close": 679.35, "low": 660.0, "prev_close": 656.1},
                 "AMD": {"close": 250.0, "prev_close": 240.0}, "AVGO": {"close": 300.0, "prev_close": 310.0}}
        out = recon.machine_checks(["QQQ 收盘守住 $647.05", "AMD、AVGO 收红", "费半不创新低"], None, facts, {})
        self.assertEqual([x["verdict"] for x in out], ["成立", "不成立", "判不了"])

    def test_checks_from_rules(self):
        facts = {"SOXX": {"low": 301.0, "close": 305.0}}
        rules = [[{"sym": "SOXX", "field": "low", "op": ">=", "ref": 300.0}]]
        out = recon.machine_checks(["费半不创新低"], rules, facts, {})
        self.assertEqual(out[0]["verdict"], "成立")
        out = recon.machine_checks(["费半不创新低"], [[{"sym": "SOXX", "field": "low", "op": ">=", "ref": 302.0}]], facts, {})
        self.assertEqual(out[0]["verdict"], "不成立")

    def test_market_line_scores_direction_scripts_and_range(self):
        rows = [bar("09:31", 661.0, 662.8, 660.0, 660.5), bar("09:40", 657.0, 657.2, 655.9, 656.0),
                bar("10:00", 655.0, 655.5, 653.0, 654.0), bar("15:30", 659.0, 659.6, 658.8, 659.5),
                bar("16:00", 659.5, 659.9, 659.0, 659.5)]
        odte = {"bias": "偏多", "weather": {"qqq_close": 656.95}, "implied": {"pct": 0.0143, "spot": 661.49},
                "scripts": {"a": {"name": "守住", "level": 656.95, "dir": "守住", "by": "10:00", "side": "Call"},
                            "b": {"name": "吐回", "level": 656.95, "dir": "跌破", "by": "10:00", "side": "不做"}}}
        m = recon.market_line(odte, {"QQQ": rows}, "15:30")
        self.assertEqual((m["dir_right"], m["script_hit"], m["hit_at"], m["script_side"]), (False, "B", "09:40", "不做"))
        self.assertIsNone(m["script_right"])                      # 剧本写不做，不记对错
        self.assertEqual(m["after_move"], round(659.5 / 656.0 - 1, 4))
        self.assertEqual(m["move_vs_implied"], round(abs(659.5 / 661.49 - 1) / 0.0143, 2))
        self.assertIn("剧本 B 在 09:40 触发", recon.market_text(m, "15:30"))

    def test_market_line_without_scripts_or_bars(self):
        rows = [bar("09:31", 100.0, 101.0, 99.0, 100.5), bar("16:00", 101.0, 102.0, 100.0, 102.0)]
        m = recon.market_line({"bias": "偏空"}, {"QQQ": rows}, "15:30")   # 老卡没有剧本和期权定价
        self.assertEqual((m["dir_right"], m.get("script_hit"), m.get("implied")), (False, None, None))
        self.assertIsNone(recon.market_line({"bias": "偏多"}, {}, "15:30"))
        hold = {"level": 100.0, "dir": "守住", "by": "10:00"}
        self.assertIsNone(recon.script_hit(rows[:1], hold))        # 分钟线没走到截止，不判
        self.assertEqual(recon.script_hit(rows, hold), "10:00")

    def test_rule_mult_takes_half_spread_each_way(self):
        rows = [bar("10:10", 9.0, 10.0, 9.0, 10.0), bar("15:30", 19.0, 20.0, 19.0, 20.0)]
        self.assertEqual(recon.option_rule_mult(rows, "10:10", "15:30", 0.02), round(20 * 0.99 / (10 * 1.01), 2))
        self.assertIsNone(recon.option_rule_mult(rows, "10:11", "15:30", 0.02))   # 进场那一分钟没有价就不估


if __name__ == "__main__":
    unittest.main()
