// Persist settings and bring the engine back up on the new config (the same dance the wizard's
// Done step performs). Shared by every dashboard control that changes a setting needing a restart.

import { postSettings, waitForHealth } from "./api";
import { engineManaged, restartEngine } from "./tauri";

export type ApplyResult = "applied" | "manual";

// "applied" = engine restarted and is ready on the new config. "manual" = the engine isn't
// sidecar-managed (dev / externally run), so the caller should tell the user to restart it.
export async function applyAndRestart(
  updates: Record<string, unknown>,
  onStatus?: (msg: string) => void,
): Promise<ApplyResult> {
  onStatus?.("Saving…");
  await postSettings(updates);

  const managed = await engineManaged().catch(() => false);
  if (!managed) return "manual";

  onStatus?.("Restarting engine…");
  try {
    await restartEngine();
  } catch (e) {
    if ((e instanceof Error ? e.message : String(e)).includes("not_managed")) return "manual";
    throw e;
  }

  onStatus?.("Waiting for the engine…");
  await waitForHealth((h) => h.configured && h.ready);
  return "applied";
}
