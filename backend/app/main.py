"""FastAPI entrypoint: lifespan-managed engine + authed WebSocket broadcast."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .config import Settings, get_settings
from .events import (
    AnswerCommand,
    ClearCommand,
    ErrorEvent,
    EventBus,
    PingCommand,
    ResetCommand,
    StatusEvent,
    Subscription,
    parse_command,
)
from .llm import build_llm
from .llm.base import LLMClient
from .pipeline import run_producer
from .session import Session

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_DEAD_SUBSCRIBER_DROPS = 500  # close a writer that has fallen this far behind


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not settings.llm_model:
        raise RuntimeError("INTERVIEWR_LLM_MODEL is not set")

    llm = build_llm(settings)
    if not await llm.check_health():
        raise RuntimeError(f"LLM provider '{settings.llm_provider}' health check failed")

    bus = EventBus(maxsize=settings.event_queue_maxsize)
    session = Session(
        bus,
        llm,
        max_buffer_chars=settings.max_buffer_chars,
        max_history_chars=settings.max_history_chars,
    )
    app.state.settings = settings
    app.state.bus = bus
    app.state.session = session

    # Warm the LLM in the background. Live transcription must not wait on the model
    # loading into VRAM (~seconds at boot); the LLM is only needed once a client asks
    # for an answer, and keep_alive holds it warm after that.
    warmup_task: asyncio.Task[None] = asyncio.create_task(_warm_llm(llm))
    producer: asyncio.Task[None] = asyncio.create_task(run_producer(settings, session, bus))
    try:
        yield
    finally:
        producer.cancel()
        warmup_task.cancel()
        for task in (producer, warmup_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await session.aclose()
        await llm.close()


async def _warm_llm(llm: LLMClient) -> None:
    """Best-effort LLM warmup; a failure here must not stop live transcription."""
    try:
        await llm.warmup()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("LLM warmup failed: %s", exc)


app = FastAPI(title="interviewr-ai", version="0.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "remote_viewer": str(settings.enable_remote_viewer),
    }


def _token_ok(settings: Settings, token: str) -> bool:
    if not settings.auth_token:
        # No token configured: allow only on a localhost bind (dev convenience). A
        # non-local bind requires a token via the enable_remote_viewer guard in config.
        return settings.host == "127.0.0.1"
    return hmac.compare_digest(token, settings.auth_token)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    settings: Settings = ws.app.state.settings
    bus: EventBus = ws.app.state.bus
    session: Session = ws.app.state.session

    if not _token_ok(settings, ws.query_params.get("token", "")):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()

    sub = bus.subscribe()
    bus.publish(StatusEvent(state="listening"))
    reader = asyncio.create_task(_reader(ws, session))
    writer = asyncio.create_task(_writer(ws, sub))
    try:
        _, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        bus.unsubscribe(sub)


async def _writer(ws: WebSocket, sub: Subscription) -> None:
    try:
        async for event in sub:
            await ws.send_text(event.model_dump_json())
            if sub.dropped > _DEAD_SUBSCRIBER_DROPS:
                await ws.close(code=status.WS_1011_INTERNAL_ERROR)
                return
    except (WebSocketDisconnect, RuntimeError):
        return


async def _reader(ws: WebSocket, session: Session) -> None:
    try:
        while True:
            raw = await ws.receive_text()
            try:
                cmd = parse_command(raw)
            except ValidationError:
                await ws.send_text(
                    ErrorEvent(code="bad_command", message="unparseable").model_dump_json()
                )
                continue
            match cmd:
                case AnswerCommand():
                    session.request_answer()
                case ClearCommand():
                    session.clear()
                case ResetCommand():
                    session.reset()
                case PingCommand():
                    pass
    except WebSocketDisconnect:
        return


if _STATIC_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
