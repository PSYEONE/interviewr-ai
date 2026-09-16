// The support prompt must stay rare: only after answered interviews, spaced out, capped, and never after donating.

import { describe, expect, it, vi } from "vitest";

vi.mock("./tauri", () => ({ openLink: vi.fn() }));
const { ASK_EVERY_DAYS, EMPTY_STATE, MAX_SHOWS, recordSession, shouldAsk, supportLinks } =
  await import("./support");

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.UTC(2026, 8, 16);

function afterInterviews(n: number, answered = 1) {
  let state = { ...EMPTY_STATE };
  for (let i = 0; i < n; i++) state = recordSession(state, answered);
  return state;
}

describe("shouldAsk", () => {
  it("asks after the first interview with an answer", () => {
    expect(shouldAsk(afterInterviews(1), 1, NOW)).toBe(true);
  });

  it("does not ask after an interview with no answers", () => {
    const state = afterInterviews(1, 0);
    expect(state.goodSessions).toBe(0);
    expect(shouldAsk(afterInterviews(3), 0, NOW)).toBe(false);
  });

  it("waits the full interval after the last ask", () => {
    const shown = { ...afterInterviews(2), shows: 1, lastShownAt: NOW };
    expect(shouldAsk(shown, 1, NOW + (ASK_EVERY_DAYS - 1) * DAY)).toBe(false);
    expect(shouldAsk(shown, 1, NOW + ASK_EVERY_DAYS * DAY)).toBe(true);
  });

  it("stops for good after the lifetime cap", () => {
    const capped = { ...afterInterviews(9), shows: MAX_SHOWS, lastShownAt: NOW - 365 * DAY };
    expect(shouldAsk(capped, 1, NOW)).toBe(false);
  });

  it("never asks again once the user has donated", () => {
    const supported = { ...afterInterviews(4), supported: true };
    expect(shouldAsk(supported, 1, NOW)).toBe(false);
  });
});

describe("supportLinks", () => {
  it("hides links that still hold a placeholder", () => {
    expect(supportLinks.length).toBeGreaterThan(0);
    expect(supportLinks.every((l) => !l.url.includes("YOUR_"))).toBe(true);
  });
});
