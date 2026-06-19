"""Unit tests for the conversation HistoryStore (plain sqlite, no network)."""

from __future__ import annotations

from pathlib import Path

from app.history.format import format_conversation_md
from app.history.store import HistoryStore


def _store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.sqlite3")


def test_add_turns_and_list(tmp_path: Path) -> None:
    s = _store(tmp_path)
    cid = s.start_conversation()
    s.add_turn(cid, "What is your experience?", "Ten years.")
    s.add_turn(cid, "Why us?", "Mission fit.")

    convs = s.list_conversations()
    assert len(convs) == 1
    assert convs[0]["id"] == cid
    assert convs[0]["turn_count"] == 2
    assert convs[0]["preview"] == "What is your experience?"
    assert convs[0]["title"] is None  # untitled until finalized
    s.close()


def test_get_conversation_orders_turns(tmp_path: Path) -> None:
    s = _store(tmp_path)
    cid = s.start_conversation()
    s.add_turn(cid, "q1", "a1")
    s.add_turn(cid, "q2", "a2")
    detail = s.get_conversation(cid)
    assert detail is not None
    assert [t["question"] for t in detail["turns"]] == ["q1", "q2"]
    assert s.get_conversation(9999) is None
    s.close()


def test_set_title(tmp_path: Path) -> None:
    s = _store(tmp_path)
    cid = s.start_conversation()
    s.add_turn(cid, "q", "a")
    s.set_title(cid, "Backend role screen")
    assert s.list_conversations()[0]["title"] == "Backend role screen"
    s.close()


def test_delete_cascades_turns(tmp_path: Path) -> None:
    s = _store(tmp_path)
    cid = s.start_conversation()
    s.add_turn(cid, "q", "a")
    assert s.delete_conversation(cid) is True
    assert s.list_conversations() == []
    assert s.get_conversation(cid) is None
    assert s.delete_conversation(cid) is False  # already gone
    s.close()


def test_clear_and_export(tmp_path: Path) -> None:
    s = _store(tmp_path)
    c1 = s.start_conversation()
    s.add_turn(c1, "q1", "a1")
    c2 = s.start_conversation()
    s.add_turn(c2, "q2", "a2")

    exported = s.export()
    assert [d["id"] for d in exported] == [c1, c2]  # ascending id
    assert exported[0]["turns"][0]["answer"] == "a1"

    s.clear()
    assert s.list_conversations() == []
    assert s.export() == []
    s.close()


def test_format_conversation_md_untitled_fallback(tmp_path: Path) -> None:
    s = _store(tmp_path)
    cid = s.start_conversation()
    s.add_turn(cid, "Tell me about yourself", "I build Linux tools.")
    detail = s.get_conversation(cid)
    assert detail is not None
    md = format_conversation_md(detail)
    assert md.startswith(f"# Conversation {cid} - ")  # untitled -> id + date heading
    assert "## Q: Tell me about yourself" in md
    assert "I build Linux tools." in md
    s.close()
