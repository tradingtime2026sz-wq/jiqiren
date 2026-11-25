"""交易日历：感恩节休市日跳过、次日 13:00 半日市的收盘时点都跟着日历走。"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tiger_review import alerts, config as c, recon

THU, FRI, WED = date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 25)


def bar(day, hm):
    return {"time_key": f"{day} {hm}:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}


class CalendarTest(unittest.TestCase):
    def test_thanksgiving_week_days(self):
        self.assertFalse(c.is_trading_day(THU))
        self.assertTrue(c.is_trading_day(FRI) and c.is_trading_day(WED))
        self.assertEqual(c.prev_trading_day(FRI), WED)
        self.assertEqual(c.prev_trading_day(date(2026, 11, 30)), FRI)

    def test_early_close_times(self):
        self.assertEqual((c.close_hm(FRI), c.grab_hm(FRI), c.last_bar_hm(FRI), c.default_flat_by(FRI)),
                         ("13:00", "12:54", "12:59", "12:30"))
        self.assertEqual((c.close_hm(WED), c.grab_hm(WED), c.last_bar_hm(WED), c.default_flat_by(WED)),
                         ("16:00", "15:54", "15:59", "15:30"))

    def test_rth_cuts_at_the_days_own_close(self):
        early = [bar(FRI, h) for h in ("09:31", "12:59", "13:00", "13:01")]
        self.assertEqual([r["time_key"][11:16] for r in alerts.rth(early, FRI)], ["09:31", "12:59", "13:00"])
        full = [bar(WED, h) for h in ("15:59", "16:00", "16:01")]
        self.assertEqual([r["time_key"][11:16] for r in alerts.rth(full, WED)], ["15:59", "16:00"])

    def test_gate0_after_holiday_reads_the_wednesday_close(self):
        with tempfile.TemporaryDirectory() as raw:
            log = Path(raw) / "monitor-log.md"
            log.write_text("## 2026-11-26 每日复盘（覆盖2026-11-25收盘）\n【Gate 0】RED 红灯\n", encoding="utf-8")
            saved, recon.MONITOR_LOG = recon.MONITOR_LOG, log
            try:
                g = recon.gate0(FRI)
            finally:
                recon.MONITOR_LOG = saved
        self.assertEqual((g["color"], g.get("covers")), ("红", "2026-11-25"))

    def test_card_flat_by_is_capped_by_the_days_default(self):
        doc = {"intraday_alerts": {"schema": "odte-intraday-alerts.v1", "panic_put": {"underlyings": [], "premarket": {}},
                                   "pin_catch": {"names": []},
                                   "session": {"main_window": ["09:30", "10:30"], "flat_by": "15:30"}}}
        import json
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "odte.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(alerts.load_card(FRI, source=path).flat_by, "12:30")
            self.assertEqual(alerts.load_card(WED, source=path).flat_by, "15:30")


if __name__ == "__main__":
    unittest.main()
