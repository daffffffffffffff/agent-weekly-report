#!/usr/bin/env python3
"""Scan AI agent session logs (Codex / Claude Code / Cursor style) for a date range.

Outputs a single JSON file with user messages and final assistant replies per session,
so a report-writing agent can work from evidence instead of re-reading raw logs.

Usage:
  python3 scan_agent_sessions.py --start 2026-10-26 --end 2026-11-02 --output /tmp/sessions.json
  python3 scan_agent_sessions.py --start 2026-10-26 --end 2026-11-02 --user-only --format text
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_ROOTS = [
    "~/.codex/sessions",
    "~/.codex/archived_sessions",
    "~/.claude/projects",
    "~/.cursor/projects",
]

SKIP_PREFIXES = (
    "# AGENTS.md",
    "# CLAUDE.md",
    "<environment_context>",
    "<app-context>",
    "<system-reminder>",
    "Current runtime context.",
    "This session is being continued",
    "Caveat:",
    "<turn_aborted>",
    "<local-command-stdout>",
    "<command-name>",
    "<heartbeat>",
)

MAX_CHARS_DEFAULT = 4000


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="inclusive date, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="exclusive date, YYYY-MM-DD")
    p.add_argument("--root", action="append", default=None, help="extra session root (repeatable)")
    p.add_argument("--timezone", default="Asia/Shanghai")
    p.add_argument("--output", help="write JSON here (default: stdout)")
    p.add_argument("--user-only", action="store_true", help="keep only the human's own messages")
    p.add_argument("--max-chars", type=int, default=MAX_CHARS_DEFAULT)
    p.add_argument("--format", choices=["json", "text"], default="json")
    return p.parse_args()


def text_of_content(content) -> str:
    if isinstance(content, str):
        return content.strip()
    parts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "input_text", "output_text"} and item.get("text"):
                    parts.append(item["text"])
    return "\n".join(parts).strip()


def clean(text: str) -> str:
    if "## My request:" in text:
        text = text.split("## My request:", 1)[1]
    return text.strip()


def should_skip(role: str, text: str) -> bool:
    if not text:
        return True
    if text.startswith(SKIP_PREFIXES):
        return True
    # tool results and long pasted blobs add noise, not evidence
    if text.startswith("<") and ("tool_result" in text[:300] or "tool_use" in text[:300]):
        return True
    return False


def parse_file(path: Path, start: datetime, end: datetime, tz, max_chars: int) -> dict | None:
    """Return {session, cwd, messages} or None. Handles codex + claude/cursor shapes."""
    messages: list[dict] = []
    cwd = None
    session_id = path.stem

    try:
        handle = path.open(errors="ignore")
    except OSError:
        return None

    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_raw = record.get("timestamp") or record.get("time")
            if not ts_raw:
                continue
            try:
                if isinstance(ts_raw, (int, float)):
                    stamp = datetime.fromtimestamp(
                        ts_raw / 1000 if ts_raw > 1e11 else ts_raw, tz
                    )
                else:
                    stamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).astimezone(tz)
            except (ValueError, OSError):
                continue
            if not (start <= stamp < end):
                continue

            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            role = None
            text = ""
            subtype = record.get("type")

            if subtype == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if role == "assistant" and payload.get("phase") not in (None, "final_answer"):
                    continue
                text = text_of_content(payload.get("content"))
            elif subtype in {"user", "assistant"}:
                if record.get("isSidechain") or record.get("isMeta"):
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if subtype == "user":
                    origin = (record.get("origin") or {}).get("kind")
                    if origin not in (None, "human"):
                        continue
                    if record.get("toolUseResult") is not None:
                        continue
                text = text_of_content(message.get("content"))
            elif subtype == "session_meta":
                cwd = cwd or payload.get("cwd")
                continue
            else:
                continue

            if role not in {"user", "assistant"}:
                continue
            text = clean(text)
            if should_skip(role, text):
                continue
            cwd = cwd or record.get("cwd")
            messages.append(
                {
                    "timestamp": stamp.isoformat(timespec="minutes"),
                    "role": role,
                    "text": text[:max_chars],
                }
            )

    if not messages:
        return None
    return {"session": session_id, "cwd": cwd, "source": str(path), "messages": messages}


def main() -> None:
    args = parse_args()
    tz = ZoneInfo(args.timezone)
    start = datetime.combine(datetime.fromisoformat(args.start).date(), time.min, tz)
    end = datetime.combine(datetime.fromisoformat(args.end).date(), time.min, tz)

    roots = args.root or DEFAULT_ROOTS
    files: list[Path] = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.exists():
            continue
        files.extend(sorted(base.rglob("*.jsonl")))

    sessions = []
    seen = set()
    for path in files:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        if "subagents" in str(path):
            continue
        found = parse_file(path, start, end, tz, args.max_chars)
        if found:
            sessions.append(found)

    if args.user_only:
        for sess in sessions:
            sess["messages"] = [m for m in sess["messages"] if m["role"] == "user"]
        sessions = [s for s in sessions if s["messages"]]

    sessions.sort(key=lambda s: s["messages"][0]["timestamp"])
    result = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": args.timezone,
        "session_count": len(sessions),
        "message_count": sum(len(s["messages"]) for s in sessions),
        "sessions": sessions,
    }

    if args.format == "text":
        lines = []
        for sess in sessions:
            lines.append(f"### {sess['session']} ({sess['cwd'] or 'unknown cwd'})")
            for msg in sess["messages"]:
                tag = "U" if msg["role"] == "user" else "A"
                lines.append(f"[{msg['timestamp']}] {tag}: {msg['text']}".replace("\n", " "))
            lines.append("")
        payload = "\n".join(lines)
    else:
        payload = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(
            f"scanned {len(seen)} files -> {len(sessions)} sessions, "
            f"{result['message_count']} messages -> {args.output}"
        )
    else:
        print(payload)


if __name__ == "__main__":
    main()
