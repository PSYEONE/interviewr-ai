"""Session state: live transcript buffer, conversation history and answer generation."""

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
from .llm.base import STYLE_DIRECTIVES, LLMClient, Message
from .rag import Retriever, format_context

_log = logging.getLogger(__name__)
_TITLE_TIMEOUT_S = 15.0  # bound best-effort title generation so it can't hang shutdown
_FALLBACK_TITLE_WORDS = 8


def _fallback_title(turns: list[TurnRecord]) -> str:
    """A title from the first question, stored before the LLM title is attempted."""
    question = turns[0]["question"].strip() if turns else ""
    if not question:
        return ""
    words = question.split()
    title = " ".join(words[:_FALLBACK_TITLE_WORDS])
    return (title + "...") if len(words) > _FALLBACK_TITLE_WORDS else title

# Answer styles the client can switch between; only their directive text is editable.
_STYLES = ("script", "bullets")


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
        style_directives: dict[str, str] | None = None,
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
        # Conversation new turns are saved under; None until the first turn and after reset().
        self._conversation_id: int | None = None
        # In-flight best-effort title-generation tasks (one per finalized conversation).
        self._title_tasks: set[asyncio.Task[None]] = set()
        # Conversations hidden from history until their title is generated.
        self._finalizing: set[int] = set()
        # Capture runs only while an interview is active and a client is connected.
        self._capture_intent = False
        self._capture_on = asyncio.Event()
        self._capture_off = asyncio.Event()
        self._recompute_capture()
        # Answer style chosen by the client; the directive text comes from config.
        self._answer_style = "script"
        self._style_directives = dict(style_directives or STYLE_DIRECTIVES)

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
        """Conversations hidden from the history list: the open one and any still being titled."""
        ids = set(self._finalizing)
        if self._conversation_id is not None:
            ids.add(self._conversation_id)
        return ids

    def set_history_enabled(self, enabled: bool) -> None:
        """Live-toggle history persistence (applied by POST /settings/reload, no restart)."""
        self._history_enabled = enabled

    def set_answer_style(self, style: str) -> None:
        """Flip how answers are formatted (sticky until changed); applied to the next answer."""
        if style in _STYLES:
            self._answer_style = style

    def set_style_directives(self, directives: dict[str, str]) -> None:
        """Live-swap the per-style directive text (applied by POST /settings/reload)."""
        self._style_directives = dict(directives)

    def _style_directive(self) -> str:
        """Form-only instruction appended to the question for the LLM; never stored in history."""
        return self._style_directives.get(self._answer_style, "")

    def add_transcript(self, text: str) -> None:
        """Append a transcript chunk and broadcast it. Called by the producer loop."""
        self._parts.append(text)
        joined = " ".join(self._parts)
        if len(joined) > self._max_buffer_chars:
            self._parts = [joined[-self._max_buffer_chars :]]
        self._seq += 1
        self._bus.publish(TranscriptEvent(text=text, seq=self._seq))

    def publish_partial(self, text: str) -> None:
        """Broadcasts a tentative transcript tail without adding it to the buffer."""
        self._bus.publish(TranscriptEvent(text=text, final=False, seq=self._seq))

    def request_answer(self) -> None:
        """Toggle: answer from the buffered transcript + history, then clear the buffer."""
        transcript = " ".join(self._parts).strip()
        # Cleared synchronously so speech during generation goes into the next question.
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
        # RAG context and the style directive go only into this request, not the stored history.
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
        """Prepends retrieved context to the question; falls back to the bare transcript on failure."""
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
        """Saves the turn to on-disk history; failures are logged, not raised."""
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
        """Stores a fallback title, then tries to replace it with a short LLM title."""
        if self._history_store is None:
            return
        self._finalizing.add(conv_id)
        try:
            detail = self._history_store.get_conversation(conv_id)
            if detail is None or not detail["turns"] or detail["title"]:
                return
            # Store the fallback first so a failed LLM call still leaves a title.
            fallback = _fallback_title(detail["turns"])
            if fallback:
                self._history_store.set_title(conv_id, fallback)
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
        """Asks the LLM for a short title from the conversation's questions."""
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
        # Title the open conversation while the LLM is still available.
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
