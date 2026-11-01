#!/usr/bin/env python3
"""Lint a weekly report draft against the skill's output contract.

Checks: section count, bullets per section, body length, banned filler words,
next-week item count, and "grouped by tool" anti-pattern.

Usage:
  python3 report_lint.py draft.md
  python3 report_lint.py draft.md --max-chars 800 --max-chapters 5
Exit code 0 = pass, 1 = violations.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BANNED = [
    "赋能", "闭环", "抓手", "联动", "全方位", "持续推进", "深度参与", "生态位",
    "形成了体系", "总的来说", "综上所述", "希望对你有所帮助", "持续关注",
    "基本完成", "差不多了", "稳步推进",
]

TOOL_SECTIONS = ["codex", "claude", "cursor", "飞书", "云效", "钉钉", "ai 工具", "聊天记录", "项目管理平台"]


def strip_headings(text: str) -> str:
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--max-chapters", type=int, default=5)
    parser.add_argument("--min-chapters", type=int, default=3)
    parser.add_argument("--max-bullets", type=int, default=5)
    args = parser.parse_args()

    text = Path(args.file).read_text(encoding="utf-8")
    body = strip_headings(text)
    body_chars = len(re.sub(r"\s", "", body))

    problems: list[str] = []

    if "本周工作" not in text:
        problems.append("缺少「本周工作内容」部分")
    if "下周" not in text:
        problems.append("缺少「下周工作内容」部分")

    # chapters = ### headings inside 本周工作内容
    chapters = re.findall(r"^###\s+(.+)$", text, flags=re.M)
    if not (args.min_chapters <= len(chapters) <= args.max_chapters):
        problems.append(
            f"项目章节 {len(chapters)} 个，要求 {args.min_chapters}-{args.max_chapters} 个"
        )

    for name in chapters:
        lowered = name.lower()
        for tool in TOOL_SECTIONS:
            if tool in lowered:
                problems.append(f"章节「{name}」像是按工具分组，应按项目分组")

    # bullets per chapter
    blocks = re.split(r"^###\s+", text, flags=re.M)[1:]
    for block in blocks:
        title = block.splitlines()[0].strip()
        bullets = len(re.findall(r"^\s*[-*]\s+", block, flags=re.M))
        if bullets > args.max_bullets:
            problems.append(f"「{title}」有 {bullets} 条，上限 {args.max_bullets} 条")

    if body_chars > args.max_chars:
        problems.append(f"正文 {body_chars} 字，上限 {args.max_chars} 字")

    for word in BANNED:
        if word in text:
            problems.append(f"出现空泛表达：「{word}」")

    # next-week items
    next_part = text.split("下周", 1)[1] if "下周" in text else ""
    next_items = len(re.findall(r"^\s*(?:\d+[.)]|[-*])\s+", next_part, flags=re.M))
    if not (3 <= next_items <= 6):
        problems.append(f"下周工作 {next_items} 条，要求 3-6 条")

    if problems:
        print(f"FAIL ({len(problems)} 项待改) — {args.file}")
        for item in problems:
            print(f"  - {item}")
        sys.exit(1)

    print(f"PASS — {len(chapters)} 章 / {body_chars} 字 / 下周 {next_items} 条")


if __name__ == "__main__":
    main()
