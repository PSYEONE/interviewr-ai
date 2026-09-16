// WebSocket client store: connects to the engine (with reconnect and keepalive) and keeps the interview's questions and answers.

import { wsUrl } from "./connection";
import type { AnswerStyle, CommandType, ServerEvent } from "./types";

const _STYLE_KEY = "interviewr.answerStyle";

function _loadStyle(): AnswerStyle {
  return localStorage.getItem(_STYLE_KEY) === "bullets" ? "bullets" : "script";
}

// One question and its answer; the question text comes from the engine's answer.start event.
export type Turn = { id: string; question: string; answer: string; done: boolean };

export type Status =
  | "offline"
  | "connecting"
  | "listening"
  | "thinking"
  | "idle"
  | "unconfigured";

class Session {
  status = $state<Status>("offline");
  // The audio source the engine is capturing from, shown in the top bar.
  captureSource = $state<string | null>(null);
  // Answered turns, oldest first: the scrollback the interview view renders.
  turns = $state<Turn[]>([]);
  // Transcript heard since the last answer that hasn't been asked yet.
  pending = $state("");
  live = $state(""); // in-flight partial tail, replaced rather than appended
  // Answers completed since the interview started; unlike `turns`, the New button does not clear it.
  answeredThisInterview = $state(0);
  // Latest conversation title sent by the engine; the dashboard uses it to update the history list.
  lastTitled = $state<{ id: number; title: string } | null>(null);
  // Sticky answer formatting; persisted locally and re-sent to the engine on each connect.
  answerStyle = $state<AnswerStyle>(_loadStyle());

  #ws: WebSocket | null = null;
  #backoff = 1000;
  #curAnswer: string | null = null;
  #ping: ReturnType<typeof setInterval> | null = null;
  #retryTimer: ReturnType<typeof setTimeout> | null = null;
  #stopped = true; // true while not in a live session, so onclose does not auto-reconnect

  start(): void {
    if (!this.#stopped) return; // already running; don't stack a second socket
    this.#stopped = false;
    void this.#connect();
    this.#ping = setInterval(() => this.send("ping"), 20000);
  }

  stop(): void {
    this.#stopped = true; // suppress the onclose reconnect below
    if (this.#ping !== null) clearInterval(this.#ping);
    if (this.#retryTimer !== null) clearTimeout(this.#retryTimer);
    this.#retryTimer = null;
    try {
      this.#ws?.close();
    } catch {
      /* already closing */
    }
    this.#ws = null;
    this.#setStatus("offline");
  }

  send(type: CommandType): void {
    if (this.#ws && this.#ws.readyState === WebSocket.OPEN) {
      this.#ws.send(JSON.stringify({ type }));
    }
  }

  /** Sets the status, and clears the capture source once the engine is no longer listening or thinking. */
  #setStatus(state: Status, source?: string | null): void {
    this.status = state;
    if (source) this.captureSource = source;
    else if (state !== "listening" && state !== "thinking") this.captureSource = null;
  }

  // Called when an interview view opens.
  beginInterview(): void {
    this.answeredThisInterview = 0;
  }

  resetView(): void {
    this.send("reset");
    this.turns = [];
    this.pending = "";
    this.live = "";
    // Clear the source now instead of waiting for the engine, so the next interview doesn't show the old one.
    this.captureSource = null;
  }

  // Clears our copy of the pending transcript too, so the screen matches what the engine has.
  clearPending(): void {
    this.send("clear");
    this.pending = "";
    this.live = "";
  }

  setAnswerStyle(style: AnswerStyle): void {
    this.answerStyle = style;
    localStorage.setItem(_STYLE_KEY, style);
    if (this.#ws && this.#ws.readyState === WebSocket.OPEN) {
      this.#ws.send(JSON.stringify({ type: "answer_style", style }));
    }
  }

  toggleAnswerStyle(): void {
    this.setAnswerStyle(this.answerStyle === "script" ? "bullets" : "script");
  }

  async #connect(): Promise<void> {
    const url = await wsUrl();
    this.#setStatus("connecting");
    const ws = new WebSocket(url);
    this.#ws = ws;
    ws.onopen = () => {
      this.#backoff = 1000;
      this.#setStatus("listening");
      this.setAnswerStyle(this.answerStyle); // re-assert the sticky style on the shared session
    };
    ws.onmessage = (e: MessageEvent<string>) => this.#handle(JSON.parse(e.data) as ServerEvent);
    ws.onclose = () => {
      this.#setStatus("offline");
      if (!this.#stopped) this.#retry();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }

  #retry(): void {
    this.#retryTimer = setTimeout(() => void this.#connect(), this.#backoff);
    this.#backoff = Math.min(this.#backoff * 1.6, 8000);
  }

  // Turns are replaced rather than mutated in place so the $state proxy sees the change.
  #patch(id: string, fn: (t: Turn) => Turn): void {
    this.turns = this.turns.map((t) => (t.id === id ? fn(t) : t));
  }

  #appendAnswer(id: string, token: string): void {
    this.#patch(id, (t) => ({ ...t, answer: t.answer + token }));
  }

  #finishAnswer(id: string, cancelled: boolean): void {
    this.#patch(id, (t) => ({
      ...t,
      answer: cancelled ? t.answer + " [cancelled]" : t.answer,
      done: true,
    }));
  }

  // Attaches an error to the answer in progress, or adds it as its own entry if there isn't one.
  #noteError(message: string): void {
    const last = this.turns.at(-1);
    if (last && !last.done) {
      this.#patch(last.id, (t) => ({ ...t, answer: `${t.answer}\n[error: ${message}]` }));
      return;
    }
    this.turns = [
      ...this.turns,
      { id: `err-${Date.now()}`, question: "", answer: `[error: ${message}]`, done: true },
    ];
  }

  #handle(ev: ServerEvent): void {
    switch (ev.type) {
      case "transcript":
        if (ev.final) {
          if (ev.text) this.pending += ev.text + " ";
          this.live = "";
        } else {
          this.live = ev.text;
        }
        break;
      case "answer.start":
        this.#curAnswer = ev.answer_id;
        // The engine used the pending transcript for this question, so clear ours too.
        this.pending = "";
        this.turns = [
          ...this.turns,
          { id: ev.answer_id, question: ev.question, answer: "", done: false },
        ];
        break;
      case "answer.token":
        if (ev.answer_id === this.#curAnswer) this.#appendAnswer(ev.answer_id, ev.token);
        break;
      case "answer.done":
        this.#finishAnswer(ev.answer_id, ev.cancelled);
        if (!ev.cancelled) this.answeredThisInterview += 1;
        break;
      case "status":
        this.#setStatus(ev.state, ev.source);
        break;
      case "error":
        this.#noteError(ev.message);
        break;
      case "conversation.titled":
        this.lastTitled = { id: ev.conversation_id, title: ev.title };
        break;
    }
  }
}

export const session = new Session();
