"""插针速查表：每只票「从当天高点回落多少算深」= 过去 14 天平均日内振幅。

收盘后自动任务顺带刷新，第二天盘中直接看。票池 = option-ab-lab 的热门票池 + 你交易过的标的。
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DATA_DIR, ET, REPORTS_DIR
from .market import Prices
from .report import CSS, esc

POOL = Path("/Users/vock/Codes/option-ab-lab/pool.csv")
SKIP = {"QQQ", "SPY", "SOXX"}


def universe() -> list[tuple[str, str]]:
    out: dict[str, str] = {}
    if POOL.exists():
        for row in csv.reader(POOL.open(encoding="utf-8")):
            if row and row[0] not in SKIP:
                out[row[0]] = row[1] if len(row) > 1 else ""
    orders = DATA_DIR / "raw" / "orders.json"
    if orders.exists():
        for o in json.loads(orders.read_text()):
            c = o.get("contract") or {}
            if c.get("sec_type") in ("OPT", "MLEG") and c.get("symbol") and c["symbol"] not in SKIP:
                out.setdefault(c["symbol"], "你交易过")
    return sorted(out.items(), key=lambda x: (x[1], x[0]))


def build(p: Prices | None = None) -> Path:
    own = p is None
    p = p or Prices()
    now = datetime.now(tz=ET)
    today = now.date()
    # 盘中生成时当天 K 线还没走完，不计入振幅
    last_day = today if now.hour >= 16 else today - timedelta(days=1)
    rows = []
    try:
        for sym, sector in universe():
            p.ensure_daily(sym, today - timedelta(days=40), today)
            d = [r for r in p.daily(sym) if r["time_key"][:10] <= last_day.isoformat()]
            if len(d) < 16:
                continue
            rng = [(d[i]["high"] - d[i]["low"]) / d[i - 1]["close"] for i in range(len(d) - 14, len(d))]
            atr = sum(rng) / len(rng)
            rows.append((sector, sym, atr, float(d[-1]["close"]), d[-1]["time_key"][:10]))
    finally:
        if own:
            p.close()
    asof = max((r[4] for r in rows), default=today.isoformat())
    body = "".join(
        f"<tr><td>{esc(sec)}</td><td><b>{esc(sym)}</b></td><td>{atr * 100:.1f}%</td>"
        f"<td>{close:.2f}</td><td>{close * atr:.2f}</td></tr>" for sec, sym, atr, close, _ in rows)
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>插针速查</title><style>{CSS}</style></head>
<body><main><h1>插针速查</h1><div class='sub'>按 {esc(asof)} 收盘计算，过去 14 个交易日的平均日内振幅</div>
<div class='card hero'>
<p><b>只看深针</b>：从当天高点回落，超过下表「回落多少算深」才算。</p>
<p><b>打分 ≥3 才接</b>：插针时仍在开盘价上方算 2 分；高开 ≥3%、开盘 30 分钟以后、QQQ 同时也在跌，各算 1 分。最常见的凑法是「仍在开盘价上方 + 再占一条」。</p>
<p><b>等确认再进</b>：看 1 分钟 K 线，等收盘重新站上 1 分钟 MA20 再进；之后跌破针底就走。RSI、KDJ、布林下轨都不用看。</p>
</div>
<div class='tw'><table><tr><th>板块</th><th>代码</th><th>回落多少算深</th><th>最新收盘</th><th>约合美元</th></tr>{body}</table></div>
<p class='sub'>回测依据见「插针量化」报告。热点日、深回落、打分 ≥3、等 1 分钟收回 MA20 再进：30 分钟内先涨 2% 的约一半，先跌破针底的约两成（2024-10 至 2026-10，81 次）。</p>
</main></body></html>"""
    path = REPORTS_DIR / "插针速查.html"
    path.write_text(html, encoding="utf-8")
    return path


if __name__ == "__main__":
    print(build())
