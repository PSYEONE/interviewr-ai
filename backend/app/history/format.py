"""Render stored conversations as Markdown (the per-conversation "script" download)."""

from __future__ import annotations

from .store import ConversationDetail


def format_conversation_md(detail: ConversationDetail) -> str:
    heading = detail["title"] or f"Conversation {detail['id']} - {detail['started_at']}"
    lines = [f"# {heading}", ""]
    for turn in detail["turns"]:
        lines.append(f"## Q: {turn['question']}")
        lines.append("")
        lines.append(turn["answer"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_history_md(details: list[ConversationDetail]) -> str:
    if not details:
        return "# Conversation history\n\n(empty)\n"
    return "\n\n---\n\n".join(format_conversation_md(d) for d in details)
