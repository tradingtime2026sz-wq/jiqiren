#!/usr/bin/env python3
"""发文字和/或文件到 Telegram 的全部目的地（~/.config/telegram/chat_id 一行一个）。

用法：tg_send.py [--text "..."|--text-file 路径] [--file 路径 ...] [--caption "..."] [--groups-only|--all]
凭据只从 ~/.config/telegram 读；任何输出不含 token。

2026-11-10（river：打回要变成红灯）：
- 默认只发私聊（chat_id 第一行）；发群要明说 --groups-only，全发要 --all。--only-first 保留，等于默认。
- 文字发出前过一遍发出前检查（规则表在团队仓库 automation/outgoing-rules.json）：有红灯就不发，
  打印「BLOCKED（没发）」和每条怎么改，退出码 3；改掉再发。黄灯照发，发完提示一句。
- --templated：文字是代码拼出来的固定模板（不是模型现写的）时用，照查照记账，但不拦。
"""
import argparse, json, mimetypes, sys, uuid
from pathlib import Path
from urllib import request, parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outgate  # noqa: E402

CFG = Path.home() / ".config/telegram"


def destinations(only_first: bool = False, groups_only: bool = False) -> list[str]:
    """chat_id 文件一行一个目的地：正数 = 私聊，负数或 @ 开头 = 群/频道。"""
    rows = [x.strip() for x in (CFG / "chat_id").read_text().splitlines() if x.strip() and not x.strip().startswith("#")]
    if groups_only:
        return [r for r in rows if r.startswith(("-", "@"))]
    return rows[:1] if only_first else rows


def api(token: str, method: str, data: bytes, content_type: str) -> bool:
    req = request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data, method="POST",
                          headers={"Content-Type": content_type})
    try:
        with request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:  # 异常对象可能含 URL（含 token），不打印
        return False


def send_text(token: str, chat: str, text: str) -> bool:
    body = parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    return api(token, "sendMessage", body, "application/x-www-form-urlencoded")


def send_file(token: str, chat: str, path: Path, caption: str) -> bool:
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption[:1000])):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
                  f'Content-Type: {ctype}\r\n\r\n').encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return api(token, "sendDocument", b"".join(parts), f"multipart/form-data; boundary={boundary}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text"); ap.add_argument("--text-file"); ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--caption", default=""); ap.add_argument("--only-first", action="store_true")
    ap.add_argument("--groups-only", action="store_true", help="只发群/频道，不发私聊")
    ap.add_argument("--all", action="store_true", help="私聊和群都发（默认只发私聊）")
    ap.add_argument("--profile", default="telegram", choices=["telegram", "card"], help="按哪套规则查")
    ap.add_argument("--source", default="", help="谁在发（齿轮名），记进检查账本")
    ap.add_argument("--templated", action="store_true", help="代码拼的固定模板：照查照记，不拦")
    a = ap.parse_args()
    text = Path(a.text_file).read_text(encoding="utf-8") if a.text_file else a.text
    checked = "\n".join(x for x in (text, a.caption) if x)
    note = ""
    if checked:
        ok, note = outgate.lint(checked, a.profile, a.source or "tg_send", enforce=not a.templated)
        if not ok and not a.templated:
            print("BLOCKED（没发）：发出前检查有红灯。\n" + note)
            return 3
    token = (CFG / "bot_token").read_text().strip()
    if a.groups_only:
        dests = destinations(groups_only=True)
    elif a.all:
        dests = destinations()
    else:
        dests = destinations(only_first=True)
    ok_all = True
    for chat in dests:
        tag = f"{chat[:4]}…"
        if text:
            for i in range(0, len(text), 3900):          # Telegram 单条上限 4096
                ok = send_text(token, chat, text[i:i + 3900]); ok_all &= ok
                print(f"text -> {tag}: {'ok' if ok else 'FAIL'}")
        for f in a.file:
            ok = send_file(token, chat, Path(f), a.caption); ok_all &= ok
            print(f"file {Path(f).name} -> {tag}: {'ok' if ok else 'FAIL'}")
    if note:
        print("已发。检查结果：\n" + note)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
