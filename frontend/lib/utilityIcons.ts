// One symbol per kind of utility, shared by the panels (inline SVG) and the map (drawn onto a canvas).
// Paths use a 24 x 24 box.

import type { ResearchCategory, ResearchProject } from "./types";

export type UtilityKind = "electric" | "gas" | "water" | "road";

export const ICON_PATH: Record<UtilityKind, string> = {
  electric: "M13.5 2 5 13.5h5.5L9.5 22 19 10h-5.5L15 2z",
  gas: "M12 2c1.2 3.8-1.8 5.4-1.8 8.2 0 1.3.9 2.2 2 2.2 1.4 0 2.2-1.2 1.9-2.9 2.4 1.6 3.9 4 3.9 6.6A6 6 0 0 1 12 22a6 6 0 0 1-6-5.9C6 10.6 12 8 12 2z",
  water: "M12 2.5C8.2 7.5 5.5 11.2 5.5 14.8A6.5 6.5 0 0 0 12 21.3a6.5 6.5 0 0 0 6.5-6.5c0-3.6-2.7-7.3-6.5-12.3z",
  road: "M8.5 2h2.6v4.5h1.8V2h2.6L20 22h-6.9v-4.5h-2.2V22H4L8.5 2zm2.6 7.5v5h1.8v-5h-1.8z",
};

export const KIND_LABEL: Record<UtilityKind, string> = { electric: "Electric", gas: "Gas", water: "Water", road: "Road" };

// Roads and water share a research category; the record's own words say which it is.
const WATERISH = /water|sewer|wastewater|reclamation|treatment plant|harbor|harbour|port|terminal|dam\b|lock|beach|river|canal|dredg/i;

export function kindOf(r: Pick<ResearchProject, "category" | "name" | "utility" | "utility_kind">): UtilityKind {
  if (r.category === "electric") return "electric";
  if (r.category === "gas") return "gas";
  return WATERISH.test(`${r.name} ${r.utility} ${r.utility_kind}`) ? "water" : "road";
}

// The symbols a research category can show (the picker and legend show both for roads and water).
export const CATEGORY_KINDS: Record<ResearchCategory, UtilityKind[]> = {
  electric: ["electric"], gas: ["gas"], roads_water: ["road", "water"],
};
