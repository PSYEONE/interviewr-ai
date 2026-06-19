"""Session state: live transcript buffer, conversation history, answer generation.

The buffer and history moved here out of the LLM client so the continuously-running
producer (which appends transcript) and the command-triggered answer path (any WS
client) share one source of truth.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from .events import (
    AnswerDoneEvent,
    AnswerStartEvent,
    AnswerTokenEvent,
    ErrorEvent,
    EventBus,
    StatusEvent,
    TranscriptEvent,
)
from .history import HistoryStore, TurnRecord
from .llm.base import LLMClient, Message
from .rag import Retriever, format_context

_log = logging.getLogger(__name__)
_TITLE_TIMEOUT_S = 15.0  # bound best-effort title generation so it can't hang shutdown


class Session:
    def __init__(
        self,
        bus: EventBus,
        llm: LLMClient,
        max_buffer_chars: int = 2000,
        max_history_chars: int = 3000,
        retriever: Retriever | None = None,
        history: HistoryStore | None = None,
        history_enabled: bool = True,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._retriever = retriever
        self._history_store = history
        self._history_enabled = history_enabled
        self._max_buffer_chars = max_buffer_chars
        self._max_history_chars = max_history_chars
        self._parts: list[str] = []
        self._history: list[Message] = []
        self._seq = 0
        self._gen_task: asyncio.Task[None] | None = None
        # The conversation answered turns are persisted under; None until the first turn, and
        # reset to None on reset() so a new topic opens a fresh conversation.
        self._conversation_id: int | None = None
        # In-flight best-effort title-generation tasks (one per finalized conversation).
        self._title_tasks: set[asyncio.Task[None]] = set()

    def add_transcript(self, text: str) -> None:
        """Append a transcript chunk and broadcast it. Called by the producer loop."""
        self._parts.append(text)
        joined = " ".join(self._parts)
        if len(joined) > self._max_buffer_chars:
            self._parts = [joined[-self._max_buffer_chars :]]
        self._seq += 1
        self._bus.publish(TranscriptEvent(text=text, seq=self._seq))

    def publish_partial(self, text: str) -> None:
        """Broadcast a tentative (not-yet-settled) transcript tail. Deliberately does
        not touch _parts/_history, so the LLM only ever answers on committed text."""
        self._bus.publish(TranscriptEvent(text=text, final=False, seq=self._seq))

    def request_answer(self) -> None:
        """Toggle: answer from the buffered transcript + history, then clear the buffer."""
        transcript = " ".join(self._parts).strip()
        # Snapshot + clear synchronously (no await between) so speech that arrives during
        # generation accumulates into the next question instead of being lost.
        self._parts = []
        if not transcript:
            self._bus.publish(
                ErrorEvent(code="bad_command", message="nothing to answer yet")
            )
            return
        if self._gen_task is not None and not self._gen_task.done():
            self._gen_task.cancel()
        self._gen_task = asyncio.create_task(self._run_answer(transcript))

    def clear(self) -> None:
        """Drop the pending (possibly mis-heard) transcript without generating."""
        self._parts = []
        self._bus.publish(StatusEvent(state="listening"))

    def reset(self) -> None:
        """Start a fresh topic: drop the buffer and the conversation history."""
        if self._gen_task is not None and not self._gen_task.done():
            self._gen_task.cancel()
        self._finalize_current()  # title the conversation we are closing (background)
        self._parts = []
        self._history = []
        self._conversation_id = None  # the next answered turn opens a new conversation
        self._bus.publish(StatusEvent(state="listening"))

    async def _run_answer(self, transcript: str) -> None:
        answer_id = uuid.uuid4().hex
        self._bus.publish(StatusEvent(state="thinking"))
        self._bus.publish(
            AnswerStartEvent(answer_id=answer_id, source_chars=len(transcript))
        )
        # Retrieved RAG context augments only the message sent to the LLM; history keeps the
        # raw transcript (below) so each turn retrieves fresh and context never accretes.
        prompt = await self._with_context(transcript)
        messages: list[Message] = [*self._history, {"role": "user", "content": prompt}]
        tokens: list[str] = []
        cancelled = False
        try:
            async for token in self._llm.generate(messages):
                tokens.append(token)
                self._bus.publish(AnswerTokenEvent(answer_id=answer_id, token=token))
            self._commit_history(transcript, "".join(tokens))
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:  # ollama down at runtime, bad response, etc.
            self._bus.publish(ErrorEvent(code="llm_unavailable", message=str(exc)))
        finally:
            self._bus.publish(AnswerDoneEvent(answer_id=answer_id, cancelled=cancelled))
            self._bus.publish(StatusEvent(state="listening"))

    async def _with_context(self, transcript: str) -> str:
        """Prepend retrieved resume/JD context to the question. Best-effort: any failure
        falls back to the bare transcript so RAG can never break an answer."""
        if self._retriever is None:
            return transcript
        try:
            chunks = await self._retriever.retrieve(transcript)
        except Exception:  # noqa: BLE001 - retrieval is an enhancement, not a dependency
            return transcript
        context = format_context(chunks)
        return f"{context}{transcript}" if context else transcript

    def _commit_history(self, transcript: str, answer: str) -> None:
        if not answer:
            return
        self._history.append({"role": "user", "content": transcript})
        self._history.append({"role": "assistant", "content": answer})
        # Drop oldest user/assistant pairs while over the char budget.
        while (
            len(self._history) > 2
            and sum(len(m["content"]) for m in self._history) > self._max_history_chars
        ):
            del self._history[:2]
        self._persist_turn(transcript, answer)

    def _persist_turn(self, question: str, answer: str) -> None:
        """Append the completed turn to the on-disk history. Best-effort: a storage failure must
        never break answering (same stance as RAG)."""
        if not self._history_enabled or self._history_store is None:
            return
        try:
            if self._conversation_id is None:
                self._conversation_id = self._history_store.start_conversation()
            self._history_store.add_turn(self._conversation_id, question, answer)
        except Exception as exc:  # noqa: BLE001 - persistence is not a dependency of answering
            _log.warning("failed to persist history turn: %s", exc)

    def _finalize_current(self) -> None:
        """Schedule background title generation for the current conversation, if any."""
        conv_id = self._conversation_id
        if conv_id is None or self._history_store is None or not self._history_enabled:
            return
        task = asyncio.create_task(self._finalize_conversation(conv_id))
        self._title_tasks.add(task)
        task.add_done_callback(self._title_tasks.discard)

    async def _finalize_conversation(self, conv_id: int) -> None:
        """Generate and store a short LLM title for a conversation. Best-effort and bounded; on
        any failure the title stays NULL and the UI falls back to the first question."""
        if self._history_store is None:
            return
        try:
            detail = self._history_store.get_conversation(conv_id)
            if detail is None or not detail["turns"] or detail["title"]:
                return
            title = await asyncio.wait_for(
                self._generate_title(detail["turns"]), timeout=_TITLE_TIMEOUT_S
            )
            if title:
                self._history_store.set_title(conv_id, title)
        except Exception as exc:  # noqa: BLE001 - a title is a nicety; never disrupt the engine
            _log.warning("conversation title finalize failed: %s", exc)

    async def _generate_title(self, turns: list[TurnRecord]) -> str:
        """Ask the LLM for a short label from the conversation's questions. The adapter prepends
        its own system prompt, so the instruction is explicit to keep the output title-only."""
        questions = "\n".join(f"- {t['question']}" for t in turns[:8])
        prompt = (
            "Summarize this interview/meeting conversation as a short title of 3 to 6 words. "
            "Respond with only the title, with no quotes or trailing punctuation.\n\n"
            f"Questions discussed:\n{questions}"
        )
        tokens: list[str] = []
        async for token in self._llm.generate([{"role": "user", "content": prompt}]):
            tokens.append(token)
        raw = "".join(tokens).strip().strip('"').strip()
        if not raw:
            return ""
        return raw.splitlines()[0].strip()[:80]

    async def aclose(self) -> None:
        if self._gen_task is not None and not self._gen_task.done():
            self._gen_task.cancel()
            try:
                await self._gen_task
            except asyncio.CancelledError:
                pass
        # Title the conversation we are leaving (the LLM is still open at this point in the
        # lifespan teardown). Bounded by the per-call timeout; failure just leaves it untitled.
        conv_id = self._conversation_id
        if conv_id is not None and self._history_store is not None and self._history_enabled:
            await self._finalize_conversation(conv_id)
        # Stop any background title tasks still running from earlier resets.
        for task in list(self._title_tasks):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
