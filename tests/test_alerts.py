"""提醒的时间边界、两段信号、持久去重与 I/O 隔离。全部使用合成数据。"""
import copy
import json
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import pandas as pd
import pytest

from tiger_review import alerts as a

DAY = date(2026, 10, 22)


def at(hm):
    return datetime.fromisoformat(f"{DAY} {hm}:00").replace(tzinfo=a.ET)


def card(pins=None, enabled=None):
    return a.Card(DAY, dict(a.PANIC), copy.deepcopy(a.PIN), pins or [], ["QQQ"],
                  {"enabled": enabled}, ["09:30", "10:30"], "15:30", False, -0.04)


def engine(c=None, pool=None, persistent=False):
    messages = []

    def send(ts, text):
        messages.append((ts, text))
        return True

    return a.Engine(c or card(), send, pool or [], persistent), messages


def quotes(v=103, q=99, opening=101):
    return {"VIXY": dict(last_price=v, prev_close_price=100),
            "QQQ": dict(last_price=q, prev_close_price=100, open_price=opening)}


@pytest.mark.parametrize("value,count", [(101.99, 0), (102, 1), (104.199, 1), (104.2, 0)])
def test_panic_trigger_band(value, count):
    e, messages = engine()
    e.step(at("10:00"), quotes(v=value), {})
    assert len(messages) == count


def test_panic_weak_then_strong_then_excluded_once_each():
    c = card()
    e, messages = engine(c)
    e.step(at("10:00"), quotes(opening=99), {})  # 只有五日跌幅尾巴。
    e.step(at("10:01"), quotes(), {})
    e.step(at("10:02"), quotes(), {})
    e.step(at("10:03"), quotes(v=106.1), {})  # 恰好 6.1% 也排除。
    e.step(at("10:04"), quotes(v=110), {})
    assert [m.splitlines()[0] for _, m in messages] == ["恐慌有苗头，还不够：先别动", "恐慌来了：可以小仓位买 QQQ 的 Put", "VIX 今天已经涨过 13%：Put 不追了"]
    assert "不追" in messages[-1][1]
    assert all(len(m.splitlines()) <= 12 for _, m in messages)


def test_panic_disabled_only_exclusion_and_no_premarket_decision():
    e, messages = engine(card(enabled=False))
    e.step(at("09:29"), quotes(), {})
    e.step(at("09:44"), quotes(), {})
    assert not messages
    e.step(at("10:00"), quotes(), {})
    e.step(at("10:01"), quotes(), {})
    assert len(messages) == 1 and "今天不做" in messages[0][1]
    assert "小仓位" not in messages[0][1]


def test_acceleration_and_missing_breadth_do_not_use_future_or_small_denominator():
    c = card()
    c.ret5 = 0
    e, messages = engine(c, ["A", "B", "C", "D"])
    e.step(at("09:30"), quotes(v=100, opening=99), {})
    q = quotes(v=103, opening=99)
    q["A"] = dict(last_price=90, turnover=10000, volume=100)
    e.step(at("10:00"), q, {})
    assert len(messages) == 1
    assert "最近 30 分钟 VIX 还在加速" in messages[0][1]
    assert "只有 25%" in messages[0][1]
    assert "85% 以上跌破" not in messages[0][1]
    e2, messages2 = engine(c)
    e2.step(at("10:01"), quotes(v=103, opening=99), {})
    assert not messages2  # 从中途启动，不能把现价当作 30 分钟前的价。


def pin_spec():
    return dict(sym="TEST", prev_close=100, pin_depth_pct=0.03, min_score=3, best_score=4,
                no_catch_if="同组一起跌，先不接")


def pin_bars():
    rows = []
    start = at("09:40")
    for i in range(31):
        close = 105 if i == 30 else 109
        rows.append(dict(time_key=(start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                         open=100 if i == 0 else 109, high=110, low=104 if i == 30 else 108,
                         close=close, volume=100))
    return rows


def append_bar(rows, close, high, low):
    stamp = datetime.fromisoformat(rows[-1]["time_key"]) + timedelta(minutes=1)
    return rows + [dict(time_key=stamp.strftime("%Y-%m-%d %H:%M:%S"), open=close,
                        close=close, high=high, low=low, volume=100)]


def test_pin_arrival_then_cross_confirmation_then_new_day_high():
    e, messages = engine(card([pin_spec()]))
    rows = pin_bars()
    e.step(at("10:10"), {}, {"TEST": rows})
    e.step(at("10:10"), {}, {"TEST": rows})
    assert len(messages) == 1 and "插针了" in messages[0][1]
    rows = append_bar(rows, 109, 110, 103)
    e.step(at("10:11"), {}, {"TEST": rows})
    assert len(messages) == 2 and "TEST 可以接了" in messages[1][1]
    assert "针底 103.00" in messages[1][1]
    rows = append_bar(rows, 105, 110, 104)
    e.step(at("10:12"), {}, {"TEST": rows})
    rows = append_bar(rows, 109, 110, 104)
    e.step(at("10:13"), {}, {"TEST": rows})
    assert len(messages) == 2  # 同一个针只能各发一次。
    rows = append_bar(rows, 111, 112, 110)
    e.step(at("10:14"), {}, {"TEST": rows})
    rows = append_bar(rows, 107, 111, 106)
    e.step(at("10:15"), {}, {"TEST": rows})
    assert len(messages) == 3 and "插针了" in messages[-1][1]
    rows = append_bar(rows, 111, 112, 110)
    e.step(at("10:16"), {}, {"TEST": rows})
    assert len(messages) == 4 and "可以接了" in messages[-1][1]


def test_pin_confirmation_requires_an_actual_later_cross_and_time_cutoff():
    e, messages = engine(card([pin_spec()]))
    rows = pin_bars()
    for row in rows[:-1]:
        row["close"] = 101
    e.step(at("10:10"), {}, {"TEST": rows})  # 深度满足但收盘已在 MA20 上方。
    rows = append_bar(rows, 106, 110, 105)
    e.step(at("10:11"), {}, {"TEST": rows})
    assert len(messages) == 1  # 仍在 MA20 上方，不算重新站上。
    e2, after = engine(card([pin_spec()]))
    e2.step(at("15:31"), {}, {"TEST": rows})
    assert not after
    e3, outside = engine(card([pin_spec()]))
    shifted = [{**r, "time_key": r["time_key"].replace("09:", "12:").replace("10:", "13:")} for r in pin_bars()]
    e3.step(at("13:10"), {}, {"TEST": shifted})
    assert "只当提示" in outside[0][1]


def test_failed_confirmation_retries_and_persistent_state_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "DATA_DIR", tmp_path)
    e, messages = engine(persistent=True)
    attempts = []

    def fail(ts, text):
        attempts.append(text)
        return False

    e.send = fail
    e.emit("pin:TEST:confirmed", at("10:00"), ["接针：确认 TEST", "止损＝针底：100，跌破就走"])
    assert "pin:TEST:confirmed" not in e.state["sent"]
    e2, delivered = engine(persistent=True)
    e2.step(at("10:01"), {}, {})
    assert len(delivered) == 1 and delivered[0][0] == at("10:00")
    e3, repeated = engine(persistent=True)
    e3.emit("pin:TEST:confirmed", at("10:02"), ["接针：确认 TEST"])
    assert not repeated


def test_retries_never_send_expired_pin_or_panic_buy_after_exclusion():
    e, messages = engine()
    e.send = lambda *args: False
    e.step(at("10:00"), quotes(), {})
    assert "panic_strong" in e.state["pending"]
    delivered = []
    e.send = lambda ts, text: delivered.append(text) or True
    e.step(at("10:01"), quotes(v=107), {})
    assert len(delivered) == 1 and "不追" in delivered[0]
    assert "panic_strong" not in e.state["pending"]
    e.send = lambda *args: False
    e.emit("pin:TEST:confirmed", at("15:30"), ["接针：确认 TEST"])
    e.send = lambda ts, text: delivered.append(text) or True
    e.step(at("15:31"), {}, {})
    assert len(delivered) == 1


def test_card_missing_fields_falls_back_and_put_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "CARDS", tmp_path)
    path = tmp_path / str(DAY) / "odte.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"intraday_alerts": {"schema": "odte-intraday-alerts.v1"}}))
    fallback = a.load_card(DAY)
    assert fallback.missing and fallback.underlyings == ["QQQ"] and not fallback.pins
    data = dict(schema="odte-intraday-alerts.v1", panic_put=dict(premarket=dict(enabled=False), underlyings=["TSLA"]),
                pin_catch=dict(names=[dict(sym="TSLA", side="Call"), dict(sym="NVDA", side="Put")]),
                session=dict(main_window=["09:30", "10:30"], flat_by="15:30"))
    path.write_text(json.dumps({"intraday_alerts": data}))
    c = a.load_card(DAY)
    assert [p["sym"] for p in c.pins] == ["TSLA"]
    assert c.underlyings == ["QQQ", "TSLA"] and c.premarket["enabled"] is False
    assert a.parse_pins("TSLA:3.0,NVDA")[0]["pin_depth_pct"] == 0.03


def test_atr_and_ret5_use_only_closed_daily_bars(monkeypatch):
    rows = [dict(time_key=str(DAY - timedelta(days=20 - i)), high=102, low=98, close=100 + i, volume=1)
            for i in range(20)]
    rows.append(dict(time_key=str(DAY), high=10000, low=1, close=500, volume=1))
    modes = []

    class Cached:
        def __init__(self, use_futu):
            modes.append(use_futu)

        def ensure_daily(self, *args):
            pass

        def daily(self, sym):
            return rows

        def close(self):
            pass

    monkeypatch.setattr(a, "Prices", Cached)
    c = card([dict(sym="TEST", pin_depth_pct=None)])
    a.daily_context(c)
    assert modes == [False]
    assert c.pins[0]["pin_depth_pct"] == pytest.approx(sum(4 / (100 + i - 1) for i in range(6, 20)) / 14)
    assert c.pins[0]["prev_close"] == 119
    assert c.ret5 == pytest.approx(119 / 114 - 1)


def test_telegram_failure_never_prints_credential_and_uses_form(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(a.Path, "home", lambda: tmp_path)
    config = tmp_path / ".config" / "telegram"
    config.mkdir(parents=True)
    (config / "bot_token").write_text("synthetic-private-value")
    (config / "chat_id").write_text("123\n-456\n")   # 私聊 + 群：只应发群
    logs = []
    monkeypatch.setattr(a, "log", logs.append)
    forms = []

    def reject(req, timeout):
        forms.append(parse_qs(req.data.decode()))
        raise RuntimeError(req.full_url)

    monkeypatch.setattr(a.request, "urlopen", reject)
    assert not a.Telegram(False).send(at("10:00"), "测试提醒")
    assert forms == [{"chat_id": ["-456"], "text": ["测试提醒"]}]
    assert "synthetic-private-value" not in capsys.readouterr().out + "".join(logs)
    (config / "chat_id").unlink()
    forms.clear()
    assert not a.Telegram(False).send(at("10:00"), "测试提醒")
    assert not forms


def test_live_feed_only_uses_snapshot_and_current_completed_bars(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Context:
        def __init__(self, **kwargs):
            pass

        def subscribe(self, codes, types):
            calls.append(("subscribe", codes))
            return 0, None

        def get_market_snapshot(self, codes):
            calls.append(("snapshot", codes))
            return 0, pd.DataFrame([dict(code=code, last_price=100, prev_close_price=99) for code in codes])

        def get_cur_kline(self, code, count, kind):
            calls.append(("kline", code, count, kind))
            return 0, pd.DataFrame(pin_bars())

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(a.socket, "create_connection", lambda *args, **kwargs: Connection())
    monkeypatch.setitem(sys.modules, "futu", SimpleNamespace(OpenQuoteContext=Context,
                        SubType=SimpleNamespace(K_1M="1m"), KLType=SimpleNamespace(K_1M="1m"), RET_OK=0))
    feed = a.LiveFeed([pin_spec()], ["OTHER"])
    q, b = feed.poll(at("10:10"))
    feed.close()
    assert calls[0] == ("subscribe", ["US.QQQ", "US.TEST"])
    assert len([c for c in calls if c[0] == "snapshot"]) == 1
    assert set(q) == {"VIXY", "QQQ", "TEST", "OTHER"}
    assert all(rows[-1]["time_key"] == f"{DAY} 10:09:00" for rows in b.values())


def test_replay_is_offline_and_does_not_touch_live_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(a, "DATA_DIR", tmp_path)
    monkeypatch.setattr(a, "MINUTES", tmp_path / "minutes")
    folder = tmp_path / "minutes" / "QQQ"
    folder.mkdir(parents=True)
    row = dict(time_key=f"{DAY} 09:31:00", open=100, high=101, low=99, close=100, volume=100)
    (folder / f"{DAY}.json").write_text(json.dumps([row]))
    research = tmp_path / "research"
    research.mkdir()
    (research / "vixy_1m.csv").write_text("time_key,open,high,low,close,volume\n"
                                         "2026-10-21 16:00:00,10,10,10,10,100\n"
                                         "2026-10-22 09:31:00,10,10,10,10,100\n")

    def forbidden(*args, **kwargs):
        pytest.fail("回放不应使用网络")

    monkeypatch.setattr(a.socket, "create_connection", forbidden)
    monkeypatch.setattr(a.request, "urlopen", forbidden)
    a.replay(card(), {"QQQ": [dict(close=100)], "VIXY": []}, [])
    assert "当日无触发" in capsys.readouterr().out
    assert not (tmp_path / "raw").exists()


def test_once_success_and_failure_exit_without_real_network(monkeypatch, capsys):
    logs = []
    monkeypatch.setattr(a, "log", logs.append)

    class Feed:
        def __init__(self, *args):
            pass

        def poll(self, ts):
            return quotes(), {}

        def close(self):
            pass

    monkeypatch.setattr(a, "LiveFeed", Feed)
    a.live(card(), [], dry=True, once=True)
    output = capsys.readouterr().out
    assert "一轮快照" in output and "VIXY" in output and "QQQ" in output
    assert "相对昨收" in output

    def disconnected(*args):
        raise RuntimeError("无法连接 OpenD 127.0.0.1:11111")

    monkeypatch.setattr(a, "LiveFeed", disconnected)
    with pytest.raises(SystemExit) as exc:
        a.live(card(), [], dry=True, once=True)
    assert exc.value.code == 1
    assert "无法连接 OpenD" in capsys.readouterr().out
