// Typed HTTP client for the backend capability/settings endpoints. These run over fetch() and
// are therefore CORS-gated (the backend allows local + Tauri origins); the WS is separate.

import { authHeaders, httpBase } from "./connection";
import { saveText } from "./tauri";
import type {
  ConfigView,
  ConversationDetail,
  ConversationRecord,
  Health,
  PullProgress,
  SystemReport,
} from "./types";

async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(`${await httpBase()}${path}`, { headers: await authHeaders() });
  if (!resp.ok) throw new Error(`GET ${path} -> ${resp.status}`);
  return (await resp.json()) as T;
}

export const getHealth = (): Promise<Health> => getJSON<Health>("/health");
export const getSettings = (): Promise<ConfigView> => getJSON<ConfigView>("/settings");

// Poll /health until `pred` holds or `timeoutMs` elapses. Used after an engine restart to wait
// for the fresh process to come up and report the new config. Returns the matching Health, or
// throws on timeout. Unreachable health (engine still restarting) counts as "not yet".
export async function waitForHealth(
  pred: (h: Health) => boolean,
  timeoutMs = 30000,
  intervalMs = 500,
): Promise<Health> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const h = await getHealth();
      if (pred(h)) return h;
    } catch {
      /* engine not back yet */
    }
    if (Date.now() >= deadline) throw new Error("timed out waiting for the engine");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// Detecting GPUs shells out to vulkaninfo/nvidia-smi/Ollama, so cache the result module-side:
// it survives the wizard step components unmounting on back-navigation. `force` re-probes (used
// by the refresh button and after a model pull). The cache is refreshed on every real fetch.
let _systemReport: SystemReport | null = null;
export async function getSystemInfo(force = false): Promise<SystemReport> {
  if (!force && _systemReport) return _systemReport;
  _systemReport = await getJSON<SystemReport>("/system/info");
  return _systemReport;
}

export interface DocumentRecord {
  id: number;
  name: string;
  kind: string;
  added_at: string;
  chunk_count: number;
}

export const listDocuments = (): Promise<DocumentRecord[]> =>
  getJSON<DocumentRecord[]>("/documents");

export async function deleteDocument(id: number): Promise<void> {
  const resp = await fetch(`${await httpBase()}/documents/${id}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!resp.ok) throw new Error(`DELETE /documents/${id} -> ${resp.status}`);
}

// Upload a resume / job-description (or pasted text) for RAG grounding. Multipart, so the
// browser sets the content-type + boundary itself. Returns 503 when RAG isn't available yet.
export async function uploadDocument(kind: string, file: File): Promise<DocumentRecord> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  const resp = await fetch(`${await httpBase()}/documents`, {
    method: "POST",
    headers: await authHeaders(),
    body: form,
  });
  if (!resp.ok) throw new Error((await resp.text()) || `upload -> ${resp.status}`);
  return (await resp.json()) as DocumentRecord;
}

// Conversation history.
export const listHistory = (): Promise<ConversationRecord[]> =>
  getJSON<ConversationRecord[]>("/history");
export const getConversation = (id: number): Promise<ConversationDetail> =>
  getJSON<ConversationDetail>(`/history/${id}`);

export async function deleteConversation(id: number): Promise<void> {
  const resp = await fetch(`${await httpBase()}/history/${id}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!resp.ok) throw new Error(`DELETE /history/${id} -> ${resp.status}`);
}

export async function clearHistory(): Promise<void> {
  const resp = await fetch(`${await httpBase()}/history`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!resp.ok) throw new Error(`DELETE /history -> ${resp.status}`);
}

// Fetch an export endpoint (auth header attached) and save it through the native Save dialog.
// Resolves false if the user cancels. Uses the Rust save_text command, not a webview download,
// which is unreliable on WebKitGTK.
export async function downloadExport(path: string, filename: string): Promise<boolean> {
  const resp = await fetch(`${await httpBase()}${path}`, { headers: await authHeaders() });
  if (!resp.ok) throw new Error(`GET ${path} -> ${resp.status}`);
  return saveText(filename, await resp.text());
}

export async function postSettings(updates: Record<string, unknown>): Promise<void> {
  const resp = await fetch(`${await httpBase()}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ updates }),
  });
  if (!resp.ok) throw new Error((await resp.text()) || `POST /settings -> ${resp.status}`);
}

// Apply already-persisted cheap settings to the running engine without a restart.
export async function reloadSettings(): Promise<void> {
  const resp = await fetch(`${await httpBase()}/settings/reload`, {
    method: "POST",
    headers: await authHeaders(),
  });
  if (!resp.ok) throw new Error((await resp.text()) || `POST /settings/reload -> ${resp.status}`);
}

// Remove a pulled Ollama model. POST rather than DELETE because tags carry ':' and can carry '/'.
// The backend refuses (409) models the engine is wired to, so surface its detail verbatim.
// Callers should follow with getSystemInfo(true) to refresh the cached report.
export async function deleteOllamaModel(model: string): Promise<void> {
  const resp = await fetch(`${await httpBase()}/ollama/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ model }),
  });
  if (resp.ok) return;
  // The 409 body is user-facing guidance, so unwrap FastAPI's {"detail": ...} envelope.
  const body = await resp.text();
  let message = body;
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string") message = detail;
  } catch {
    /* not JSON; use the raw body */
  }
  throw new Error(message || `delete ${model} -> ${resp.status}`);
}

// Pull an Ollama model, surfacing the backend's NDJSON progress stream frame by frame.
export async function pullModel(
  model: string,
  onProgress: (p: PullProgress) => void,
): Promise<void> {
  const resp = await fetch(`${await httpBase()}/ollama/pull`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ model }),
  });
  if (!resp.ok || !resp.body) throw new Error(`pull ${model} -> ${resp.status}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) onProgress(JSON.parse(line) as PullProgress);
    }
  }
  if (buffer.trim()) onProgress(JSON.parse(buffer) as PullProgress);
}
