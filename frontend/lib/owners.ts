// Who owns a project, and how it looks: name, color, marker shape, date wording. All of it comes from
// GET /api/sources (the backend's sources table), never from a hardcoded map. A project finds its source by
// source_id, or by its utility key for recordings made before sources existed.

import type { CSSProperties } from "react";
import { create } from "zustand";

import type { Project, SourceView } from "./types";

// Changing the list re-renders whatever reads it.
export const useSources = create<{ list: SourceView[] }>(() => ({ list: [] }));

let byId = new Map<string, SourceView>();
let byKey = new Map<string, SourceView>();

export function setSources(list: SourceView[]): void {
  byId = new Map(list.map((s) => [s.id, s]));
  byKey = new Map();
  for (const s of list) if (!byKey.has(s.utility_key)) byKey.set(s.utility_key, s);
  useSources.setState({ list });
}

export const sourceOf = (p: Project | undefined): SourceView | undefined =>
  p ? (p.source_id ? byId.get(p.source_id) : undefined) ?? byKey.get(p.utility) : undefined;

const UNKNOWN = "#8b93a4"; // --muted: a project whose source is gone (an old recording of a removed plan)

export const colorOf = (p: Project | undefined) => sourceOf(p)?.color ?? UNKNOWN;
export const shapeOf = (p: Project | undefined): "circle" | "diamond" => (sourceOf(p)?.display.shape === "diamond" ? "diamond" : "circle");
// For elements styled with var(--own): list rows, pair cards, markers.
export const ownStyle = (p: Project | undefined) => ({ "--own": colorOf(p) }) as CSSProperties;
export const builtin = (p: Project | undefined) => Boolean(sourceOf(p)?.builtin);

// A filing that lists several owners (Georgia's plan: GPC, SAV, GTC, ...) names the row's owner; otherwise the source.
export function ownerName(p: Project): string {
  const s = sourceOf(p);
  if (!s) return p.sponsor;
  if (s.sponsors.length > 1) return s.sponsors.find((x) => x.code === p.sponsor)?.name ?? p.sponsor;
  return s.display.ui_name ?? s.display_name;
}

export const ownerShort = (p: Project) => sourceOf(p)?.display.short_name ?? p.sponsor;

// Georgia's plan gives a need date; everyone else an in-service date.
export const dateWord = (p: Project) => sourceOf(p)?.display.date_label ?? "In service";

// "redacted" (Georgia), "public" (Dominion) or "stated" (anything else).
export const costKind = (p: Project) => sourceOf(p)?.display.costs ?? "stated";
export const showsStatus = (p: Project) => sourceOf(p)?.display.show_status !== false;

// An owner inside a filing that isn't shown by default (Georgia's GTC, MEAG, DU): same rule as the backend.
export function hiddenByDefault(p: Project): boolean {
  const s = sourceOf(p);
  return Boolean(s && s.sponsors.length) && !s!.sponsors.some((x) => x.code === p.sponsor && x.default);
}

// The owners the "all sponsors" filter adds, for its label: "GTC, MEAG, DU".
export const extraSponsors = (list: SourceView[]) =>
  list.filter((s) => s.status === "active").flatMap((s) => s.sponsors.filter((x) => !x.default).map((x) => x.code));

// Owner prefixes a filing puts in its project names ("SAV: ..."), dropped in list rows.
export function namePrefixes(list: SourceView[]): string[] {
  return list.flatMap((s) => s.sponsors.map((x) => x.code).filter((c) => c !== s.code));
}

// Sources the legend shows: every active one, plus any that the current run's projects come from.
export function legendSources(list: SourceView[], projects: Project[]): SourceView[] {
  const used = new Set(projects.map((p) => sourceOf(p)?.id).filter(Boolean));
  return list.filter((s) => s.status === "active" || used.has(s.id));
}

// Where a Reader agent sits on the map while it reads (a utility's headquarters), by agent id.
export function readerHome(agentId: string): { lon: number; lat: number; label: string } | undefined {
  for (const s of byId.values()) if (s.agent_id === agentId && s.display.hq) return s.display.hq;
  return undefined;
}

// A submitted plan may give only a year or month; say so instead of printing a made-up day.
export const approxDate = (p: Project | undefined) => Boolean(p && p.date_precision && p.date_precision !== "day");

export function serviceDate(p: Project, fmt: (iso: string) => string): string {
  if (p.date_precision === "year") return `${p.in_service_date.slice(0, 4)} (year only)`;
  if (p.date_precision === "month") {
    const [y, m] = p.in_service_date.split("-");
    return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
  }
  return fmt(p.in_service_date);
}

// "about 730" when either side's date is only a year or month.
export const gapText = (days: number, a?: Project, b?: Project) =>
  `${approxDate(a) || approxDate(b) ? "about " : ""}${days.toLocaleString()}`;

// Where a project came from: a page of a built-in filing, or a row or page of a submitted plan.
export const whereFrom = (p: Project) =>
  builtin(p) ? `${p.source_file}, page ${p.source_page} (${p.source_ref})` : `${p.source_file}, ${p.source_ref}`;
