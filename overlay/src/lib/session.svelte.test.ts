// The capture-source label: set when capture starts, kept through the interview, cleared when it ends.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { session } from "./session.svelte";
import type { ServerEvent } from "./types";

/** A WebSocket stand-in that lets a test hand events to the store the way the engine would. */
class FakeSocket {
  static last: FakeSocket | null = null;
  static OPEN = 1; // the store checks readyState against this before sending
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 1;
  sent: string[] = [];

  constructor() {
    FakeSocket.last = this;
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {}
}

/** Start the store against the fake socket and wait for the async connect to attach handlers. */
async function connect(): Promise<FakeSocket> {
  vi.stubGlobal("WebSocket", FakeSocket);
  session.start();
  // wsUrl() resolves through a couple of promises; getConnection has no Tauri here and falls back.
  for (let i = 0; i < 10 && FakeSocket.last?.onmessage == null; i++) await Promise.resolve();
  const ws = FakeSocket.last;
  if (!ws?.onmessage) throw new Error("session never attached a message handler");
  ws.onopen?.(); // the engine accepting the connection is what re-asserts the sticky settings
  return ws;
}

function emit(ws: FakeSocket, ev: ServerEvent): void {
  ws.onmessage?.({ data: JSON.stringify(ev) } as MessageEvent<string>);
}

describe("captureSource", () => {
  beforeEach(() => {
    session.stop();
    session.captureSource = null;
    FakeSocket.last = null;
  });

  it("is set from the status the engine sends when capture starts", async () => {
    const ws = await connect();
    emit(ws, { type: "status", state: "listening", source: "Brave" });
    expect(session.captureSource).toBe("Brave");
  });

  it("survives later statuses that carry no source", async () => {
    const ws = await connect();
    emit(ws, { type: "status", state: "listening", source: "Brave" });
    // Later statuses don't repeat the source, so clearing on them would make the label flicker.
    emit(ws, { type: "status", state: "thinking" });
    expect(session.captureSource).toBe("Brave");
    emit(ws, { type: "status", state: "listening" });
    expect(session.captureSource).toBe("Brave");
  });

  it("is dropped when capture stops", async () => {
    const ws = await connect();
    emit(ws, { type: "status", state: "listening", source: "Brave" });
    emit(ws, { type: "status", state: "idle" });
    expect(session.captureSource).toBeNull();
  });

  it("is dropped when the connection goes", async () => {
    // "offline" comes from the client's own close handler rather than the engine, so test that path directly.
    const ws = await connect();
    emit(ws, { type: "status", state: "listening", source: "Brave" });
    ws.onclose?.(); // not stop() first: that clears via its own path and would hide a regression
    expect(session.status).toBe("offline");
    expect(session.captureSource).toBeNull();
    session.stop(); // cancel the reconnect the close scheduled
  });

  it("is cleared by leaving the interview, without waiting for the engine", async () => {
    // Don't wait for the engine's idle event, which can arrive after the next interview has started.
    const ws = await connect();
    emit(ws, { type: "status", state: "listening", source: "Brave" });
    session.resetView();
    expect(session.captureSource).toBeNull();
  });
});

describe("reasoning", () => {
  beforeEach(() => {
    session.stop();
    session.setReasoning(false);
    session.turns = [];
    FakeSocket.last = null;
  });

  it("streams onto the turn separately from the answer", async () => {
    const ws = await connect();
    emit(ws, { type: "answer.start", answer_id: "a1", question: "design it", source_chars: 9 });
    emit(ws, { type: "answer.reasoning", answer_id: "a1", token: "weighing the budget" });
    emit(ws, { type: "answer.token", answer_id: "a1", token: "Split it in three." });
    const turn = session.turns.at(-1);
    expect(turn?.reasoning).toBe("weighing the budget");
    expect(turn?.answer).toBe("Split it in three.");
  });

  it("times the wait so the collapsed summary can report it", async () => {
    const ws = await connect();
    emit(ws, { type: "answer.start", answer_id: "a2", question: "q", source_chars: 1 });
    emit(ws, { type: "answer.reasoning", answer_id: "a2", token: "hmm" });
    emit(ws, { type: "answer.token", answer_id: "a2", token: "Answer." });
    expect(session.turns.at(-1)?.thoughtMs).not.toBeNull();
  });

  it("restates the style with every answer, so a dropped toggle cannot desync the engine", async () => {
    // Reproduces the real fault: toggling while the socket is down updated the label and
    // localStorage but never reached the engine, which kept answering in the old style.
    const ws = await connect();
    session.setAnswerStyle("bullets");
    ws.readyState = 3; // socket gone; the toggle below cannot be delivered
    session.setAnswerStyle("script");
    expect(session.answerStyle).toBe("script");

    ws.readyState = 1;
    ws.sent.length = 0;
    session.requestAnswer();
    const frames = ws.sent.map((f) => JSON.parse(f));
    expect(frames).toContainEqual({ type: "answer_style", style: "script" });
    expect(frames.at(-1)).toEqual({ type: "answer" });
  });

  it("tells the engine when it changes, and re-asserts on reconnect", async () => {
    const ws = await connect();
    ws.sent.length = 0;
    session.setReasoning(true);
    expect(ws.sent.map((f) => JSON.parse(f))).toContainEqual({ type: "reasoning", on: true });

    // A reconnect gets a fresh session on the engine, so the sticky choice has to be re-sent.
    const next = await connect();
    expect(next.sent.map((f) => JSON.parse(f))).toContainEqual({ type: "reasoning", on: true });
    session.setReasoning(false);
  });
});
