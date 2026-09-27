// Shared by the Setup modal's sections.

import type { SetupRef } from "@/lib/types";

export const keyOf = (r: SetupRef) => `${r.provider}/${r.model}`;
export const parseKey = (v: string): SetupRef => {
  const i = v.indexOf("/");
  return { provider: v.slice(0, i), model: v.slice(i + 1) };
};
export const same = (a: SetupRef[], b: SetupRef[]) => a.length === b.length && a.every((x, i) => keyOf(x) === keyOf(b[i]));
export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

// One color per model: the provider's hue, a step lighter or deeper for each further model of that provider.
const HUES: Record<string, string[]> = {
  jev: ["#fb923c", "#fdba74", "#ea580c", "#fed7aa"],
  gemini: ["#e879f9", "#f0abfc", "#c084fc", "#d946ef", "#f5d0fe"],
  claude: ["#fda4af", "#fb7185", "#fecdd3"],
  openai: ["#22d3ee", "#67e8f9", "#06b6d4"],
};
const OTHER = ["#94a1b9", "#cbd5e1", "#7f8ba5"];

export function modelColors(keys: string[]): Record<string, string> {
  const seen: Record<string, number> = {};
  const out: Record<string, string> = {};
  for (const k of keys) {
    if (out[k]) continue;
    const p = k.slice(0, k.indexOf("/"));
    const list = HUES[p] ?? OTHER;
    out[k] = list[(seen[p] = (seen[p] ?? -1) + 1) % list.length];
  }
  return out;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
