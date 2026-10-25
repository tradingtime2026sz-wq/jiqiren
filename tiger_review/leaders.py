"""时代领袖发言监听（WQ 2026-10-24 定：川普、黄仁勋、马斯克这类人的原话是「边际高置信」信息，要第一时间推到群里）。

数据源：Google News RSS（免费、不用密钥、延迟分钟级），每个人一条查询，只看最近 WINDOW_MIN 分钟。
初筛：标题里本人名字紧挨着「说 / 宣布 / 威胁…」这类动词（是他在说，不是别人在说他），且带市场相关词；
去重：同一个人 6 小时内标题用词重合度高的算同一件事；每人每小时最多推 MAX_PER_HOUR 条，防刷屏。
裁判（WQ 10-25 定：不能一说话就推）：初筛过的再交给三个人投票——宏观交易员 / AI 硬件板块研究员 / 期权日内交易员，
用本机 `claude -p`（Haiku，不开工具），只有「新事实」且至少两票 push 才发；裁判出错按不发处理，都记在 data/logs/leaders.log。
推送：Telegram 群（tg_send.py --groups-only 读 chat_id 第 2 行起的群）；去重状态在 data/leaders_seen.json。
用法：python -m tiger_review leaders [--dry-run] [--window 90]      # launchd 每 5 分钟跑一次，跑完就退
名单和关键词改本文件顶部的 LEADERS / MARKET 即可。
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as XML
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib import parse, request

from .config import DATA_DIR, ET

# 盯谁：显示名 → Google News 查询 + 标题里认人的别名（按长到短排）
LEADERS: dict[str, dict] = {
    "川普": {"q": '"Trump"', "aliases": ["President Trump", "Donald Trump", "Trump", "POTUS"]},
    "黄仁勋": {"q": '"Jensen Huang" OR "Nvidia CEO"',
               "aliases": ["Jensen Huang", "Nvidia CEO", "Nvidia's Huang", "Nvidia chief", "Jensen", "Huang"]},
    "马斯克": {"q": '"Elon Musk" OR "Musk"', "aliases": ["Elon Musk", "Musk", "Elon"]},
}
VERBS = (r"(?:says?|said|tells?|told|warns?|vows?|calls?|announce[sd]?|threatens?|slams?|signals?|plans?|posts?|posted"
         r"|wrote|writes|tweets?|unveils?|orders?|pledges?|hints?|predicts?|urges?|confirms?|denies|rules? out|promises?"
         r"|backs?|blasts?|praises?|demands?|rejects?|touts?|floats?|insists?|claims?|reveals?|suggests?|proposes?"
         r"|wants?|seeks?|pushes|pushed|moves? to|set to|to [a-z]+)")
# 市场相关：命中的词会写进推送的「相关」行
MARKET = re.compile(
    r"\b(tariffs?|trade|China|Chinese|Fed|rates?|Powell|interest|inflation|Nvidia|NVDA|chips?|semiconductors?|exports?"
    r"|AI|Tesla|TSLA|SpaceX|xAI|Grok|tax(?:es)?|bill|oil|deal|ban|sanctions?|dollar|crypto|bitcoin|TSMC|Intel|Apple"
    r"|data ?centers?|energy|nuclear|robots?|Optimus|rare earths?|Taiwan|Blackwell|Rubin|OpenAI|stocks?|markets?|economy"
    r"|recession|Treasury|debt|shutdown|Boeing|Starship|Starlink|FSD|robotaxi|Cybercab|Neuralink|GPUs?|capex|Micron"
    r"|Samsung|Hynix|Huawei|DeepSeek|Anthropic|Microsoft|Google|Amazon|Meta|Palantir|Oracle|CoreWeave|Iran|Russia|Ukraine"
    r"|ceasefire|war|Venezuela|Canada|Mexico|EU|Europe|Japan|Korea|India|pharma|drug prices?|Medicare|housing|mortgage)\b",
    re.I,
)
SEEN = DATA_DIR / "leaders_seen.json"
TTL_H = 48          # 去重记忆保留多久
DUP_H = 6           # 多久以内的相似标题算同一件事
DUP_JACCARD = 0.3   # 标题用词重合度 ≥ 这个值算同一件事（已去掉人名和停用词）
MAX_PER_HOUR = 3    # 每人每小时最多推几条
WINDOW_MIN = 90
JUDGE_MODEL = os.environ.get("LEADERS_JUDGE_MODEL", "claude-haiku-5-5")
JUDGE_TIMEOUT = 90
JUDGE_PROMPT = """你是美股科技交易团队的「领袖发言裁判」。团队做纳指期权和 AI 硬件链（算力 / 存储 / 光互连 / 物理基建：NVDA、MU、SNDK、LITE、AAOI、MRVL、TSLA 等），也盯关税、中美、美联储、财政这些宏观开关。
下面是一条新闻标题，说的是 {who} 本人刚说的话。请三个人分别投票，再给结论：
1) 宏观交易员：这句话会不会改变未来 5 个交易日的宏观 / 指数预期（关税、利率、中美、财政、战争）？
2) AI 硬件板块研究员：会不会直接改变 AI 算力 / 存储 / 光通信 / 半导体或 TSLA、NVDA 的基本面预期（新数字、新政策、新订单、新禁令）？
3) 期权日内交易员：开盘 30 分钟内会不会因此出现可交易的波动（方向明确、标的明确）？
先定性 fact：这句话是不是「新事实」——新数字、新日期、新决定、新威胁、新订单、新禁令；重复既有立场、口水、竞选话术、旧闻重炒、别人转述他「可能」怎样，fact=false。拿不准就 false。
标题：{title}
媒体：{source}；时间：{at} ET；初筛命中词：{hits}
只输出一行 JSON，不要别的字：{{"fact":true或false,"macro":true或false,"sector":true或false,"options":true或false,"why":"不超过40字的中文理由","tickers":["最相关的美股代码，最多3个"]}}
不要调用任何工具。"""


def _claude_bin() -> str | None:
    """本机 claude 命令：环境变量 CLAUDE_BIN → PATH → 常见安装位置（launchd 的 PATH 很短，所以要自己找）。"""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude")
    if found:
        return found
    home = str(Path.home())
    for pat in (f"{home}/.local/bin/claude", f"{home}/.nvm/versions/node/*/bin/claude", "/opt/homebrew/bin/claude"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def judge(who: str, it: dict) -> dict:
    """三人投票。返回 {"push": bool, "why": str, "tickers": [...], "votes": "宏观✓ 板块✗ 期权✓", "error": str|None}。
    任何失败都按不推处理（宁可漏，不刷屏）。"""
    bad = {"push": False, "why": "", "tickers": [], "votes": "", "error": None}
    cb = _claude_bin()
    if not cb:
        return {**bad, "error": "没找到 claude 命令"}
    prompt = JUDGE_PROMPT.format(who=who, title=strip_source(it["title"]), source=it["source"] or "未知",
                                 at=f"{it['at'].astimezone(ET):%m-%d %H:%M}", hits="、".join(it["hits"]))
    try:
        r = subprocess.run([cb, "-p", "--model", JUDGE_MODEL, "--max-turns", "1", "--no-session-persistence",
                            "--output-format", "text", prompt],
                           capture_output=True, text=True, timeout=JUDGE_TIMEOUT, cwd=str(Path.home()),
                           env={**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return {**bad, "error": f"claude 调用失败 {type(e).__name__}"}
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if r.returncode != 0 or not m:
        return {**bad, "error": f"claude 返回异常 rc={r.returncode}: {(r.stderr or r.stdout)[:120]!r}"}
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return {**bad, "error": f"裁判输出不是 JSON: {m.group(0)[:120]!r}"}
    fact = bool(d.get("fact"))
    votes = [bool(d.get(k)) for k in ("macro", "sector", "options")]
    mark = lambda v: "✓" if v else "✗"  # noqa: E731
    # 推送条件：是新事实，且宏观或板块至少一人认为会改变预期。期权那一票只作参考（开盘 30 分钟有没有可交易波动），
    # 不作门槛——板块级的新事实本身就是团队要的「边际高置信」。
    return {"push": fact and (votes[0] or votes[1]), "why": str(d.get("why") or "")[:60],
            "tickers": [str(t) for t in (d.get("tickers") or [])][:3],
            "votes": f"新事实{mark(fact)} 宏观{mark(votes[0])} 板块{mark(votes[1])} 期权{mark(votes[2])}", "error": None}
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "as", "at", "by", "with", "after", "over",
        "from", "is", "are", "be", "has", "have", "it", "its", "his", "her", "he", "she", "that", "this", "says", "said"}


def _log(msg: str) -> None:
    d = DATA_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "leaders.log", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now(tz=ET):%Y-%m-%d %H:%M:%S} {msg}\n")


def strip_source(title: str) -> str:
    """去掉 Google News 标题末尾的「 - 媒体名」。"""
    return re.sub(r"\s+-\s+[^-]+$", "", title).strip()


ALIAS_WORDS = {w.lower() for cfg in LEADERS.values() for a in cfg["aliases"] for w in re.findall(r"[a-z]+", a.lower())}
# 「…, says Trump」这种动词在名字前面的写法，只认紧挨着的纯说话动词，免得 "plan to dismantle Trump's agenda" 也算
SAY_BEFORE = (r"(?:says?|said|tells?|told|warns?|vows?|insists?|claims?|predicts?|promises?|threatens?|announce[sd]?"
              r"|writes|wrote|posts?|posted|according to)")


def tokens(title: str) -> set[str]:
    """标题用词（去掉停用词和人名本身，人名几乎每条都有，留着会把不相干的事判成同一件）。"""
    words = re.findall(r"[a-z0-9]+", strip_source(title).lower())
    return {w for w in words if w not in STOP and w not in ALIAS_WORDS and len(w) > 2}


# 名字后面跟这些词就不是他本人在说（"Trump administration"、"Trump's"、"Trump team"…）
NOT_HIMSELF = r"(?!['’]s\b)(?!\s+(?:administration|admin|team|officials?|aides?|campaign|allies|era|org|family|supporters?)\b)"


def speaks(title: str, aliases: list[str]) -> bool:
    """本人在说：名字后 3 个词内出现说话动词，或「…, says Trump」，或「Trump: …」。
    只看标题；别人引用、评论他的（"AI bear slams Nvidia's pitch: '…Jensen Huang'"）不算。"""
    t = strip_source(title)
    for name in aliases:
        n = re.escape(name)
        if re.search(rf"\b{n}\b{NOT_HIMSELF}\W+(?:\w+\W+){{0,3}}?{VERBS}\b", t, re.I):
            return True
        if re.search(rf"\b{SAY_BEFORE}\W+\b{n}\b", t, re.I):
            return True
        if re.search(rf"^\s*{n}\s*[:：]", t, re.I):
            return True
    return False


def fetch(query: str, timeout: int = 15) -> list[dict]:
    """Google News RSS 一条查询 → [{title, link, source, at(UTC datetime)}]。"""
    url = ("https://news.google.com/rss/search?q=" + parse.quote(f"{query} when:3h")
           + "&hl=en-US&gl=US&ceid=US:en")
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=timeout) as r:
        root = XML.fromstring(r.read())
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        try:
            at = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        out.append({"title": title, "link": (it.findtext("link") or "").strip(),
                    "source": (it.findtext("source") or "").strip(), "at": at})
    return out


def pick(items: list[dict], aliases: list[str], window_min: int) -> list[dict]:
    """只留最近 window_min 分钟内、本人在说、且和市场有关的；标出命中的关键词。"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)
    kept = []
    for it in items:
        if it["at"] < cutoff or not speaks(it["title"], aliases):
            continue
        hits = sorted({m.group(0) for m in MARKET.finditer(strip_source(it["title"]))}, key=str.lower)
        if not hits:
            continue
        kept.append({**it, "hits": hits})
    return kept


def _load_seen() -> list[dict]:
    """[{who, title, tokens, at}]，只留 TTL_H 小时内的。"""
    try:
        seen = json.loads(SEEN.read_text(encoding="utf-8"))
        if not isinstance(seen, list):
            seen = []
    except (OSError, ValueError):
        seen = []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=TTL_H)).isoformat()
    return [s for s in seen if s.get("at", "") >= cutoff]


def _save_seen(seen: list[dict]) -> None:
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


def is_dup(who: str, title: str, seen: list[dict]) -> bool:
    """同一个人 DUP_H 小时内有用词重合度 ≥ DUP_JACCARD 的标题，就算同一件事。"""
    toks = tokens(title)
    if not toks:
        return True
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=DUP_H)).isoformat()
    for s in seen:
        if s.get("who") != who or s.get("at", "") < cutoff:
            continue
        other = set(s.get("tokens") or [])
        if other and len(toks & other) / len(toks | other) >= DUP_JACCARD:
            return True
    return False


def pushed_last_hour(who: str, seen: list[dict]) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return sum(1 for s in seen if s.get("who") == who and s.get("at", "") >= cutoff and s.get("pushed", True))


def format_message(who: str, it: dict, verdict: dict | None = None) -> str:
    at = it["at"].astimezone(ET)
    src = f"（{it['source']}）" if it["source"] else ""
    lines = [f"【领袖发言·边际高置信】{who} · {at:%H:%M} ET",
             f"{strip_source(it['title'])}{src}", it["link"]]
    if verdict:
        tick = f"｜标的：{'、'.join(verdict['tickers'])}" if verdict.get("tickers") else ""
        lines.append(f"判断：{verdict['why']}（{verdict['votes']}）{tick}")
    else:
        lines.append(f"相关：{'、'.join(it['hits'])}")
    return "\n".join(lines)


def send_group(text: str) -> bool:
    sender = Path(__file__).resolve().parent.parent / "scripts" / "tg_send.py"
    r = subprocess.run([sys.executable, str(sender), "--groups-only", "--text", text],
                       check=False, capture_output=True, timeout=120)
    return r.returncode == 0


def run_once(dry: bool = False, window_min: int = WINDOW_MIN) -> int:
    """跑一轮：抓 → 筛 → 去重 → 推。返回推出的条数。"""
    seen = _load_seen()
    pushed = 0
    for who, cfg in LEADERS.items():
        try:
            items = pick(fetch(cfg["q"]), cfg["aliases"], window_min)
        except Exception as e:  # noqa: BLE001 —— 网络抖动不影响下一轮
            _log(f"{who} 抓取失败 {type(e).__name__}: {e}")
            continue
        for it in sorted(items, key=lambda x: x["at"]):
            if is_dup(who, it["title"], seen):
                continue
            if pushed_last_hour(who, seen) >= MAX_PER_HOUR:
                _log(f"{who} 一小时已推 {MAX_PER_HOUR} 条，先压下：{it['title'][:80]}")
                break
            v = judge(who, it)
            title80 = strip_source(it["title"])[:80]
            # 判过的（不管推不推）都记进 seen，同一件事不再反复送去裁判
            seen.append({"who": who, "title": strip_source(it["title"]), "tokens": sorted(tokens(it["title"])),
                         "at": datetime.now(timezone.utc).isoformat(), "pushed": False})
            if v["error"]:
                _log(f"{who} 裁判出错，不推：{v['error']}｜{title80}")
                if dry:
                    print(f"[裁判出错] {title80}：{v['error']}\n", flush=True)
                continue
            if not v["push"]:
                _log(f"{who} 裁判否决（{v['votes']}）{v['why']}｜{title80}")
                if dry:
                    print(f"[否决 {v['votes']}] {title80}｜{v['why']}\n", flush=True)
                continue
            msg = format_message(who, it, v)
            if dry:
                print(msg, "\n", flush=True)
                ok = True
            else:
                ok = send_group(msg)
                _log(f"{who} {'已推' if ok else '推送失败'}（{v['votes']}）{v['why']}｜{title80}")
            if ok:
                seen[-1]["pushed"] = True
                pushed += 1
    if not dry:
        _save_seen(seen)
    return pushed
