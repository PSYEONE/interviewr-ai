// WS client as a Svelte 5 runes store: the connect/handle/retry loop, plus the interview's turn
// log. Transcript arrives as a pending buffer with a live tail; each answer.start consumes that
// buffer into a Turn, so past questions and answers survive instead of being overwritten.
// Capped-exponential reconnect and a 20s keepalive ping.

import { wsUrl } from "./connection";
import type { AnswerStyle, CommandType, ServerEvent } from "./types";

const _STYLE_KEY = "interviewr.answerStyle";

function _loadStyle(): AnswerStyle {
  return localStorage.getItem(_STYLE_KEY) === "bullets" ? "bullets" : "script";
}

// One question and the answer generated for it. The question comes from the engine on
// answer.start, so the pairing is authoritative rather than re-derived from the transcript.
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
  // The audio source the engine resolved at capture start, so the bar can show what is actually
  // being listened to rather than leaving a stale capture target indistinguishable from a live one.
  captureSource = $state<string | null>(null);
  // Answered turns, oldest first: the scrollback the interview view renders.
  turns = $state<Turn[]>([]);
  // Final transcript heard since the last answer, i.e. the not-yet-asked buffer. Mirrors the
  // engine's own _parts, and is consumed into a Turn when an answer starts.
  pending = $state("");
  live = $state(""); // in-flight partial tail, replaced rather than appended
  // Last conversation whose background-generated title landed; the dashboard watches this to
  // patch the title into the history list in place (titles arrive after the session ends).
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

  /** Status, plus the capture label that has to move with it.
   *
   *  The engine names the source only when capture starts, so it is held for the rest of the
   *  interview and dropped as soon as the state is no longer a capturing one. Every status change
   *  goes through here, including a dropped connection, which otherwise left the bar claiming to
   *  listen to an application while offline. */
  #setStatus(state: Status, source?: string | null): void {
    this.status = state;
    if (source) this.captureSource = source;
    else if (state !== "listening" && state !== "thinking") this.captureSource = null;
  }

  resetView(): void {
    this.send("reset");
    this.turns = [];
    this.pending = "";
    this.live = "";
    // Leaving the interview stops capture. Don't wait for the engine's idle event to say so: if
    // it arrives after the view is gone, the next interview opens on the old source.
    this.captureSource = null;
  }

  // The engine drops its buffer on "clear"; drop ours too, or the screen keeps showing text the
  // engine has already forgotten and the next answer silently excludes it.
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

  // Errors attach to the turn in flight; with none (e.g. a failure before any answer) they get
  // their own entry so the message is never swallowed.
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
        // The engine cleared its buffer to build this question, so ours goes with it. Anything
        // spoken from here on arrives after this event and belongs to the next turn.
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
