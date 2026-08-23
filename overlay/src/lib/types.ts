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

// One curated catalog model, fit-labelled against the detected VRAM (mirrors backend RankedModel).
export interface RankedModel {
  name: string; // ollama tag
  label: string;
  params: string; // "14B"
  min_vram_mb: number;
  family: string;
  blurb: string;
  fit: string; // fits | tight | too_big | cpu
}

export interface Recommendation {
  can_run_local: boolean;
  tier: string; // large | medium | small | none
  vram_mb: number | null;
  suggested_models: string[]; // default preselection (largest fitting first)
  catalog: RankedModel[]; // full curated catalog, ranked + fit-labelled for this machine
  discrete_gpu_index: number | null;
  reason: string;
}

export interface SystemReport {
  gpus: GpuInfo[];
  cpu_count: number;
  ram_mb: number;
  gpu_error: string | null; // GPU present but driver unreachable (e.g. NVIDIA mismatch)
  ollama: OllamaStatus;
  recommendation: Recommendation;
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
  system_prompt: string; // resolved: override if set, else the built-in default
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
}

// Server -> client WS events (discriminated on `type`).
export type ServerEvent =
  | { type: "transcript"; text: string; final: boolean; seq: number }
  | { type: "answer.start"; answer_id: string; source_chars: number }
  | { type: "answer.token"; answer_id: string; token: string }
  | { type: "answer.done"; answer_id: string; cancelled: boolean }
  | { type: "status"; state: "idle" | "listening" | "thinking" | "unconfigured" }
  | { type: "error"; code: string; message: string }
  | { type: "conversation.titled"; conversation_id: number; title: string };

export type CommandType = "answer" | "clear" | "reset" | "ping" | "pause" | "resume";

export type AnswerStyle = "script" | "bullets";
