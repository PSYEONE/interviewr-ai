// Typed HTTP client for the engine's REST endpoints (the WebSocket connection lives in session.svelte.ts).

import { authHeaders, httpBase } from "./connection";
import { saveText } from "./tauri";
import type {
  ConfigView,
  DiskInfo,
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

// Returns the backend's error message from FastAPI's {"detail": ...} body, or the raw response text.
async function failure(resp: Response, what: string): Promise<string> {
  const body = await resp.text();
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  } catch {
    /* not JSON; fall through to the raw body */
  }
  return body || `${what} -> ${resp.status}`;
}

export const getHealth = (): Promise<Health> => getJSON<Health>("/health");
export const getSettings = (): Promise<ConfigView> => getJSON<ConfigView>("/settings");

// Apps that are playing now or have played before, so a silent browser can still be picked.
export const getAudioApps = (): Promise<string[]> => getJSON<string[]>("/audio/apps");

// Polls /health until `pred` is true or it times out; used to wait for the engine after a restart.
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

/** What the caller is in the middle of choosing, since the two model lists are sized against
 *  each other's VRAM. Omit them to be labelled against the saved settings. */
export interface FitOverrides {
  whisperModel?: string;
  llmModel?: string;
  assumeRecommendedLlm?: boolean; // size speech against the chat model this report recommends
}

function fitQuery(opts: FitOverrides): string {
  const q = new URLSearchParams();
  if (opts.whisperModel) q.set("whisper_model", opts.whisperModel);
  if (opts.llmModel) q.set("llm_model", opts.llmModel);
  if (opts.assumeRecommendedLlm) q.set("assume_recommended_llm", "true");
  return q.size ? `?${q}` : "";
}

// Cached because the hardware probe is slow; `force` runs it again.
let _systemReport: SystemReport | null = null;
export async function getSystemInfo(
  force = false,
  opts: FitOverrides = {},
): Promise<SystemReport> {
  const query = fitQuery(opts);
  // An overridden report describes a configuration that isn't saved, so it neither reads the cache
  // nor writes to it. Sharing one key with the real labels is how they came to disagree.
  if (query) return await getJSON<SystemReport>(`/system/info${query}`);
  if (!force && _systemReport) return _systemReport;
  _systemReport = await getJSON<SystemReport>("/system/info");
  return _systemReport;
}

// Disk figures only, much cheaper than getSystemInfo(); used to re-check when the window regains focus.
export async function getDisk(): Promise<DiskInfo | null> {
  const disk = await getJSON<DiskInfo | null>("/system/disk");
  // Update the cached report too, so it doesn't bring back the old figures later.
  if (_systemReport) _systemReport = { ..._systemReport, disk };
  return disk;
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

// Uploads a resume, job description or notes file for RAG; the engine returns 503 if RAG isn't available.
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

// Downloads an export and saves it through the native Save dialog; resolves false if the user cancels.
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

// Removes a downloaded Ollama model (POST, since tags can contain ':' and '/'); errors carry the engine's reason.
export async function deleteOllamaModel(model: string): Promise<void> {
  const resp = await fetch(`${await httpBase()}/ollama/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ model }),
  });
  if (!resp.ok) throw new Error(await failure(resp, `delete ${model}`));
}

// Pull an Ollama model, surfacing the backend's NDJSON progress stream frame by frame.
export async function pullModel(
  model: string,
  onProgress: (p: PullProgress) => void,
  signal?: AbortSignal,
  onStart?: () => void,
): Promise<void> {
  // The abort signal also cancels the engine's checks that run before the download starts.
  const resp = await fetch(`${await httpBase()}/ollama/pull`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ model }),
    signal,
  });
  if (!resp.ok) {
    // Keep the status so callers can tell "model not found" (404) apart from other failures.
    const err = new Error(await failure(resp, `pull ${model}`)) as Error & { status?: number };
    err.status = resp.status;
    throw err;
  }
  if (!resp.body) throw new Error(`pull ${model} -> no response body`);
  // Called once the download has really started, so a rejected pull never shows a progress bar.
  onStart?.();

  const report = (progress: PullProgress): void => {
    const failed = pullFailure(model, progress);
    if (failed) throw failed;
    onProgress(progress);
  };

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
      if (line.trim()) report(JSON.parse(line) as PullProgress);
    }
  }
  if (buffer.trim()) report(JSON.parse(buffer) as PullProgress);
}

// Ollama reports a refused pull mid-stream on a 200 response, so that line has to throw.
function pullFailure(model: string, progress: PullProgress): Error | null {
  if (!progress.error) return null;
  return new Error(`pull ${model} failed: ${progress.error}`);
}
