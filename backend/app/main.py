"""FastAPI entrypoint: lifespan-managed engine + authed WebSocket broadcast."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
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
from .rag import Retriever, build_retriever
from .rag.base import DocKind
from .rag.store import DocumentRecord
from .session import Session

_log = logging.getLogger(__name__)

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

    retriever = await _start_retriever(settings)

    bus = EventBus(maxsize=settings.event_queue_maxsize)
    session = Session(
        bus,
        llm,
        max_buffer_chars=settings.max_buffer_chars,
        max_history_chars=settings.max_history_chars,
        retriever=retriever,
    )
    app.state.settings = settings
    app.state.bus = bus
    app.state.session = session
    app.state.retriever = retriever

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
        if retriever is not None:
            await retriever.close()


async def _warm_llm(llm: LLMClient) -> None:
    """Best-effort LLM warmup; a failure here must not stop live transcription."""
    try:
        await llm.warmup()
    except Exception as exc:  # noqa: BLE001
        _log.warning("LLM warmup failed: %s", exc)


async def _start_retriever(settings: Settings) -> Retriever | None:
    """Build the retriever and bootstrap configured docs. RAG is an enhancement: if the
    embedder is unreachable, log and run without it rather than blocking transcription."""
    retriever = build_retriever(settings)
    if retriever is None:
        return None
    if not await retriever.check_health():
        _log.warning(
            "embedder '%s' health check failed; RAG disabled (transcription unaffected)",
            settings.embed_provider,
        )
        await retriever.close()
        return None
    await _bootstrap_documents(settings, retriever)
    return retriever


async def _bootstrap_documents(settings: Settings, retriever: Retriever) -> None:
    """Ingest resume/job-desc files named in config, unless that kind is already stored."""
    present: set[str] = {doc["kind"] for doc in retriever.list_documents()}
    targets: list[tuple[str, DocKind]] = [
        (settings.resume_path, "resume"),
        (settings.job_desc_path, "job_description"),
    ]
    for path_str, kind in targets:
        if not path_str or kind in present:
            continue
        path = Path(path_str)
        try:
            record = await retriever.ingest_file(path.name, kind, path.read_bytes())
            _log.info("bootstrapped %s (%d chunks) from %s", kind, record["chunk_count"], path)
        except Exception as exc:  # noqa: BLE001
            _log.warning("failed to bootstrap %s from %s: %s", kind, path_str, exc)


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


def _require_auth(request: Request) -> None:
    """Same token policy as the WS path, for HTTP: ?token= or 'Authorization: Bearer'."""
    settings: Settings = request.app.state.settings
    token = request.query_params.get("token", "")
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:]
    if not _token_ok(settings, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad or missing token")


def _get_retriever(request: Request) -> Retriever:
    retriever: Retriever | None = request.app.state.retriever
    if retriever is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG is disabled or the embedder is unavailable",
        )
    return retriever


@app.post("/documents")
async def add_document(
    request: Request,
    kind: DocKind = Form("other"),
    name: str | None = Form(None),
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
) -> DocumentRecord:
    """Ingest a resume/JD/notes doc from an uploaded file OR a raw-text field.

    The overlay's drag-drop (Phase 3) becomes a multipart POST here; pasted notes use `text`.
    """
    _require_auth(request)
    retriever = _get_retriever(request)
    try:
        if file is not None:
            doc_name = name or file.filename or "upload"
            return await retriever.ingest_file(doc_name, kind, await file.read())
        if text is not None and text.strip():
            return await retriever.ingest_text(name or "notes", kind, text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="provide a 'file' upload or a 'text' field"
    )


@app.get("/documents")
async def list_documents(request: Request) -> list[DocumentRecord]:
    _require_auth(request)
    return _get_retriever(request).list_documents()


@app.delete("/documents/{doc_id}")
async def delete_document(request: Request, doc_id: int) -> dict[str, bool]:
    _require_auth(request)
    if not _get_retriever(request).delete_document(doc_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such document")
    return {"deleted": True}


if _STATIC_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
