// TypeScript mirrors of the backend models and the WS protocol. Kept in sync by hand with
// backend/app/{system,config_store,events}.py so the frontend cannot silently drift.

export interface Connection {
  host: string;
  port: number;
  token: string;
}

export interface Health {
  status: string;
  configured: boolean; // user has completed setup (llm_model set)
  ready: boolean; // live pipeline running (LLM healthy)
  stt_provider: string;
  llm_provider: string;
  remote_viewer: string;
  stt_ready: boolean; // speech model loaded; false while a first run downloads it
  stt_progress: number; // download percentage, 0 until it starts
  llm_can_think: boolean; // model has a reasoning mode, so the toggle is worth showing
}

export interface GpuInfo {
  index: number; // Vulkan device index (== INTERVIEWR_WHISPER_GPU_DEVICE)
  name: string;
  vendor: string; // intel | nvidia | amd | other
  kind: string; // discrete | integrated | virtual | cpu | other
  vram_mb: number | null;
}

export interface ModelInfo {
  name: string;
  size: number;
  parameter_size: string | null;
  quantization: string | null;
  family: string | null;
}

export interface OllamaStatus {
  installed: boolean;
  running: boolean;
  version: string | null;
  models: ModelInfo[];
  install_command: string | null;
}

// A curated model and how well it fits the detected VRAM (mirrors backend RankedModel); its facts are null until catalog.json is refreshed.
export interface RankedModel {
  name: string; // ollama tag
  label: string;
  download_mb: number | null; // exact, summed from the registry manifest
  params: string | null; // "14.8B"
  quant: string | null; // "Q4_K_M"
  family: string | null;
  fit: string | null; // fits | tight | too_big | cpu; null when the size isn't known
  vram_mb: number | null; // estimated VRAM while running: weights plus cache and compute buffers
}

export interface Recommendation {
  can_run_local: boolean;
  tier: string; // large | medium | small | none
  vram_mb: number | null; // the card's total
  usable_vram_mb: number | null; // what's left for the chat model once speech and retrieval have theirs
  suggested_models: string[]; // default preselection (largest fitting first)
  catalog: RankedModel[]; // full curated catalog, ranked + fit-labelled for this machine
  discrete_gpu_index: number | null;
  reason: string;
}

// Capacity of the filesystem holding Ollama's model store (mirrors backend DiskInfo).
export interface DiskInfo {
  path: string; // the directory the figures were measured against
  total_mb: number;
  free_mb: number;
  models_mb: number | null; // size of Ollama's model store; null when it isn't readable
  partial_mb: number; // of that, bytes held by interrupted downloads
}

// One speech model, labelled for the detected hardware (mirrors backend WhisperOption).
export interface WhisperOption {
  name: string;
  download_mb: number;
  vram_mb: number; // what whisper.cpp needs on the GPU
  live_wer: number | null; // word error rate through the live pipeline; null when not measured
  speed: number | null; // fraction of real time to transcribe; lower is faster
  fit: string | null; // fits | tight | too_big | cpu
}

export interface WhisperChoice {
  suggested: string; // preselected for this machine
  models: WhisperOption[];
}

export interface SystemReport {
  gpus: GpuInfo[];
  cpu_count: number;
  ram_mb: number;
  gpu_error: string | null; // GPU present but driver unreachable (e.g. NVIDIA mismatch)
  disk: DiskInfo | null; // null when Ollama's model store can't be located (e.g. remote)
  ollama: OllamaStatus;
  recommendation: Recommendation;
  whisper: WhisperChoice;
}

export interface ConfigView {
  stt_provider: string;
  whisper_model: string;
  whisper_gpu_device: number;
  whisper_use_gpu: boolean;
  llm_provider: string;
  llm_model: string;
  ollama_base_url: string;
  llm_keep_alive: string;
  embed_provider: string;
  embed_model: string;
  rag_enabled: boolean;
  rag_top_k: number;
  history_enabled: boolean;
  audio_capture_app: string; // empty = capture everything
  // Prompts arrive resolved, so an untouched box shows the built-in default rather than blank.
  system_prompt: string;
  answer_style_script: string;
  answer_style_bullets: string;
  auth_token_set: boolean;
  anthropic_api_key_set: boolean;
  openai_api_key_set: boolean;
  gemini_api_key_set: boolean;
  groq_api_key_set: boolean;
}

// Conversation history (mirrors backend/app/history/store.py).
export interface TurnRecord {
  id: number;
  asked_at: string;
  question: string;
  answer: string;
}

export interface ConversationRecord {
  id: number;
  started_at: string;
  title: string | null;
  turn_count: number;
  preview: string; // first question; list-UI fallback label
}

export interface ConversationDetail {
  id: number;
  started_at: string;
  title: string | null;
  turns: TurnRecord[];
}

export interface PullProgress {
  status: string;
  completed: number | null;
  total: number | null;
  percent: number | null;
  error?: string | null;
}

// Server -> client WS events (discriminated on `type`).
export type ServerEvent =
  | { type: "transcript"; text: string; final: boolean; seq: number }
  | { type: "answer.start"; answer_id: string; question: string; source_chars: number }
  | { type: "answer.token"; answer_id: string; token: string }
  // The model's reasoning, shown while it works; the engine keeps it out of history.
  | { type: "answer.reasoning"; answer_id: string; token: string }
  | { type: "answer.done"; answer_id: string; cancelled: boolean }
  | {
      type: "status";
      state: "idle" | "listening" | "thinking" | "unconfigured";
      source?: string | null; // what capture is listening to, only sent when it starts
    }
  | { type: "error"; code: string; message: string }
  | { type: "conversation.titled"; conversation_id: number; title: string };

export type CommandType = "answer" | "clear" | "reset" | "ping" | "pause" | "resume";

export type AnswerStyle = "script" | "bullets";
