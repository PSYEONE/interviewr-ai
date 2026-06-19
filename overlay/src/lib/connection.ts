// Resolve the backend host/port/token once (from the project .env via the Rust get_connection
// command) and derive the HTTP base, WS URL, and auth header. Same source the WS already used.

import { getConnection } from "./tauri";
import type { Connection } from "./types";

let cached: Connection | null = null;

async function conn(): Promise<Connection> {
  if (cached) return cached;
  try {
    cached = await getConnection();
  } catch {
    cached = { host: "127.0.0.1", port: 8765, token: "" };
  }
  return cached;
}

export async function httpBase(): Promise<string> {
  const c = await conn();
  return `http://${c.host}:${c.port}`;
}

export async function wsUrl(): Promise<string> {
  const c = await conn();
  return `ws://${c.host}:${c.port}/ws?token=${encodeURIComponent(c.token)}`;
}

export async function authHeaders(): Promise<Record<string, string>> {
  const c = await conn();
  return c.token ? { Authorization: `Bearer ${c.token}` } : {};
}
