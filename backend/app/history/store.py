"""Local conversation-history store: answered Q&A turns grouped into conversations.

Plain sqlite, mirroring ``app/rag/store.py`` (row factory, executescript schema, ``with`` writes,
TypedDict records). Turns are short text, so there is no vector/index machinery here. A
conversation groups the turns answered between resets/boots; an optional LLM-generated ``title``
labels it and stays NULL until finalized, in which case the UI falls back to the first question.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    title      TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    asked_at        TEXT NOT NULL,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL
);
"""


class TurnRecord(TypedDict):
    id: int
    asked_at: str
    question: str
    answer: str


class ConversationRecord(TypedDict):
    id: int
    started_at: str
    title: str | None
    turn_count: int
    preview: str  # first question; the list UI uses it as a fallback label


class ConversationDetail(TypedDict):
    id: int
    started_at: str
    title: str | None
    turns: list[TurnRecord]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")  # so deleting a conversation drops its turns
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def start_conversation(self) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO conversations (started_at, title) VALUES (?, NULL)", (_now(),)
            )
        return int(cast(int, cur.lastrowid))

    def add_turn(self, conversation_id: int, question: str, answer: str) -> TurnRecord:
        asked_at = _now()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO turns (conversation_id, asked_at, question, answer) VALUES (?, ?, ?, ?)",
                (conversation_id, asked_at, question, answer),
            )
        return TurnRecord(
            id=int(cast(int, cur.lastrowid)), asked_at=asked_at, question=question, answer=answer
        )

    def set_title(self, conversation_id: int, title: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
            )

    def list_conversations(self) -> list[ConversationRecord]:
        rows = self._conn.execute(
            "SELECT c.id, c.started_at, c.title, COUNT(t.id) AS turn_count, "
            "  (SELECT question FROM turns WHERE conversation_id = c.id ORDER BY id LIMIT 1) "
            "    AS preview "
            "FROM conversations c LEFT JOIN turns t ON t.conversation_id = c.id "
            "GROUP BY c.id ORDER BY c.id DESC"
        ).fetchall()
        return [
            ConversationRecord(
                id=int(r["id"]),
                started_at=cast(str, r["started_at"]),
                title=cast("str | None", r["title"]),
                turn_count=int(r["turn_count"]),
                preview=cast(str, r["preview"]) if r["preview"] is not None else "",
            )
            for r in rows
        ]

    def get_conversation(self, conversation_id: int) -> ConversationDetail | None:
        conv = self._conn.execute(
            "SELECT id, started_at, title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conv is None:
            return None
        turns = self._conn.execute(
            "SELECT id, asked_at, question, answer FROM turns "
            "WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return ConversationDetail(
            id=int(conv["id"]),
            started_at=cast(str, conv["started_at"]),
            title=cast("str | None", conv["title"]),
            turns=[
                TurnRecord(
                    id=int(t["id"]),
                    asked_at=cast(str, t["asked_at"]),
                    question=cast(str, t["question"]),
                    answer=cast(str, t["answer"]),
                )
                for t in turns
            ],
        )

    def delete_conversation(self, conversation_id: int) -> bool:
        with self._conn:
            cur = self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0

    def clear(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM conversations")

    def export(self) -> list[ConversationDetail]:
        ids = [
            int(r["id"])
            for r in self._conn.execute("SELECT id FROM conversations ORDER BY id").fetchall()
        ]
        return [d for cid in ids if (d := self.get_conversation(cid)) is not None]

    def close(self) -> None:
        self._conn.close()


__all__ = ["ConversationDetail", "ConversationRecord", "HistoryStore", "TurnRecord"]
