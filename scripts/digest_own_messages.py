#!/usr/bin/env python3
"""Export ONLY the messages you sent yourself in a time range (Feishu / Lark via lark-cli).

Why only your own: a weekly report must credit your own actions. Reading other people's
messages adds noise and invites mis-attributing their work to you.

Requires: lark-cli installed and logged in (lark-cli auth login), scope search:message.

Usage:
  python3 digest_own_messages.py --start 2026-10-26T00:00:00+08:00 \
      --end 2026-11-02T00:00:00+08:00 --output /tmp/my_messages.json
  python3 digest_own_messages.py --start ... --end ... --format text
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ENV = {
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}


def run(args: list[str]) -> dict:
    env = os.environ.copy()
    env.update(ENV)
    proc = subprocess.run(args, capture_output=True, text=True, env=env)
    raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(raw[:800] or f"command failed: {' '.join(args)}")
    if proc.returncode != 0 or data.get("ok") is False:
        err = data.get("error", {})
        hint = err.get("hint") or ""
        raise SystemExit(
            f"{err.get('type', 'error')}: {err.get('message', '')}\n{hint}".strip()
        )
    return data


def walk_chats(node) -> dict:
    found: dict[str, dict] = {}

    def rec(value):
        if isinstance(value, dict):
            if "chat_id" in value:
                found[value["chat_id"]] = value
            for item in value.values():
                rec(item)
        elif isinstance(value, list):
            for item in value:
                rec(item)

    rec(node)
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp")
    parser.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp")
    parser.add_argument("--output", help="write JSON here (default: stdout)")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--limit", type=int, default=40, help="max pages")
    args = parser.parse_args()

    status = run(["lark-cli", "auth", "status", "--json", "--verify"])
    user = status.get("identities", {}).get("user", {})
    open_id = user.get("openId")
    if not open_id:
        raise SystemExit("No logged-in user. Run: lark-cli auth login")

    search = run(
        [
            "lark-cli", "im", "+messages-search",
            "--sender", open_id,
            "--start", args.start,
            "--end", args.end,
            "--page-all",
            "--page-limit", str(args.limit),
            "--no-reactions",
            "--as", "user",
            "--format", "json",
        ]
    )
    messages = [
        m for m in (search.get("data", {}) or {}).get("messages", [])
        if (m.get("sender") or {}).get("id") == open_id
    ]
    messages.sort(key=lambda m: m.get("create_time", ""))

    names: dict[str, str] = {}
    try:
        chats = run(
            [
                "lark-cli", "im", "+chat-list",
                "--types", "p2p,group",
                "--page-size", "100",
                "--as", "user",
                "--format", "json",
            ]
        )
        for cid, chat in walk_chats(chats).items():
            names[cid] = chat.get("name") or chat.get("chat_name") or cid
    except SystemExit:
        pass  # chat names are nice-to-have

    result = {
        "start": args.start,
        "end": args.end,
        "user": {"open_id": open_id, "name": user.get("userName")},
        "message_count": len(messages),
        "messages": [
            {
                "time": m.get("create_time"),
                "chat": names.get(m.get("chat_id"), m.get("chat_id")),
                "type": m.get("msg_type"),
                "text": (m.get("content") or "").strip(),
            }
            for m in messages
        ],
    }

    if args.format == "text":
        lines = [
            f"{m['time']} [{m['chat']}] {m['text']}".replace("\n", " ")
            for m in result["messages"]
        ]
        payload = "\n".join(lines)
    else:
        payload = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"{len(messages)} messages -> {args.output}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
