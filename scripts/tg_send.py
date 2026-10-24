#!/usr/bin/env python3
"""发文字和/或文件到 Telegram 的全部目的地（~/.config/telegram/chat_id 一行一个）。

用法：tg_send.py [--text "..."|--text-file 路径] [--file 路径 ...] [--caption "..."] [--only-first]
凭据只从 ~/.config/telegram 读；任何输出不含 token。
"""
import argparse, json, mimetypes, sys, uuid
from pathlib import Path
from urllib import request, parse

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
    a = ap.parse_args()
    token = (CFG / "bot_token").read_text().strip()
    text = Path(a.text_file).read_text(encoding="utf-8") if a.text_file else a.text
    ok_all = True
    for chat in destinations(a.only_first, a.groups_only):
        tag = f"{chat[:4]}…"
        if text:
            for i in range(0, len(text), 3900):          # Telegram 单条上限 4096
                ok = send_text(token, chat, text[i:i + 3900]); ok_all &= ok
                print(f"text -> {tag}: {'ok' if ok else 'FAIL'}")
        for f in a.file:
            ok = send_file(token, chat, Path(f), a.caption); ok_all &= ok
            print(f"file {Path(f).name} -> {tag}: {'ok' if ok else 'FAIL'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
