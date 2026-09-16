// Asks for support after good interviews, rarely: a pure rule for when, plus the saved state behind it.

import { openLink } from "./tauri";

export interface SupportState {
  goodSessions: number; // interviews with enough answers to count as useful
  shows: number; // times the prompt has been shown, ever
  lastShownAt: number | null; // epoch ms
  supported: boolean; // the user said they donated
}

// An interview needs this many answers to count as one that helped.
export const MIN_ANSWERS = 1;
// The prompt may ask from this many good interviews onwards.
export const FIRST_ASK_AT = 1;
// Lifetime cap, so it stays a rare question rather than a nag.
export const MAX_SHOWS = 5;
export const ASK_EVERY_DAYS = 21;
const DAY_MS = 24 * 60 * 60 * 1000;

export const EMPTY_STATE: SupportState = {
  goodSessions: 0,
  shows: 0,
  lastShownAt: null,
  supported: false,
};

export function recordSession(state: SupportState, answered: number): SupportState {
  return answered >= MIN_ANSWERS ? { ...state, goodSessions: state.goodSessions + 1 } : state;
}

// Whether to ask after an interview with `answered` answers, given the state that already counts it.
export function shouldAsk(state: SupportState, answered: number, now: number): boolean {
  if (answered < MIN_ANSWERS || state.supported || state.shows >= MAX_SHOWS) return false;
  if (state.goodSessions < FIRST_ASK_AT) return false;
  return state.lastShownAt === null || now - state.lastShownAt >= ASK_EVERY_DAYS * DAY_MS;
}

export interface SupportLink {
  label: string;
  url: string;
}

// Donation pages. A URL still holding a placeholder is hidden; each host must also be in open_link's allowlist.
const LINKS: SupportLink[] = [
  { label: "GitHub Sponsors", url: "https://github.com/sponsors/PSYEONE" },
  { label: "Ko-fi", url: "https://ko-fi.com/YOUR_USERNAME" },
];
export const supportLinks: SupportLink[] = LINKS.filter((l) => !l.url.includes("YOUR_"));

// Opens a pre-filled issue in the browser; nothing is sent unless the user submits it there.
export const FEEDBACK_URL =
  "https://github.com/PSYEONE/interviewr-ai/issues/new?" +
  new URLSearchParams({
    title: "Feedback: ",
    body: "**What were you doing?**\n\n\n**What went wrong or could be better?**\n\n",
  }).toString();

export function openSupportLink(url: string): void {
  void openLink(url).catch(() => {
    /* no browser available; nothing else to do */
  });
}

const STORAGE_KEY = "interviewr.support";

class Support {
  #state: SupportState = this.#load();

  #load(): SupportState {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? { ...EMPTY_STATE, ...(JSON.parse(raw) as Partial<SupportState>) } : { ...EMPTY_STATE };
    } catch {
      return { ...EMPTY_STATE };
    }
  }

  #save(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.#state));
    } catch {
      /* non-fatal */
    }
  }

  // Counts a finished interview and says whether to show the prompt now.
  recordSession(answered: number): boolean {
    this.#state = recordSession(this.#state, answered);
    this.#save();
    return shouldAsk(this.#state, answered, Date.now());
  }

  markShown(): void {
    this.#state = { ...this.#state, shows: this.#state.shows + 1, lastShownAt: Date.now() };
    this.#save();
  }

  markSupported(): void {
    this.#state = { ...this.#state, supported: true };
    this.#save();
  }
}

export const support = new Support();
