#!/usr/bin/env python3
"""每天把两张盘前卡（多日作战卡、日内卡）自动发到 Telegram 群（WQ 2026-10-24 定：只发群，不发私聊）。

用法：
  card_push.py                      # 等今天的卡出来，出一张发一张，最晚等到 09:25 ET
  card_push.py --date 2026-10-23 --dry-run   # 用某天的卡试跑，只打印不发
  card_push.py --until 09:40        # 改最晚等到几点
卡的来源：~/Codes/reports/盘前作战卡/<美东日期>/ 下的 battle.json + 作战卡.pdf、日内卡.html + 日内卡.pdf。
去重：同目录 .telegram_sent.json；凭据只从 ~/.config/telegram 读，输出不含 token。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_send  # noqa: E402

ET = ZoneInfo("America/New_York")
CARDS = Path.home() / "Codes" / "reports" / "盘前作战卡"
WEEKDAY = "一二三四五六日"


def pct(x, nd=1) -> str:
    return f"{x:+.{nd}%}" if isinstance(x, (int, float)) else "待查"


def multi_text(d: dict, day: str) -> str:
    """多日作战卡 → 群消息：天气、主要矛盾、今天的动作、最值得交易的新增信息、异动没原因、时点。"""
    wd = WEEKDAY[datetime.fromisoformat(day).weekday()]
    out = [f"盘前作战卡（多日）· {day} 周{wd}", ""]
    if d.get("summary"):
        out.append(f"天气：{d['summary']}")
    c = d.get("contradiction") or {}
    if c.get("statement"):
        out.append(f"主要矛盾：{c['statement']}")
        if c.get("if_confirmed"):
            out.append(f"· 证实 = {c['if_confirmed']}")
        if c.get("if_rejected"):
            out.append(f"· 证伪 = {c['if_rejected']}")
    out.append("")
    do, cond, watch = d.get("do") or [], d.get("cond") or [], d.get("watch") or []
    out.append(f"今天直接做 {len(do)} 笔；条件单 {len(cond)} 个；观察 {len(watch)} 只")
    for r in do:
        out.append(f"做 {r.get('sym', '')}：{r.get('what', '')} → {r.get('action', '')}")
    for r in cond:
        line = f"{r.get('id', '')} {r.get('what', '')} → {r.get('action', '')}"
        if r.get("data"):
            line += f"（{r['data']}）"
        out.append(line)
    for r in watch[:4]:
        out.append(f"观察 {r.get('sym', '')}：{r.get('what', '')}；{r.get('action', '')}")
    top = d.get("top") or []
    if top:
        out += ["", "最值得交易的新增信息（否定线 / 确认线）："]
        for r in top[:5]:
            out.append(f"{r.get('sym', '')}：{r.get('tape', '')}")
    un = d.get("unexplained") or []
    if un:
        out += ["", "异动没原因，别当已知消息交易：" + "；".join(f"{r.get('sym', '')} {pct(r.get('gap'))}" for r in un[:5])]
    cp = d.get("checkpoints") or []
    if cp:
        out += ["", "时点：" + "｜".join(f"{t} {w}" for t, w in cp[:5])]
    out += ["", "完整卡见附件 PDF。内部计划，不构成投资建议。"]
    return "\n".join(out)


class _Text(HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "tr", "br", "section", "dt", "dd"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def intraday_text(html: str, day: str) -> str:
    """日内卡 HTML → 可见文字（卡本身已经收敛成三行头 + 每只四行，直接用）。"""
    p = _Text()
    p.feed(html)
    t = re.sub(r"[ \t]+", " ", "".join(p.out))
    t = re.sub(r"\n\s*\n+", "\n", t).strip()
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    if lines and lines[0].startswith("日内卡") and len(lines) > 1 and lines[1].startswith("日内卡"):
        lines = lines[1:]                      # 去掉重复的 <title>
    labels = ("主要矛盾", "大盘门", "今天进攻", "变了什么", "合约与赔率", "插针线", "不接：", "名单里没新事", "期权价差超过", "异动没找到原因", "接针四步")
    fixed = []
    head = re.compile(r"^([A-Z]{1,5})(Put|Call)([+-]?\d+(?:\.\d+)?%)(.*)$")   # 票头：QQQPut-0.3%盘前可交易
    for x in lines:
        m = head.match(x)
        if m:
            sym, side, gap, rest = m.groups()
            x = f"【{sym} {side}】盘前 {gap}" + (f"，{rest.strip()}" if rest.strip() else "")
            fixed.append("")
            fixed.append(x)
            continue
        for lb in labels:                      # 标签和正文之间补一个冒号或空格，手机上好读
            if x.startswith(lb) and not x.startswith(lb + "：") and not lb.endswith("："):
                x = lb + "：" + x[len(lb):]
                break
        fixed.append(x)
    return "\n".join(fixed + ["", "完整卡见附件 PDF。"])


def send(text: str, pdf: Path, caption: str, dry: bool) -> bool:
    if dry:
        print(f"----- [dry-run] 文字（{len(text)} 字）-----\n{text}\n----- 附件：{pdf.name} ({pdf.stat().st_size // 1024} KB) -----", flush=True)
        return True
    token = (tg_send.CFG / "bot_token").read_text().strip()
    ok_all = True
    for chat in tg_send.destinations(groups_only=True):
        for i in range(0, len(text), 3900):
            ok_all &= tg_send.send_text(token, chat, text[i:i + 3900])
        ok_all &= tg_send.send_file(token, chat, pdf, caption)
    return ok_all


MULTI_GATE_UNTIL = "09:05"   # 多日卡两个前提到这个点还没齐就不再等，照发并注明


def multi_ready(d: Path) -> tuple[bool, str]:
    """多日卡发出的两个前提（WQ 2026-10-24 定：TMTB 早报出来之后再发，内容才有效）。

    ① 裁定版：同目录 grok_cards.json 里至少一张卡有 routing.judge（作战卡运行器可能在裁定前先出过一版）；
    ② TMTB 早报已到：当天 tmtb/ 下有 morning-wrap 原文，或裁定那边已写 tmtb_brief.json。
    返回 (是否齐, 缺什么)。
    """
    missing = []
    gc = d / "grok_cards.json"
    try:
        cards = json.loads(gc.read_text(encoding="utf-8")) if gc.exists() else []
        if isinstance(cards, dict):
            cards = cards.get("cards") or []
        if not any((c.get("routing") or {}).get("judge") for c in cards if isinstance(c, dict)):
            missing.append("裁定")
    except (OSError, ValueError):
        missing.append("裁定")
    tmtb_in = any((d / "tmtb").glob("*morning-wrap*")) or (d / "tmtb_brief.json").exists()
    if not tmtb_in:
        missing.append("TMTB 早报")
    return (not missing, "、".join(missing))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="美东日期，默认今天")
    ap.add_argument("--until", default="09:25", help="最晚等到几点（美东）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    day = a.date or datetime.now(ET).strftime("%Y-%m-%d")
    d = CARDS / day
    state_p = d / ".telegram_sent.json"
    state = json.loads(state_p.read_text()) if (state_p.exists() and not a.dry_run) else {}
    want = {"multi": ("battle.json", "作战卡.pdf"), "intraday": ("日内卡.html", "日内卡.pdf")}
    while True:
        for key, (src, pdf) in want.items():
            if key in state or not (d / src).exists() or not (d / pdf).exists():
                continue
            gate_note = ""
            if key == "multi" and not a.date:
                ready, lack = multi_ready(d)
                if not ready:
                    if datetime.now(ET).strftime("%H:%M") < MULTI_GATE_UNTIL:
                        continue                   # 等裁定 / TMTB 早报，到点再说
                    gate_note = f"\n（{MULTI_GATE_UNTIL} 仍缺{lack}，先发这版）"
            time.sleep(3)                      # 等文件写完
            if key == "multi":
                text = multi_text(json.loads((d / src).read_text(encoding="utf-8")), day) + gate_note
                cap = f"盘前作战卡（多日）{day} 完整版"
            else:
                text = intraday_text((d / src).read_text(encoding="utf-8"), day)
                cap = f"日内卡 {day} 完整版"
            ok = send(text, d / pdf, cap, a.dry_run)
            print(f"{datetime.now(ET):%H:%M:%S} {key} {'发出' if ok else '发送失败'}", flush=True)
            if ok and not a.dry_run:
                state[key] = datetime.now(ET).isoformat()
                d.mkdir(parents=True, exist_ok=True)
                state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        if len(state) == 2 or a.dry_run or a.date:
            missing = [k for k in want if k not in state]
            if missing and not a.dry_run:
                print(f"{day} 没等到：{'、'.join(missing)}", flush=True)
            return 0
        if datetime.now(ET).strftime("%H:%M") >= a.until:
            print(f"{day} 到 {a.until} 还没等到：{'、'.join(k for k in want if k not in state)}", flush=True)
            return 1
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
