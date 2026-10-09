"""FastAPI entrypoint: lifespan-managed engine + authed WebSocket broadcast."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import anyio
import httpx
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .config import LLMProvider, Settings, get_settings
from .config_store import ConfigView, apply_config, current_config
from .events import (
    AnswerCommand,
    AnswerStyleCommand,
    ClearCommand,
    ErrorEvent,
    EventBus,
    PauseCommand,
    PingCommand,
    ReasoningCommand,
    ResetCommand,
    ResumeCommand,
    StatusEvent,
    Subscription,
    parse_command,
)
from .history import (
    ConversationDetail,
    ConversationRecord,
    HistoryStore,
    format_conversation_md,
    format_history_md,
)
from .llm import build_llm
from .llm.base import LLMClient, effective_style_directives
from .pipeline import CapturePrefs, run_producer
from .rag import Retriever, build_retriever
from .rag.base import DocKind
from .rag.store import DocumentRecord
from .session import Session
from .audio import routing as audio_routing
from .audio.capture import get_default_sink
from .system import SystemReport, collect_system_report
from .system.disk import DiskInfo, missing_bytes, present_blobs, probe_disk
from .system.ollama_admin import OllamaAdmin, normalize_tag
from .system.registry import resolve_model

_log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_DEAD_SUBSCRIBER_DROPS = 500  # close a writer that has fallen this far behind


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    bus = EventBus(maxsize=settings.event_queue_maxsize)
    app.state.settings = settings
    app.state.bus = bus
    app.state.session = None
    app.state.retriever = None
    app.state.history = None
    app.state.llm = None  # the live LLM client, for cheap live-reload (e.g. keep-alive)
    app.state.configured = bool(settings.llm_model)
    app.state.ready = False
    app.state.stt_ready = False  # speech model loaded (a first run downloads it)
    app.state.stt_progress = 0
    app.state.llm_can_think = False  # whether the model has a reasoning mode worth offering
    app.state.capture_prefs = None

    # Remove any null sink left behind by a crashed engine.
    await audio_routing.cleanup(await _default_sink_or_none())

    # History is available even in setup mode so past conversations can still be browsed.
    history = HistoryStore(settings.resolved_history_db_path())
    app.state.history = history

    # Setup mode: without a model or a healthy LLM, only the configuration endpoints run.
    llm: LLMClient | None = None
    warmup_task: asyncio.Task[None] | None = None
    producer: asyncio.Task[None] | None = None

    # RAG only needs the embedder, so it starts even in setup mode.
    retriever: Retriever | None = await _start_retriever(settings)
    app.state.retriever = retriever

    if not app.state.configured:
        _log.warning("INTERVIEWR_LLM_MODEL not set; starting in setup mode (configure via wizard)")
    else:
        llm = build_llm(settings)
        if await llm.check_health():
            session = Session(
                bus,
                llm,
                max_buffer_chars=settings.max_buffer_chars,
                max_history_chars=settings.max_history_chars,
                retriever=retriever,
                history=history,
                history_enabled=settings.history_enabled,
                style_directives=effective_style_directives(settings),
            )
            app.state.session = session
            app.state.llm = llm
            app.state.ready = True
            app.state.llm_can_think = await _llm_can_think(settings)
            # Warm the LLM in the background so transcription doesn't wait for it to load.
            warmup_task = asyncio.create_task(_warm_llm(llm))
            capture_prefs = CapturePrefs.of(settings)
            app.state.capture_prefs = capture_prefs
            def stt_state(ready: bool, percent: int) -> None:
                app.state.stt_ready = ready
                app.state.stt_progress = percent

            producer = asyncio.create_task(
                run_producer(settings, session, bus, capture_prefs, stt_state)
            )
            producer.add_done_callback(_log_producer_exit)
        else:
            _log.warning(
                "LLM provider '%s' unhealthy; starting in setup mode", settings.llm_provider
            )
            await llm.close()
            llm = None

    try:
        yield
    finally:
        for task in (producer, warmup_task):
            if task is not None:
                task.cancel()
        for task in (producer, warmup_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        if app.state.session is not None:
            await app.state.session.aclose()  # may write a final title via history + llm
        if llm is not None:
            await llm.close()
        if retriever is not None:
            await retriever.close()
        # Return the user's audio to the default sink before shutting down.
        await audio_routing.cleanup(await _default_sink_or_none())
        history.close()


async def _default_sink_or_none() -> str | None:
    """The default sink, or None when PipeWire isn't available."""
    try:
        return await get_default_sink()
    except (RuntimeError, FileNotFoundError, OSError) as exc:  # noqa: BLE001
        _log.debug("no default sink: %s", exc)
        return None


def _log_producer_exit(task: asyncio.Task[None]) -> None:
    """Logs the transcript producer ending before shutdown, which would otherwise stop transcription silently."""
    if task.cancelled():
        return
    _log.error("transcript producer stopped; restart the engine", exc_info=task.exception())


async def _warm_llm(llm: LLMClient) -> None:
    """Best-effort LLM warmup; a failure here must not stop live transcription."""
    try:
        await llm.warmup()
    except Exception as exc:  # noqa: BLE001
        _log.warning("LLM warmup failed: %s", exc)


async def _start_retriever(settings: Settings) -> Retriever | None:
    """Builds the retriever and ingests configured docs; None if the embedder is unreachable."""
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

# Allow the overlay's local and Tauri origins; requests are still token-gated.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost|https?://tauri\.localhost)$"
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _llm_can_think(settings: Settings) -> bool:
    """Whether the configured model has a reasoning mode. Asked once: a model change restarts us."""
    if settings.llm_provider != LLMProvider.OLLAMA:
        return False  # the cloud adapters don't expose their reasoning APIs
    admin = OllamaAdmin(settings.ollama_base_url)
    try:
        return "thinking" in await admin.capabilities(settings.llm_model)
    finally:
        await admin.close()


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "configured": bool(settings.llm_model),  # has the user completed setup?
        "ready": bool(getattr(request.app.state, "ready", False)),  # is the live pipeline running?
        # Speech model state, so the UI can show a first run's download instead of looking stuck.
        "stt_ready": bool(getattr(request.app.state, "stt_ready", False)),
        "stt_progress": int(getattr(request.app.state, "stt_progress", 0)),
        # Drives the interview screen's reasoning toggle, which is hidden when the model can't.
        "llm_can_think": bool(getattr(request.app.state, "llm_can_think", False)),
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "remote_viewer": str(settings.enable_remote_viewer),
    }


def _token_ok(settings: Settings, token: str) -> bool:
    if not settings.auth_token:
        # No token configured: only allowed on a localhost bind.
        return settings.host == "127.0.0.1"
    return hmac.compare_digest(token, settings.auth_token)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    settings: Settings = ws.app.state.settings
    bus: EventBus = ws.app.state.bus
    session: Session | None = ws.app.state.session

    if not _token_ok(settings, ws.query_params.get("token", "")):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()

    sub = bus.subscribe()
    if session is not None:
        session.notify_subscribers_changed()  # a client is present; capture may resume
    # In setup mode there is no session yet; tell clients to show the wizard, not "listening".
    bus.publish(StatusEvent(state="listening" if session is not None else "unconfigured"))
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
        if session is not None:
            session.notify_subscribers_changed()  # last client gone -> capture pauses


async def _writer(ws: WebSocket, sub: Subscription) -> None:
    try:
        async for event in sub:
            await ws.send_text(event.model_dump_json())
            if sub.dropped > _DEAD_SUBSCRIBER_DROPS:
                await ws.close(code=status.WS_1011_INTERNAL_ERROR)
                return
    except (WebSocketDisconnect, RuntimeError):
        return


async def _reader(ws: WebSocket, session: Session | None) -> None:
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
            if session is None:
                await ws.send_text(
                    ErrorEvent(
                        code="needs_setup", message="engine not configured"
                    ).model_dump_json()
                )
                continue
            match cmd:
                case AnswerCommand():
                    session.request_answer()
                case ClearCommand():
                    session.clear()
                case ResetCommand():
                    session.reset()
                case PauseCommand():
                    session.pause_capture()
                case ResumeCommand():
                    session.resume_capture()
                case AnswerStyleCommand():
                    session.set_answer_style(cmd.style)
                case ReasoningCommand():
                    session.set_reasoning(cmd.on)
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
    """Ingests a resume, job description or notes from an uploaded file or a text field."""
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


class PullRequest(BaseModel):
    model: str


class DeleteRequest(BaseModel):
    model: str


class SettingsUpdate(BaseModel):
    updates: dict[str, object]


_PULL_HEADROOM_MB = 1024  # leave room for the daemon's temp files and the rest of the system


def _local_ollama(base_url: str) -> bool:
    """Whether Ollama runs on this machine, i.e. whether our disk is the one that fills."""
    host = urlparse(base_url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


async def _guard_pull(settings: Settings, model: str) -> None:
    """Rejects a pull for an unknown tag or one that won't fit on disk; allows it when unsure."""
    resolution, layers = await resolve_model(model)
    if resolution == "missing":
        # The registry says this tag doesn't exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no model called '{model}'"
        )
    if not _local_ollama(settings.ollama_base_url):
        return
    disk = await asyncio.to_thread(probe_disk, settings.resolved_ollama_models_dir())
    if disk is None or layers is None:
        return
    models_dir = Path(disk.path)
    present = await asyncio.to_thread(present_blobs, models_dir)
    needed_mb = missing_bytes(layers, present) // (1024 * 1024)
    if needed_mb + _PULL_HEADROOM_MB <= disk.free_mb:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"{model} needs {needed_mb / 1024:.1f} GB but only "
            f"{disk.free_mb / 1024:.1f} GB is free on {disk.path}"
        ),
    )


def _guard_model_in_use(settings: Settings, model: str) -> None:
    """Rejects deleting the model currently used for answers or retrieval."""
    tag = normalize_tag(model)
    if settings.llm_provider == "ollama" and normalize_tag(settings.llm_model) == tag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="that model is answering right now; switch to another one first",
        )
    if (
        settings.rag_enabled
        and settings.embed_provider == "ollama"
        and normalize_tag(settings.embed_model) == tag
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="that model powers retrieval; turn RAG off first",
        )


@app.get("/system/info")
async def system_info(request: Request) -> SystemReport:
    """Hardware capability + Ollama status for the setup wizard's GPU-check screen."""
    _require_auth(request)
    settings: Settings = request.app.state.settings
    return await collect_system_report(settings)


@app.get("/system/disk")
async def system_disk(request: Request) -> DiskInfo | None:
    """Disk figures only; much cheaper than /system/info, so the UI can re-check it on focus."""
    _require_auth(request)
    settings: Settings = request.app.state.settings
    return await asyncio.to_thread(probe_disk, settings.resolved_ollama_models_dir())


@app.get("/audio/apps")
async def audio_apps(request: Request) -> list[str]:
    """Apps the user can capture from: those playing now and those that have played before."""
    _require_auth(request)
    live = {a.name for a in await audio_routing.list_apps()}
    return sorted(live | set(audio_routing.known_apps()))


@app.post("/ollama/pull")
async def ollama_pull(request: Request, body: PullRequest) -> StreamingResponse:
    """Pull an Ollama model, streaming progress as newline-delimited JSON (PullProgress)."""
    _require_auth(request)
    settings: Settings = request.app.state.settings
    await _guard_pull(settings, body.model)

    async def stream() -> AsyncIterator[str]:
        admin = OllamaAdmin(settings.ollama_base_url)
        try:
            # aclosing makes sure the Ollama stream is closed if the client disconnects.
            async with aclosing(admin.pull_model(body.model)) as progress_stream:
                async for progress in progress_stream:
                    yield progress.model_dump_json() + "\n"
        finally:
            # Shielded so closing isn't itself cancelled on the disconnect path.
            with anyio.CancelScope(shield=True):
                await admin.close()

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/ollama/delete")
async def ollama_delete(request: Request, body: DeleteRequest) -> dict[str, bool]:
    """Remove a pulled Ollama model. POST, not DELETE: tags carry ':' and can carry '/'."""
    _require_auth(request)
    settings: Settings = request.app.state.settings
    _guard_model_in_use(settings, body.model)

    admin = OllamaAdmin(settings.ollama_base_url)
    try:
        deleted = await admin.delete_model(body.model)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"ollama unreachable: {exc}"
        ) from exc
    finally:
        await admin.close()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such model")
    return {"deleted": True}


@app.get("/settings")
async def read_settings(request: Request) -> ConfigView:
    """Current config for the settings UI; secret values are redacted to set/unset flags."""
    _require_auth(request)
    return current_config(request.app.state.settings)


@app.post("/settings")
async def write_settings(request: Request, body: SettingsUpdate) -> dict[str, bool]:
    """Validates and saves config to .env; the engine must restart to apply it."""
    _require_auth(request)
    settings: Settings = request.app.state.settings
    try:
        apply_config(settings, body.updates)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"restart_required": True}


@app.post("/settings/reload")
async def reload_settings(request: Request) -> dict[str, bool]:
    """Applies saved lightweight settings to the running engine without a restart."""
    _require_auth(request)
    get_settings.cache_clear()
    fresh = get_settings()
    request.app.state.settings = fresh

    retriever: Retriever | None = request.app.state.retriever
    if retriever is not None:
        retriever.set_top_k(fresh.rag_top_k)
    session: Session | None = request.app.state.session
    if session is not None:
        session.set_history_enabled(fresh.history_enabled)
        session.set_style_directives(effective_style_directives(fresh))
    prefs: CapturePrefs | None = request.app.state.capture_prefs
    if prefs is not None:
        # Takes effect at the next capture start.
        prefs.update(fresh)
    set_keep_alive = getattr(request.app.state.llm, "set_keep_alive", None)
    if callable(set_keep_alive):
        set_keep_alive(fresh.llm_keep_alive)
    return {"reloaded": True}


def _get_history(request: Request) -> HistoryStore:
    history: HistoryStore | None = request.app.state.history
    if history is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="history store unavailable"
        )
    return history


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@app.get("/history")
async def list_history(request: Request) -> list[ConversationRecord]:
    """Conversations newest first, hiding any still being finalized."""
    _require_auth(request)
    convs = _get_history(request).list_conversations()
    session: Session | None = getattr(request.app.state, "session", None)
    hidden = session.pending_conversation_ids if session is not None else set()
    if hidden:
        convs = [c for c in convs if c["id"] not in hidden]
    return convs


# Declared before /history/{conversation_id} so "export" is not parsed as a conversation id.
@app.get("/history/export")
async def export_history(request: Request, fmt: str = Query("md", alias="format")) -> Response:
    """Download all conversations: Markdown by default, JSON with ?format=json."""
    _require_auth(request)
    details = _get_history(request).export()
    if fmt == "json":
        return JSONResponse(content=details, headers=_attachment("interviewr-history.json"))
    return Response(
        content=format_history_md(details),
        media_type="text/markdown",
        headers=_attachment("interviewr-history.md"),
    )


@app.get("/history/{conversation_id}")
async def get_history(request: Request, conversation_id: int) -> ConversationDetail:
    _require_auth(request)
    detail = _get_history(request).get_conversation(conversation_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such conversation")
    return detail


@app.get("/history/{conversation_id}/export")
async def export_conversation(
    request: Request, conversation_id: int, fmt: str = Query("md", alias="format")
) -> Response:
    """Download one conversation as a Markdown 'script' (or JSON with ?format=json)."""
    _require_auth(request)
    detail = _get_history(request).get_conversation(conversation_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such conversation")
    if fmt == "json":
        return JSONResponse(
            content=detail, headers=_attachment(f"conversation-{conversation_id}.json")
        )
    return Response(
        content=format_conversation_md(detail),
        media_type="text/markdown",
        headers=_attachment(f"conversation-{conversation_id}.md"),
    )


@app.delete("/history/{conversation_id}")
async def delete_history(request: Request, conversation_id: int) -> dict[str, bool]:
    _require_auth(request)
    if not _get_history(request).delete_conversation(conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such conversation")
    return {"deleted": True}


@app.delete("/history")
async def clear_history(request: Request) -> dict[str, bool]:
    _require_auth(request)
    _get_history(request).clear()
    return {"cleared": True}


if _STATIC_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
