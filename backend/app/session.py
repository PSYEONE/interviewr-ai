"""Session state: live transcript buffer, conversation history, answer generation.

The buffer and history moved here out of the LLM client so the continuously-running
producer (which appends transcript) and the command-triggered answer path (any WS
client) share one source of truth.
"""

from __future__ import annotations

import asyncio
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
from .llm.base import LLMClient, Message
from .rag import Retriever, format_context


class Session:
    def __init__(
        self,
        bus: EventBus,
        llm: LLMClient,
        max_buffer_chars: int = 2000,
        max_history_chars: int = 3000,
        retriever: Retriever | None = None,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._retriever = retriever
        self._max_buffer_chars = max_buffer_chars
        self._max_history_chars = max_history_chars
        self._parts: list[str] = []
        self._history: list[Message] = []
        self._seq = 0
        self._gen_task: asyncio.Task[None] | None = None

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
        self._parts = []
        self._history = []
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

    async def aclose(self) -> None:
        if self._gen_task is not None and not self._gen_task.done():
            self._gen_task.cancel()
            try:
                await self._gen_task
            except asyncio.CancelledError:
                pass
