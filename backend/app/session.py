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
    ConversationTitledEvent,
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

# Form-only answer-style directives appended to the question (the client flips between them).
_STYLE_DIRECTIVES = {
    "script": (
        "STYLE: Answer as a natural first-person spoken script, what the candidate would say "
        "out loud. Conversational and flowing. No bullet points, lists, or headers."
    ),
    "bullets": (
        "STYLE: Answer as 3 to 6 concise bullet points, each a single line to glance at and "
        "expand on. No long paragraphs."
    ),
}


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
        # Conversations whose finalization (title generation) is in flight; hidden from the
        # history list until the title lands so the raw first question is never shown as a title.
        self._finalizing: set[int] = set()
        # Capture gating. The producer captures system audio only while _capture_on is set.
        # Capture runs when intent is set (inside an interview) AND a client is connected; the
        # subscriber gate stops a manually-run backend from capturing once the last client goes.
        self._capture_intent = False
        self._capture_on = asyncio.Event()
        self._capture_off = asyncio.Event()
        self._recompute_capture()
        # Answer formatting, flipped live by the client (sticky until changed).
        self._answer_style = "script"

    def _recompute_capture(self) -> None:
        active = self._capture_intent and self._bus.subscriber_count() > 0
        if active:
            self._capture_off.clear()
            self._capture_on.set()
        else:
            self._capture_on.clear()
            self._capture_off.set()

    def resume_capture(self) -> None:
        """Intend to capture system audio (sent when entering an interview)."""
        self._capture_intent = True
        self._recompute_capture()

    def pause_capture(self) -> None:
        """Stop intending to capture (sent when leaving/finishing an interview)."""
        self._capture_intent = False
        self._recompute_capture()

    def notify_subscribers_changed(self) -> None:
        """Re-evaluate capture after a client connects/disconnects (WS lifecycle)."""
        self._recompute_capture()

    async def wait_capture_start(self) -> None:
        await self._capture_on.wait()

    async def wait_capture_stop(self) -> None:
        await self._capture_off.wait()

    @property
    def pending_conversation_ids(self) -> set[int]:
        """Conversations the history list hides: the open (not-yet-finalized) one and any whose
        title is still being generated. They appear once finalized and titled."""
        ids = set(self._finalizing)
        if self._conversation_id is not None:
            ids.add(self._conversation_id)
        return ids

    def set_history_enabled(self, enabled: bool) -> None:
        """Live-toggle history persistence (applied by POST /settings/reload, no restart)."""
        self._history_enabled = enabled

    def set_answer_style(self, style: str) -> None:
        """Flip how answers are formatted (sticky until changed); applied to the next answer."""
        if style in _STYLE_DIRECTIVES:
            self._answer_style = style

    def _style_directive(self) -> str:
        """Form-only instruction appended to the question for the LLM; never stored in history."""
        return _STYLE_DIRECTIVES.get(self._answer_style, "")

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
            AnswerStartEvent(
                answer_id=answer_id, question=transcript, source_chars=len(transcript)
            )
        )
        # Retrieved RAG context augments only the message sent to the LLM; history keeps the
        # raw transcript (below) so each turn retrieves fresh and context never accretes. The
        # answer-style directive rides the same ephemeral message, never the stored history.
        prompt = await self._with_context(transcript)
        directive = self._style_directive()
        if directive:
            prompt = f"{prompt}\n\n{directive}"
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
        self._finalizing.add(conv_id)  # hide from history until the title lands
        task = asyncio.create_task(self._finalize_conversation(conv_id))
        self._title_tasks.add(task)
        task.add_done_callback(self._title_tasks.discard)

    async def _finalize_conversation(self, conv_id: int) -> None:
        """Generate and store a short LLM title for a conversation. Best-effort and bounded; on
        any failure the title stays NULL and the UI falls back to the first question."""
        if self._history_store is None:
            return
        self._finalizing.add(conv_id)
        try:
            detail = self._history_store.get_conversation(conv_id)
            if detail is None or not detail["turns"] or detail["title"]:
                return
            title = await asyncio.wait_for(
                self._generate_title(detail["turns"]), timeout=_TITLE_TIMEOUT_S
            )
            if title:
                self._history_store.set_title(conv_id, title)
                self._bus.publish(
                    ConversationTitledEvent(conversation_id=conv_id, title=title)
                )
        except Exception as exc:  # noqa: BLE001 - a title is a nicety; never disrupt the engine
            _log.warning("conversation title finalize failed: %s", exc)
        finally:
            self._finalizing.discard(conv_id)

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
