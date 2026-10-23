"""命令行入口。

  python -m tiger_review doctor                 检查老虎凭据与富途行情
  python -m tiger_review sync --since 2026-01-01 拉订单 / 持仓 / 净值到 data/raw
  python -m tiger_review review [--from --to]   出期间复盘
  python -m tiger_review day [--date]           出单日复盘（当天有动作的交易）
  python -m tiger_review demo                   用真实行情 + 模拟成交出演示报告
  python -m tiger_review auto                   收盘后自动任务：同步、当天有期权成交才出报告并通知
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta

from .config import ET, PROPS_DIR, REPORTS_DIR, Settings
from .fetch import load_json, merge_orders, save_json
from .market import Prices
from .pipeline import run
from .report import render


def _mask(acct: str | None) -> str:
    return f"尾号 {str(acct)[-4:]}" if acct else "未知账户"


def cmd_doctor(_args) -> None:
    from .fetch import TigerFetcher
    ok = True
    try:
        f = TigerFetcher()
        accts = f.accounts()
        print(f"[老虎] 凭据可用，默认账户 {_mask(f.account)}，名下账户 {len(accts)} 个")
        tok = getattr(f.config, "token", None)
        if tok:
            import base64
            gen, exp = base64.b64decode(tok)[:27].decode().split(",")
            when = datetime.fromtimestamp(int(exp) / 1000, tz=ET)
            note = "（本次已自动续期）" if f.token_refreshed else ""
            print(f"[老虎] token 到期 {when:%Y-%m-%d}{note}；签发满 7 天后跑 sync 会自动续期")
        today = datetime.now(tz=ET).date()
        n = f.filled_orders_check(today - timedelta(days=30), today)
        print(f"[老虎] 近 30 天成交订单 {n} 张（独立接口对账用）")
    except FileNotFoundError as e:
        ok = False
        print(f"[老虎] {e}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[老虎] 调用失败：{type(e).__name__}: {e}")
    p = Prices()
    try:
        from futu import RET_OK
        ret, q = p._ctx().get_history_kl_quota(get_detail=False)
        print(f"[富途] OpenD 连通，历史 K 线额度 已用 {q[0]} / 剩余 {q[1]}" if ret == RET_OK else f"[富途] {q}")
    except Exception as e:  # noqa: BLE001
        print(f"[富途] 连不上 OpenD：{e}")
    finally:
        p.close()
    raise SystemExit(0 if ok else 1)


def cmd_sync(args) -> None:
    from .fetch import TigerFetcher
    f = TigerFetcher()
    today = datetime.now(tz=ET).date()
    since = date.fromisoformat(args.since)
    new = f.orders(since, today)
    merged = merge_orders(load_json("orders.json", []), new)
    save_json("orders.json", merged)
    save_json("positions.json", f.positions())
    try:
        save_json("nav.json", f.nav_history(since, today))
    except Exception as e:  # noqa: BLE001
        print(f"净值历史没拉到：{e}")
    save_json("meta.json", {"account": _mask(f.account), "synced_at": datetime.now(tz=ET).isoformat(),
                            "since": since.isoformat()})
    print(f"订单 {len(new)} 张（本地累计 {len(merged)}），持仓已更新。")


def _write(res, recs, meta, name: str) -> str:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{name}.html"
    path.write_text(render(res, recs, meta), encoding="utf-8")
    (REPORTS_DIR / f"{name}.json").write_text(
        json.dumps({"res": res, "recs": recs, "meta": meta}, ensure_ascii=False, default=str, indent=1),
        encoding="utf-8")
    return str(path)


def cmd_review(args) -> None:
    orders = load_json("orders.json")
    if not orders:
        raise SystemExit("本地还没有订单，先跑 sync。")
    s = Settings.load()
    start = date.fromisoformat(args.start or s.strategy_start)
    end = date.fromisoformat(args.end) if args.end else None
    meta_acct = (load_json("meta.json", {}) or {}).get("account", "")
    p = Prices()
    try:
        res, recs, meta, _ = run(orders, p, s, start, end, load_json("positions.json", []),
                                 account=f"{s.account_label} {meta_acct}".strip())
    finally:
        p.close()
    tag = f"{(start or date.fromisoformat(recs[0]['open_ts'][:10]) if recs else date.today()):%Y%m%d}"
    print(_write(res, recs, meta, f"期权复盘-{tag}-{(end or datetime.now(tz=ET).date()):%Y%m%d}"))


def cmd_day(args) -> None:
    orders = load_json("orders.json")
    if not orders:
        raise SystemExit("本地还没有订单，先跑 sync。")
    d = date.fromisoformat(args.date) if args.date else datetime.now(tz=ET).date()
    s = Settings.load()
    p = Prices()
    try:
        start = date.fromisoformat(args.since or s.strategy_start)
        res, recs, meta, _ = run(orders, p, s, start, None, load_json("positions.json", []))
    finally:
        p.close()
    todays = [r for r in recs if any(e["ts"][:10] == d.isoformat() for e in r["events"])]
    meta["range"] = f"{d:%Y-%m-%d} 当日"
    meta["day_mode"] = True
    res["_all_recs"] = recs
    print(_write(res, todays or recs, meta, f"期权日复盘-{d:%Y%m%d}"))


def cmd_demo(args) -> None:
    from .demo_scenarios import build_orders
    s = Settings()
    p = Prices()
    try:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        orders = build_orders(p, start, end)
        save_json("demo_orders.json", orders)
        res, recs, meta, _ = run(orders, p, s, account="演示账户（模拟成交）", demo=True)
    finally:
        p.close()
    meta["notes"].insert(0, "演示数据：标的走势是真实行情，期权成交是按几类常见单腿打法模拟的，不是你的交易。")
    print(_write(res, recs, meta, "演示-期权复盘"))


def _notify(title: str, text: str) -> None:
    import subprocess
    t = text.replace('"', "'").replace("\\", "/")[:230]
    subprocess.run(["/usr/bin/osascript", "-e", f'display notification "{t}" with title "{title}"'],
                   check=False, capture_output=True)


def _log(msg: str) -> None:
    from .config import DATA_DIR
    d = DATA_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "auto.log", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now(tz=ET):%Y-%m-%d %H:%M:%S} {msg}\n")


def _covered_day(now: datetime) -> date:
    """这次复盘覆盖的交易日：开盘（9:30）前跑算上一个交易日，之后跑算当天。"""
    d = now.date()
    if now.hour * 60 + now.minute < 9 * 60 + 30:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _refresh_cheatsheet() -> None:
    """顺带刷新插针速查表（每只票回落多少算深）；失败不影响复盘。"""
    if not _futu_up():
        return
    try:
        from .cheatsheet import build
        build()
    except Exception as e:  # noqa: BLE001
        _log(f"插针速查表没刷新：{type(e).__name__}: {e}")


def _futu_up() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 11111), timeout=2):
            return True
    except OSError:
        return False


def cmd_auto(args) -> None:
    """收盘后自动跑：同步 → 只为「上次报告之后的新期权成交」出报告 → 系统通知。

    用状态文件记住已报告到哪一笔成交，所以 Mac 睡过头隔天补跑、或白天手动先跑过一次，
    都不会漏报也不会重复报。没有新成交不打扰，出错才报。
    """
    import shutil
    from .fetch import TigerFetcher
    s = Settings.load()
    start = date.fromisoformat(s.strategy_start)
    today = datetime.now(tz=ET).date()
    state = load_json("auto_state.json", {}) or {}
    last_ms = int(state.get("last_fill_ms", 0))
    scheduled = bool(getattr(args, "scheduled", False))
    covered = _covered_day(datetime.now(tz=ET))
    if scheduled and state.get("last_review_date") == covered.isoformat():
        _log(f"{covered:%m/%d} 已经手动复盘过，定时任务跳过")
        return
    if not scheduled:
        state["last_review_date"] = covered.isoformat()
        save_json("auto_state.json", state)
    try:
        f = TigerFetcher()
        merged = merge_orders(load_json("orders.json", []), f.orders(start, today))
        save_json("orders.json", merged)
        positions = f.positions()
        save_json("positions.json", positions)
    except Exception as e:  # noqa: BLE001
        _log(f"同步失败 {type(e).__name__}: {e}")
        _notify("老虎复盘失败", f"同步老虎订单失败：{type(e).__name__}: {e}")
        raise SystemExit(1)
    if f.token_refreshed:
        _log("token 已自动续期")
    new = [o for o in merged if (o.get("contract") or {}).get("sec_type") in ("OPT", "MLEG")
           and int(o.get("trade_time") or 0) > last_ms]
    if not new:
        _refresh_cheatsheet()
        _log("没有新的期权成交，不出报告")
        return
    days = sorted({datetime.fromtimestamp(int(o["trade_time"]) / 1000, tz=ET).date() for o in new})
    warn = []
    futu = _futu_up()
    if not futu:
        warn.append("富途 OpenD 没开，盈亏拆解缺行情，开了之后重跑一次")
    p = Prices(use_futu=futu)
    try:
        res, recs, meta, _ = run(merged, p, s, start, None, positions)
        if p.errors:
            warn.append(f"行情缺失 {len(p.errors)} 处")
    finally:
        p.close()
    cum = _write(res, recs, meta, f"期权复盘-{start:%Y%m%d}-{days[-1]:%Y%m%d}")
    res["_all_recs"] = recs
    texts, day_path = [], None
    for d in days:
        day_recs = [r for r in recs if any(e["ts"][:10] == d.isoformat() for e in r["events"])]
        if not day_recs:
            continue
        meta_d = dict(meta, range=f"{d:%Y-%m-%d} 当日", day_mode=True)
        day_path = _write(res, day_recs, meta_d, f"期权日复盘-{d:%Y%m%d}")
        pnl = sum(r["pnl"] for r in day_recs if r["status"] == "closed")
        texts.append(f"{d:%m/%d} {len(day_recs)} 笔净 {'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}")
    if day_path:
        shutil.copyfile(day_path, REPORTS_DIR / "最新-今日复盘.html")
    shutil.copyfile(cum, REPORTS_DIR / "最新-策略累计复盘.html")
    if positions:
        warn.append(f"还有 {len(positions)} 个持仓未了结，按持仓中处理")
    ov = res.get("overall") or {}
    text = ("；".join(texts) + f"；累计 {ov.get('n', 0)} 笔净 "
            f"{'+' if ov.get('pnl', 0) >= 0 else '-'}${abs(ov.get('pnl', 0)):,.0f}。")
    if warn:
        text += " 注意：" + "；".join(warn)
    state.update({"last_fill_ms": max(int(o["trade_time"]) for o in new),
                  "reported_at": datetime.now(tz=ET).isoformat(),
                  "last_review_date": covered.isoformat()})
    save_json("auto_state.json", state)
    _refresh_cheatsheet()
    _log(f"完成 {text} {day_path}")
    _notify("期权复盘已生成", text + " 报告：tiger-review/reports/最新-今日复盘.html")


def cmd_spike(args) -> None:
    """盘中查插针读数：python -m tiger_review spike NVDA [--at 09:34] [--date 2026-10-20]"""
    from .spike import describe, reading
    d = date.fromisoformat(args.date) if args.date else datetime.now(tz=ET).date()
    if args.at:
        hh, mm = map(int, args.at.split(":"))
        ts = datetime(d.year, d.month, d.day, hh, mm, 59, tzinfo=ET)
    else:
        ts = datetime.now(tz=ET)
    p = Prices()
    try:
        for sym in args.symbols:
            r = reading(p, sym.upper(), ts, "up" if args.put else "down")
            print(describe(r) if r else f"{sym}：没有这个时刻的常规时段分钟线")
    finally:
        p.close()


def cmd_note(args) -> None:
    """记选票前提 / 进场逻辑：python -m tiger_review note "SPCX 2026-10-21 09:44" --premise ... --logic ..."""
    import json as _json
    from .config import DATA_DIR
    path = DATA_DIR / "notes.json"
    data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    item = data.get(args.key, {})
    if args.premise:
        item["选票前提"] = args.premise
    if args.logic:
        item["进场逻辑"] = args.logic
    data[args.key] = item
    path.write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已记录 {args.key}：{item}")


def cmd_alerts(args) -> None:
    from .alerts import cmd_alerts as alerts
    alerts(args)


def main() -> None:
    ap = argparse.ArgumentParser(prog="tiger_review")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sp = sub.add_parser("sync"); sp.add_argument("--since", default=f"{date.today().year}-01-01")
    rv = sub.add_parser("review"); rv.add_argument("--from", dest="start"); rv.add_argument("--to", dest="end")
    dy = sub.add_parser("day"); dy.add_argument("--date"); dy.add_argument("--since")
    sk = sub.add_parser("spike"); sk.add_argument("symbols", nargs="+"); sk.add_argument("--at"); sk.add_argument("--date")
    sk.add_argument("--put", action="store_true", help="读向上冲高（买 Put 的场景）")
    nt = sub.add_parser("note"); nt.add_argument("key"); nt.add_argument("--premise"); nt.add_argument("--logic")
    au = sub.add_parser("auto")
    au.add_argument("--scheduled", action="store_true",
                    help="收盘定时任务用：当天已手动复盘过就直接跳过")
    al = sub.add_parser("alerts", help="盘中 Telegram 提醒 / 离线回放")
    al.add_argument("--replay", metavar="YYYY-MM-DD", help="用本地分钟线离线回放")
    al.add_argument("--pins", help="回放接针名单，例如 TSLA:3.0,NVDA:2.5；省略百分数则算 ATR14")
    al.add_argument("--dry-run", action="store_true", help="只打印，不发 Telegram、不写去重状态")
    al.add_argument("--once", action="store_true", help="实盘只读取一轮，忽略常驻时段限制")
    dm = sub.add_parser("demo"); dm.add_argument("--start", default="2026-06-15"); dm.add_argument("--end", default="2026-09-25")
    args = ap.parse_args()
    {"doctor": cmd_doctor, "sync": cmd_sync, "review": cmd_review, "day": cmd_day, "demo": cmd_demo,
     "auto": cmd_auto, "spike": cmd_spike, "note": cmd_note, "alerts": cmd_alerts}[args.cmd](args)
