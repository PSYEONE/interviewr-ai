"""WebSocket event/command schema and an in-process broadcast bus.

The engine publishes typed events to a set of subscribers and never assumes a
single client. ``EventBus.publish`` is synchronous and never blocks, so a slow or
dead subscriber can never stall the producer loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

# Server -> client events


class TranscriptEvent(BaseModel):
    type: Literal["transcript"] = "transcript"
    text: str
    final: bool = True  # reserved for partial-hypothesis STT; whisper output is settled
    seq: int


class AnswerStartEvent(BaseModel):
    type: Literal["answer.start"] = "answer.start"
    answer_id: str
    source_chars: int


class AnswerTokenEvent(BaseModel):
    type: Literal["answer.token"] = "answer.token"
    answer_id: str
    token: str


class AnswerDoneEvent(BaseModel):
    type: Literal["answer.done"] = "answer.done"
    answer_id: str
    cancelled: bool = False


class StatusEvent(BaseModel):
    type: Literal["status"] = "status"
    state: Literal["idle", "listening", "thinking"]


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: Literal["llm_unavailable", "internal", "bad_command", "unauthorized"]
    message: str


Event = Annotated[
    Union[
        TranscriptEvent,
        AnswerStartEvent,
        AnswerTokenEvent,
        AnswerDoneEvent,
        StatusEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]

# Client -> server commands


class AnswerCommand(BaseModel):
    type: Literal["answer"] = "answer"


class ClearCommand(BaseModel):
    type: Literal["clear"] = "clear"


class ResetCommand(BaseModel):
    type: Literal["reset"] = "reset"


class PingCommand(BaseModel):
    type: Literal["ping"] = "ping"


Command = Annotated[
    Union[AnswerCommand, ClearCommand, ResetCommand, PingCommand],
    Field(discriminator="type"),
]

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


def parse_command(raw: str) -> Command:
    """Parse one inbound WS frame into a Command. Raises ValidationError on junk."""
    return _COMMAND_ADAPTER.validate_json(raw)


# Broadcast bus


@dataclass(eq=False)
class Subscription:
    queue: asyncio.Queue[Event]
    _bus: EventBus
    dropped: int = 0

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        return await self.queue.get()

    async def aclose(self) -> None:
        self._bus.unsubscribe(self)


class EventBus:
    """Fan-out to per-subscriber bounded queues. ``publish`` never awaits."""

    def __init__(self, maxsize: int = 256) -> None:
        self._subs: set[Subscription] = set()
        self._maxsize = maxsize

    def subscribe(self) -> Subscription:
        sub = Subscription(queue=asyncio.Queue(maxsize=self._maxsize), _bus=self)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def publish(self, event: Event) -> None:
        for sub in self._subs:
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()  # drop oldest
                except asyncio.QueueEmpty:
                    pass
                sub.queue.put_nowait(event)  # newest always lands
                sub.dropped += 1

    def subscriber_count(self) -> int:
        return len(self._subs)
