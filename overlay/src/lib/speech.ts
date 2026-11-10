// Labels for the speech model and language pickers, shared by the wizard and the settings screen.

import type { WhisperOption } from "./types";

export const AUTO_LANGUAGE = "auto";

// "small.en · 466 MB · English only", plus a warning when it won't fit the GPU. The measured error
// rate is left out: the list is ordered by size, and "bigger is more accurate" says it without jargon.
export function speechLabel(m: WhisperOption, warnFit = false): string {
  const size =
    m.download_mb >= 1024 ? `${(m.download_mb / 1024).toFixed(1)} GB` : `${m.download_mb} MB`;
  const scope = m.english_only ? "English only" : "multilingual";
  const warn = warnFit && m.fit === "too_big" ? ", may not fit this GPU" : "";
  return `${m.name} · ${size} · ${scope}${warn}`;
}

let names: Intl.DisplayNames | null | undefined;

// "bg" -> "Bulgarian"; the bare code when the browser has no name for it (whisper's "jw").
export function languageName(code: string): string {
  if (code === AUTO_LANGUAGE) return "Auto-detect";
  if (names === undefined) {
    try {
      names = new Intl.DisplayNames(["en"], { type: "language" });
    } catch {
      names = null;
    }
  }
  try {
    return names?.of(code) ?? code;
  } catch {
    return code;
  }
}

export interface LanguageOption {
  code: string;
  label: string;
}

// Auto first, then the codes by English name, so whisper's list of 99 is scannable. A saved code
// stays selectable even when the list has not loaded, so saving cannot blank it.
export function languageOptions(codes: string[], saved = ""): LanguageOption[] {
  const all = new Set(codes);
  if (saved && saved !== AUTO_LANGUAGE) all.add(saved);
  const sorted = [...all]
    .filter((code) => code !== AUTO_LANGUAGE)
    .map((code) => ({ code, label: languageName(code) }))
    .sort((a, b) => a.label.localeCompare(b.label));
  return [{ code: AUTO_LANGUAGE, label: "Auto-detect" }, ...sorted];
}

// Whether the pair the user is about to save can transcribe the language they asked for.
export function englishOnlyMismatch(language: string, model: WhisperOption | string | undefined): boolean {
  if (language === AUTO_LANGUAGE || language === "en" || model === undefined) return false;
  return typeof model === "string" ? model.endsWith(".en") : model.english_only;
}
