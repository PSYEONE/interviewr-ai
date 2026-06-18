// WS client as a Svelte 5 runes store. Ports the connect/handle/retry loop from the old
// vanilla app.js: committed transcript + live tail, streamed answer, status, capped-exponential
// reconnect, and a 20s keepalive ping.

import { wsUrl } from "./connection";
import type { CommandType, ServerEvent } from "./types";

export type Status =
  | "offline"
  | "connecting"
  | "listening"
  | "thinking"
  | "idle"
  | "unconfigured";

class Session {
  status = $state<Status>("offline");
  committed = $state("");
  live = $state("");
  answer = $state("");

  #ws: WebSocket | null = null;
  #backoff = 1000;
  #curAnswer: string | null = null;
  #ping: ReturnType<typeof setInterval> | null = null;

  start(): void {
    void this.#connect();
    this.#ping = setInterval(() => this.send("ping"), 20000);
  }

  stop(): void {
    if (this.#ping !== null) clearInterval(this.#ping);
    try {
      this.#ws?.close();
    } catch {
      /* already closing */
    }
  }

  send(type: CommandType): void {
    if (this.#ws && this.#ws.readyState === WebSocket.OPEN) {
      this.#ws.send(JSON.stringify({ type }));
    }
  }

  resetView(): void {
    this.send("reset");
    this.committed = "";
    this.live = "";
    this.answer = "";
  }

  async #connect(): Promise<void> {
    const url = await wsUrl();
    this.status = "connecting";
    const ws = new WebSocket(url);
    this.#ws = ws;
    ws.onopen = () => {
      this.#backoff = 1000;
      this.status = "listening";
    };
    ws.onmessage = (e: MessageEvent<string>) => this.#handle(JSON.parse(e.data) as ServerEvent);
    ws.onclose = () => {
      this.status = "offline";
      this.#retry();
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
    setTimeout(() => void this.#connect(), this.#backoff);
    this.#backoff = Math.min(this.#backoff * 1.6, 8000);
  }

  #handle(ev: ServerEvent): void {
    switch (ev.type) {
      case "transcript":
        if (ev.final) {
          if (ev.text) this.committed += ev.text + " ";
          this.live = "";
        } else {
          this.live = ev.text;
        }
        break;
      case "answer.start":
        this.#curAnswer = ev.answer_id;
        this.answer = "";
        break;
      case "answer.token":
        if (ev.answer_id === this.#curAnswer) this.answer += ev.token;
        break;
      case "answer.done":
        if (ev.cancelled) this.answer += " [cancelled]";
        break;
      case "status":
        this.status = ev.state;
        break;
      case "error":
        this.answer += `\n[error: ${ev.message}]`;
        break;
    }
  }
}

export const session = new Session();
