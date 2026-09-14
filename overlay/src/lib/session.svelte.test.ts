// The capture-source label: set when capture starts, kept through the interview, cleared when it ends.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { session } from "./session.svelte";
import type { ServerEvent } from "./types";

/** A WebSocket stand-in that lets a test hand events to the store the way the engine would. */
class FakeSocket {
  static last: FakeSocket | null = null;
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
