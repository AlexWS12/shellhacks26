"use client";

// Streets basemap online (OpenFreeMap dark, no key), bundled basemap offline.

import maplibregl, { type GeoJSONSource, type LngLatBoundsLike, type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef } from "react";

import { activeIn, lineCheck, lineEnds, researchActiveIn, visible } from "@/lib/filters";
import { CATEGORY_LABEL, engineColor } from "@/lib/format";
import { run, useRev } from "@/lib/run";
import { builtin, colorOf, legendSources, shapeOf, useSources } from "@/lib/owners";
import { CATEGORY_KINDS, ICON_PATH, kindOf, type UtilityKind } from "@/lib/utilityIcons";
import { mapPalette } from "@/lib/theme";
import type { Overlap, Project, ResearchCategory, ThirdParty } from "@/lib/types";
import { showLatestResults, startRun, useUI } from "@/lib/ui";

import ResearchPicker from "./ResearchPicker";
import UtilityIcon from "./UtilityIcon";

const STREETS = "https://tiles.openfreemap.org/styles/dark";
const US: LngLatBoundsLike = [[-125, 24.3], [-66.5, 49.5]];
const BORDER: LngLatBoundsLike = [[-85.7, 30.3], [-78.4, 35.3]];
const RIVER: LngLatBoundsLike = [[-82.7, 31.9], [-80.5, 34.0]]; // Augusta to Savannah, where the overlaps are
const RING_MI = 25;
const PIN_DROP_MS = 800;
const TRAVEL_MS = 1100; // neon ball from one project to the other when a connection is found
const DWELL_MS = 550; // how long an agent marker stays on each thing it works on
const MAX_QUEUE = 6; // if an agent is faster than that, skip ahead but keep moving visibly
// marching-ants dash steps for the connection lines
const DASHES = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
const YEARS = { min: 2023, max: 2034 };
const POINT_LAYERS = ["points", "points-diamond"]; // each source draws as circles or diamonds (display.shape)

type FC = GeoJSON.FeatureCollection;
const fc = (features: GeoJSON.Feature[]): FC => ({ type: "FeatureCollection", features });

function simpleStyle(): StyleSpecification {
  const c = mapPalette();
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      states: { type: "geojson", data: "/geo/states.json" },
      counties: { type: "geojson", data: "/geo/counties_sc_ga.json" },
      borders: { type: "geojson", data: "/geo/state_borders.json" },
    },
    layers: [
      { id: "water", type: "background", paint: { "background-color": c.water } },
      { id: "land", type: "fill", source: "states", paint: { "fill-color": ["case", ["get", "focus"], c.landFocus, c.land] } },
      { id: "counties", type: "line", source: "counties", paint: { "line-color": c.county, "line-width": 0.5 } },
      { id: "borders", type: "line", source: "borders", paint: { "line-color": c.border, "line-width": 1 } },
    ],
  };
}

// 25 mi ring, display only
function circle(lon: number, lat: number, mi: number): GeoJSON.Feature {
  const pts: [number, number][] = [];
  const dLat = mi / 69.0;
  const dLon = mi / (69.0 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= 64; i++) {
    const a = (i / 64) * 2 * Math.PI;
    pts.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [pts] } };
}

// A curved line from a to b, so links between the same places don't sit on top of each other.
function arc(a: [number, number], b: [number, number]): [number, number][] {
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const cx = mx - dy * 0.25, cy = my + dx * 0.25;
  const out: [number, number][] = [];
  for (let i = 0; i <= 24; i++) {
    const t = i / 24;
    out.push([(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * cx + t * t * b[0], (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * cy + t * t * b[1]]);
  }
  return out;
}

function currentOverlaps(): Overlap[] {
  const results = useUI.getState().results;
  return run.phase === "done" && results ? results : run.overlaps;
}

function currentOthers(): ThirdParty[] {
  const others = useUI.getState().others;
  return run.phase === "done" && others ? others : run.thirdParty;
}

function selectedPair(): { a: string; b: string } | null {
  const p = useUI.getState().panel;
  return p.kind === "pair" ? { a: p.a, b: p.b } : null;
}

function buildData() {
  const { filters, health, year, hiddenCats: hidden } = useUI.getState();
  const today = health?.today ?? "2026-09-26";
  const shown = Object.values(run.projects).filter((p) => visible(p, filters, today) && activeIn(p, year));
  const ids = new Set(shown.map((p) => p.id));
  // hollow = approximate: a weak source, or a line we won't draw (so the point is only roughly on the work)
  const hollow = (p: Project) => {
    const l = lineCheck(p);
    return !["verified", "confirmed_osm"].includes(p.location_confidence) || (l.span != null && !l.draw);
  };
  const points = fc(shown.map((p) => ({
    type: "Feature",
    // color and shape come from the project's source (/api/sources): Georgia draws as diamonds
    properties: { id: p.id, color: colorOf(p), shape: shapeOf(p), hollow: hollow(p), name: builtin(p) ? p.name : `${p.sponsor}: ${p.name}` },
    geometry: { type: "Point", coordinates: [p.lon!, p.lat!] },
  })));
  const lines = fc(shown.flatMap((p) => {
    if (!lineCheck(p).draw) return []; // one end, or too long to trust or to draw: the (hollow) point stays
    const eps = lineEnds(p);
    return [{ type: "Feature", properties: { id: p.id, color: colorOf(p), hollow: hollow(p) },
      geometry: { type: "LineString", coordinates: eps.map((e) => [e.lon!, e.lat!]) } } as GeoJSON.Feature];
  }));
  const sel = selectedPair();
  const pairs = currentOverlaps().filter((o) => ids.has(o.project_a) && ids.has(o.project_b));
  const links: GeoJSON.Feature[] = [];
  const labels: GeoJSON.Feature[] = [];
  const t = performance.now();
  for (const o of pairs) {
    const born = run.linkBorn[o.id];
    if (born && t - born < TRAVEL_MS) continue; // the ball is still drawing this one
    const a = run.projects[o.project_a], b = run.projects[o.project_b];
    const isSel = sel ? sel.a === o.project_a && sel.b === o.project_b : false;
    const path = arc([a.lon!, a.lat!], [b.lon!, b.lat!]);
    const props = { a: o.project_a, b: o.project_b, sel: isSel, dim: Boolean(sel) && !isSel, together: o.windows_overlap === true };
    links.push({ type: "Feature", properties: props, geometry: { type: "LineString", coordinates: path } });
    labels.push({ type: "Feature", properties: { ...props, label: `${o.distance_mi.toFixed(1)} mi` },
      geometry: { type: "Point", coordinates: path[12] } });
  }
  const ring = fc(sel && run.projects[sel.a]?.lat != null ? [circle(run.projects[sel.a].lon!, run.projects[sel.a].lat!, RING_MI)] : []);

  // other utilities' projects (research team)
  const panel = useUI.getState().panel;
  const selId = sel ? `${sel.a}|${sel.b}` : null;
  const linkedTo = currentOthers().filter((t) => t.overlap_id === selId);
  const linked = new Set(linkedTo.map((t) => t.research_id));
  const openId = panel.kind === "research" ? panel.id : null;
  const others = fc(Object.values(run.research).flatMap((r) => {
    if (r.lat == null || r.lon == null || !researchActiveIn(r, year) || hidden.includes(r.category)) return [];
    const approx = !["verified", "confirmed_osm"].includes(r.location_confidence);
    if (approx && !filters.townLevel) return [];
    const on = linked.has(r.id) || r.id === openId;
    return [{ type: "Feature", properties: { id: r.id, c: r.category, k: kindOf(r), name: `${r.utility}: ${r.name}`, label: r.utility,
      hollow: approx, sel: on, dim: Boolean(sel) && !on }, geometry: { type: "Point", coordinates: [r.lon, r.lat] } } as GeoJSON.Feature];
  }));
  const otherLinks = fc(linkedTo.filter((t) => !hidden.includes(t.category)).flatMap((t) => {
    const r = run.research[t.research_id];
    if (!r || r.lat == null || !sel) return [];
    return [sel.a, sel.b].flatMap((pid) => {
      const p = run.projects[pid];
      return p?.lat != null ? [{ type: "Feature", properties: { c: r.category },
        geometry: { type: "LineString", coordinates: [[r.lon!, r.lat!], [p.lon!, p.lat!]] } } as GeoJSON.Feature] : [];
    });
  }));
  return { points, lines, links: fc(links), labels: fc(labels), ring, others, otherLinks };
}

// Balls traveling along connections that were just found. Returns them plus how many are in flight.
function comets(): { data: FC; flying: number } {
  const t = performance.now();
  const feats: GeoJSON.Feature[] = [];
  for (const o of run.overlaps) {
    const born = run.linkBorn[o.id];
    if (!born || t - born >= TRAVEL_MS) continue;
    const a = run.projects[o.project_a], b = run.projects[o.project_b];
    if (!a?.lat || !b?.lat) continue;
    const path = arc([a.lon!, a.lat!], [b.lon!, b.lat!]);
    const k = Math.min(path.length - 1, Math.floor(((t - born) / TRAVEL_MS) * (path.length - 1)));
    feats.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: path.slice(0, k + 1).length > 1 ? path.slice(0, k + 1) : [path[0], path[0]] } });
    feats.push({ type: "Feature", properties: { head: true }, geometry: { type: "Point", coordinates: path[k] } });
  }
  return { data: fc(feats), flying: feats.length / 2 };
}

// A square turned 45deg, drawn at 2x. 12 css px corner to corner at icon-size 1.
const imagesHooked = new WeakSet<maplibregl.Map>();

// A utility symbol (bolt, flame, drop, road) in the pin's dark color, drawn at 2x for sharp edges.
function addGlyph(map: maplibregl.Map, kind: UtilityKind, color: string, suffix = "") {
  const id = `glyph-${kind}${suffix}`;
  if (map.hasImage(id)) return;
  const n = 32;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = n;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(n / 24, n / 24);
  ctx.fillStyle = color;
  ctx.fill(new Path2D(ICON_PATH[kind]), "evenodd");
  map.addImage(id, ctx.getImageData(0, 0, n, n), { pixelRatio: 2 });
}

function addDiamond(map: maplibregl.Map, id: string, fill: string, stroke: string) {
  if (map.hasImage(id)) return;
  const n = 24, c = n / 2, r = c - 2;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = n;
  const ctx = canvas.getContext("2d")!;
  ctx.beginPath();
  ctx.moveTo(c, c - r); ctx.lineTo(c + r, c); ctx.lineTo(c, c + r); ctx.lineTo(c - r, c);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.lineWidth = 3.2;
  ctx.strokeStyle = stroke;
  ctx.stroke();
  map.addImage(id, ctx.getImageData(0, 0, n, n), { pixelRatio: 2 });
}

function addDataLayers(map: maplibregl.Map) {
  const COLORS = mapPalette();
  const color = ["get", "color"] as maplibregl.ExpressionSpecification; // each feature carries its source's color
  // Icons in a source's color are drawn on first use, one per color: "diamond-<hex>" filled, "diamond-hollow-<hex>"
  // outlined, and "glyph-electric-o-<hex>" (the bolt inside a hollow pin).
  if (!imagesHooked.has(map)) {
    imagesHooked.add(map);
    map.on("styleimagemissing", (e: { id: string }) => {
      const m = /^diamond-(hollow-)?(#[0-9a-f]{3,8})$/i.exec(e.id);
      if (m) addDiamond(map, e.id, m[1] ? COLORS.bg : m[2], m[2]);
      const g = /^glyph-electric-o-(#[0-9a-f]{3,8})$/i.exec(e.id);
      if (g) addGlyph(map, "electric", g[1], `-o-${g[1]}`);
    });
  }
  for (const id of ["ring", "lines", "links", "labels", "points", "pulse", "comets", "others", "other-links"]) {
    if (!map.getSource(id)) map.addSource(id, { type: "geojson", data: fc([]) });
  }
  const linkOpacity = ["case", ["get", "dim"], 0.12, 1] as maplibregl.ExpressionSpecification;
  map.addLayer({ id: "ring-fill", type: "fill", source: "ring", paint: { "fill-color": COLORS.zone, "fill-opacity": 0.05 } });
  map.addLayer({ id: "ring-line", type: "line", source: "ring", paint: { "line-color": COLORS.zone, "line-width": 1, "line-opacity": 0.6, "line-dasharray": [2, 2] } });
  map.addLayer({ id: "project-lines-glow", type: "line", source: "lines",
    paint: { "line-color": color, "line-width": 6, "line-blur": 4, "line-opacity": 0.25 }, layout: { "line-cap": "round" } });
  map.addLayer({ id: "project-lines", type: "line", source: "lines",
    paint: { "line-color": color, "line-width": 2, "line-opacity": ["case", ["get", "hollow"], 0.5, 0.95] }, layout: { "line-cap": "round" } });
  map.addLayer({ id: "links-glow", type: "line", source: "links",
    paint: { "line-color": COLORS.zone, "line-width": ["case", ["get", "sel"], 12, 7], "line-blur": 6,
      "line-opacity": ["*", linkOpacity, ["case", ["get", "together"], 0.45, 0.22]] }, layout: { "line-cap": "round" } });
  map.addLayer({ id: "links", type: "line", source: "links",
    paint: { "line-color": ["case", ["get", "together"], COLORS.zone, COLORS.zoneFar], "line-width": ["case", ["get", "sel"], 3, 2],
      "line-opacity": linkOpacity, "line-dasharray": DASHES[0] } });
  map.addLayer({ id: "links-hit", type: "line", source: "links", paint: { "line-color": COLORS.bg, "line-opacity": 0, "line-width": 14 } });
  map.addLayer({ id: "pulse", type: "circle", source: "pulse",
    paint: { "circle-radius": ["get", "r"], "circle-color": color, "circle-opacity": 0, "circle-stroke-color": color,
      "circle-stroke-width": 2, "circle-stroke-opacity": ["get", "o"] } });
  map.addLayer({ id: "comet-trail", type: "line", source: "comets", filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": COLORS.zone, "line-width": 3, "line-blur": 2, "line-opacity": 0.8 }, layout: { "line-cap": "round" } });
  map.addLayer({ id: "comet-glow", type: "circle", source: "comets", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 16, "circle-color": COLORS.zone, "circle-blur": 1, "circle-opacity": 0.75 } });
  map.addLayer({ id: "comet-core", type: "circle", source: "comets", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 4.5, "circle-color": COLORS.spark } });
  map.addLayer({ id: "points-glow", type: "circle", source: "points",
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 7, 9, 13], "circle-color": color, "circle-blur": 1, "circle-opacity": 0.35 } });
  map.addLayer({ id: "points", type: "circle", source: "points", filter: ["!=", ["get", "shape"], "diamond"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 3, 9, 6],
      "circle-color": ["case", ["get", "hollow"], COLORS.bg, color],
      "circle-stroke-color": color,
      "circle-stroke-width": ["case", ["get", "hollow"], 1.6, 0],
    } });
  // A source whose shape is "diamond" (Georgia) draws as diamonds, so shape and not just color tells owners apart
  map.addLayer({ id: "points-diamond", type: "symbol", source: "points", filter: ["==", ["get", "shape"], "diamond"],
    layout: {
      "icon-image": ["concat", ["case", ["get", "hollow"], "diamond-hollow-", "diamond-"], ["get", "color"]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 4, 0.55, 9, 1.05],
      "icon-allow-overlap": true, "icon-ignore-placement": true,
    } });
  // a bolt on every source's project once zoomed in (they are all electric plans): dark on a solid pin, in the
  // source's color inside a hollow one
  addGlyph(map, "electric", COLORS.bg);
  map.addLayer({ id: "points-glyph", type: "symbol", source: "points", minzoom: 7,
    layout: { "icon-image": ["case", ["get", "hollow"], ["concat", "glyph-electric-o-", ["get", "color"]], "glyph-electric"],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 7, 0.5, 10, 0.8],
      "icon-allow-overlap": true, "icon-ignore-placement": true } });
  const cat = ["match", ["get", "c"], "electric", COLORS.catElectric, "gas", COLORS.catGas, COLORS.catRoads] as maplibregl.ExpressionSpecification;
  map.addLayer({ id: "other-links", type: "line", source: "other-links",
    paint: { "line-color": cat, "line-width": 1.4, "line-opacity": 0.85, "line-dasharray": [1, 2] } });
  map.addLayer({ id: "others-sel", type: "circle", source: "others", filter: ["get", "sel"],
    paint: { "circle-radius": 11, "circle-color": cat, "circle-opacity": 0.15, "circle-stroke-color": cat, "circle-stroke-width": 1.5 } });
  map.addLayer({ id: "others", type: "circle", source: "others",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 4.5, 7, 7, 10, 9.5],
      "circle-color": ["case", ["get", "hollow"], COLORS.bg, cat],
      "circle-stroke-color": ["case", ["get", "hollow"], cat, COLORS.bg],
      "circle-stroke-width": ["case", ["get", "hollow"], 1.8, 1.2],
      "circle-opacity": ["case", ["get", "dim"], 0.25, 1],
      "circle-stroke-opacity": ["case", ["get", "dim"], 0.25, 1],
    } });
  // solid pins get a dark symbol; hollow (approximate) pins get the symbol in their category color inside the ring
  const kindColor: Record<UtilityKind, string> = { electric: COLORS.catElectric, gas: COLORS.catGas, water: COLORS.catRoads, road: COLORS.catRoads };
  for (const k of Object.keys(ICON_PATH) as UtilityKind[]) {
    addGlyph(map, k, COLORS.bg);
    addGlyph(map, k, kindColor[k], "-h");
  }
  map.addLayer({ id: "others-glyph", type: "symbol", source: "others", minzoom: 5.5,
    layout: { "icon-image": ["concat", "glyph-", ["get", "k"], ["case", ["get", "hollow"], "-h", ""]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 5.5, 0.65, 10, 1.1],
      "icon-allow-overlap": true, "icon-ignore-placement": true },
    paint: { "icon-opacity": ["case", ["get", "dim"], 0.25, 1] } });
  map.addLayer({ id: "other-labels", type: "symbol", source: "others", filter: ["get", "sel"],
    layout: { "text-field": ["get", "label"], "text-size": 11.5, "text-font": ["Noto Sans Bold"], "text-offset": [0, 1.3],
      "text-anchor": "top", "text-allow-overlap": false },
    paint: { "text-color": cat, "text-halo-color": COLORS.bg, "text-halo-width": 2 } });
  map.addLayer({ id: "link-labels", type: "symbol", source: "labels",
    filter: ["any", ["get", "sel"], [">=", ["zoom"], 6.3]],
    layout: { "text-field": ["get", "label"], "text-size": ["case", ["get", "sel"], 14, 12], "text-font": ["Noto Sans Bold"],
      "text-allow-overlap": false, "text-padding": 4 },
    paint: { "text-color": COLORS.zone, "text-halo-color": COLORS.bg, "text-halo-width": 2.5, "text-opacity": ["case", ["get", "dim"], 0.2, 1] } });
}

// One labeled beacon per working agent. Each stop is held for DWELL_MS so people can follow it.
type Stop = { lon: number; lat: number; label: string };
type Beacon = { m: maplibregl.Marker; cur: [number, number]; label: HTMLElement; queue: Stop[]; lastT: number; arrivedAt: number };

class AgentMarkers {
  private markers = new Map<string, Beacon>();
  constructor(private map: maplibregl.Map) {}

  sync() {
    for (const [id, pos] of Object.entries(run.agentPos)) {
      const agent = run.agents[id];
      if (!agent) continue;
      let b = this.markers.get(id);
      if (!b) {
        const el = document.createElement("div");
        el.className = "agent-marker";
        el.style.setProperty("--c", engineColor(agent.engine ?? ""));
        const pin = document.createElement("i");
        pin.className = "pin";
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = agent.name;
        const em = document.createElement("em");
        tag.appendChild(em);
        el.append(pin, tag);
        const m = new maplibregl.Marker({ element: el, anchor: "left", offset: [-6, 0] }).setLngLat([pos.lon, pos.lat]).addTo(this.map);
        b = { m, cur: [pos.lon, pos.lat], label: em, queue: [], lastT: 0, arrivedAt: 0 };
        this.markers.set(id, b);
      }
      if (pos.t !== b.lastT) {
        b.lastT = pos.t;
        b.queue.push({ lon: pos.lon, lat: pos.lat, label: pos.label });
        if (b.queue.length > MAX_QUEUE) b.queue.splice(1, b.queue.length - MAX_QUEUE);
      }
    }
  }

  step(now: number): boolean {
    let busy = false;
    for (const [id, b] of this.markers) {
      const target = b.queue[0];
      if (!target) {
        // nothing left to show: remove once the agent is done
        if (run.agents[id]?.status !== "working" || run.phase !== "running") {
          b.m.remove();
          this.markers.delete(id);
        }
        continue;
      }
      busy = true;
      const dx = target.lon - b.cur[0], dy = target.lat - b.cur[1];
      if (Math.abs(dx) + Math.abs(dy) > 0.002) {
        b.cur = [b.cur[0] + dx * 0.12, b.cur[1] + dy * 0.12];
        b.m.setLngLat(b.cur);
        b.arrivedAt = 0;
        continue;
      }
      if (!b.arrivedAt) {
        b.arrivedAt = now;
        b.label.textContent = target.label.length > 28 ? `${target.label.slice(0, 26)}…` : target.label;
      }
      if (now - b.arrivedAt >= DWELL_MS && b.queue.length > 1) {
        b.queue.shift();
        b.arrivedAt = 0;
      } else if (now - b.arrivedAt >= DWELL_MS && run.agents[id]?.status !== "working") {
        b.queue.shift();
      }
    }
    return busy;
  }

  clear() {
    for (const e of this.markers.values()) e.m.remove();
    this.markers.clear();
  }
}

export default function MapView() {
  const el = useRef<HTMLDivElement>(null);
  const basemap = useUI((s) => s.basemap);
  const year = useUI((s) => s.year);
  const setYear = useUI((s) => s.setYear);
  const phase = useRev(() => run.phase);
  const cats = useRev(() => run.researchSelected.join(","));
  const known = useSources((st) => st.list); // redraw the legend when a source is added or removed
  useRev(() => Object.keys(run.projects).length);
  const hiddenCats = useUI((s) => s.hiddenCats);
  const toggleCat = useUI((s) => s.toggleCat);

  useEffect(() => {
    if (!el.current) return;
    const style = basemap === "streets" && navigator.onLine ? STREETS : simpleStyle();
    const map = new maplibregl.Map({ container: el.current, style, bounds: US, fitBoundsOptions: { padding: 20 },
      attributionControl: { compact: true }, dragRotate: false });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    const agents = new AgentMarkers(map);
    let styleOk = false;
    const fallback = setTimeout(() => { if (!styleOk && basemap === "streets") useUI.getState().setBasemap("simple"); }, 6000);
    map.on("error", (e) => {
      if (!styleOk && basemap === "streets") useUI.getState().setBasemap("simple");
      else console.warn("map:", e.error?.message);
    });
    map.on("load", () => {
      styleOk = true;
      clearTimeout(fallback);
      addDataLayers(map);
      refresh();
    });
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
    for (const layer of POINT_LAYERS) {
      map.on("mouseenter", layer, (e) => {
        map.getCanvas().style.cursor = "pointer";
        const f = e.features?.[0];
        if (f) popup.setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number]).setText(String(f.properties.name)).addTo(map);
      });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; popup.remove(); });
      map.on("click", layer, (e) => {
        const id = e.features?.[0]?.properties.id;
        if (id) useUI.getState().setPanel({ kind: "project", id: String(id) });
      });
    }
    map.on("mouseenter", "others", (e) => {
      map.getCanvas().style.cursor = "pointer";
      const f = e.features?.[0];
      if (f) popup.setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number]).setText(String(f.properties.name)).addTo(map);
    });
    map.on("mouseleave", "others", () => { map.getCanvas().style.cursor = ""; popup.remove(); });
    map.on("click", "others", (e) => {
      const id = e.features?.[0]?.properties.id;
      if (id) useUI.getState().setPanel({ kind: "research", id: String(id) });
    });
    map.on("mouseenter", "links-hit", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "links-hit", () => (map.getCanvas().style.cursor = ""));
    map.on("click", "links-hit", (e) => {
      const p = e.features?.[0]?.properties;
      if (p) {
        useUI.getState().setPanel({ kind: "pair", a: String(p.a), b: String(p.b) });
        useUI.getState().flyTo({ kind: "pair", a: String(p.a), b: String(p.b) });
      }
    });

    function refresh() {
      if (!map.getSource("points")) return; // layers not added yet (isStyleLoaded() is false during every data update)
      drawSources();
      agents.sync();
      animate();
    }

    function drawSources() {
      const d = buildData();
      (map.getSource("points") as GeoJSONSource).setData(d.points);
      (map.getSource("lines") as GeoJSONSource).setData(d.lines);
      (map.getSource("links") as GeoJSONSource).setData(d.links);
      (map.getSource("labels") as GeoJSONSource).setData(d.labels);
      (map.getSource("ring") as GeoJSONSource).setData(d.ring);
      (map.getSource("others") as GeoJSONSource).setData(d.others);
      (map.getSource("other-links") as GeoJSONSource).setData(d.otherLinks);
    }

    let raf = 0;
    let flying = 0;
    let dash = 0;
    const ants = setInterval(() => {
      if (!map.getLayer("links")) return;
      dash = (dash + 1) % DASHES.length;
      map.setPaintProperty("links", "line-dasharray", DASHES[dash]);
    }, 70);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function animate() {
      if (raf) return;
      const tick = () => {
        const t = performance.now();
        const drops = reduced ? [] : Object.entries(run.recent).filter(([, t0]) => t - t0 < PIN_DROP_MS);
        (map.getSource("pulse") as GeoJSONSource | undefined)?.setData(fc(drops.flatMap(([id, t0]) => {
          const p = run.projects[id];
          if (!p || p.lat == null) return [];
          const k = (t - t0) / PIN_DROP_MS;
          return [{ type: "Feature", properties: { color: colorOf(p), r: 3 + 18 * k, o: 0.9 * (1 - k) },
            geometry: { type: "Point", coordinates: [p.lon!, p.lat!] } } as GeoJSON.Feature];
        })));
        const c = comets();
        (map.getSource("comets") as GeoJSONSource | undefined)?.setData(c.data);
        if (c.flying !== flying) {
          flying = c.flying;
          drawSources(); // a ball landed: show its finished connection
        }
        const moving = agents.step(t);
        raf = drops.length || moving || c.flying ? requestAnimationFrame(tick) : 0;
      };
      raf = requestAnimationFrame(tick);
    }

    const unsubRev = useRev.subscribe(refresh);
    // the column grid changes when the left rail's panel opens or closes
    const resize = new ResizeObserver(() => map.resize());
    resize.observe(el.current);
    const unsubUI = useUI.subscribe((s, prev) => {
      if (s.results !== prev.results || s.others !== prev.others || s.hiddenCats !== prev.hiddenCats || s.filters !== prev.filters || s.panel !== prev.panel || s.health !== prev.health || s.year !== prev.year) refresh();
      if (s.camera !== prev.camera) moveCamera(map, s.camera);
    });
    return () => {
      resize.disconnect();
      unsubRev();
      unsubUI();
      cancelAnimationFrame(raf);
      clearTimeout(fallback);
      clearInterval(ants);
      agents.clear();
      map.remove();
    };
  }, [basemap]);

  return (
    <>
      <div ref={el} className="map" />
      <div className="mapctl">
        <button onClick={() => useUI.getState().flyTo({ kind: "us" })}>US</button>
        <button onClick={() => useUI.getState().flyTo({ kind: "border" })}>SC–GA</button>
        <button onClick={() => useUI.getState().setBasemap(basemap === "streets" ? "simple" : "streets")}>
          {basemap === "streets" ? "Simple · offline" : "Streets · online"}
        </button>
      </div>
      <div className="legend" aria-label="Legend">
        {legendSources(known, Object.values(run.projects)).map((x) => (
          <span key={x.id}><i className={`sw own${x.display.shape === "diamond" ? " diamond" : ""}`} style={{ "--own": x.color } as React.CSSProperties} />
            {x.display.legend ?? x.display.ui_name ?? x.display_name}</span>
        ))}
        <span><i className="sw hollow" />Hollow = approximate location</span>
        <span><i className="sw zone" />Under 25 mi apart</span>
        {(cats ? (cats.split(",") as ResearchCategory[]) : []).map((c) => (
          <button key={c} className={`cat-${c} catkey ${hiddenCats.includes(c) ? "off" : ""}`} aria-pressed={!hiddenCats.includes(c)}
            onClick={() => toggleCat(c)} title={hiddenCats.includes(c) ? "Show on the map" : "Hide on the map"}>
            {CATEGORY_KINDS[c].map((k) => <UtilityIcon key={k} kind={k} />)}Other · {CATEGORY_LABEL[c]}
          </button>
        ))}
      </div>
      {phase !== "idle" && (
        <div className="timeline" aria-label="Timeline">
          <button onClick={() => setYear(null)} aria-pressed={year == null}>All</button>
          <input type="range" min={YEARS.min} max={YEARS.max} value={year ?? YEARS.min}
            onChange={(e) => setYear(Number(e.target.value))} aria-label="Year" />
          <span className="yr">{year ?? "all years"}</span>
        </div>
      )}
      {phase === "idle" && (
        <div className="overlay">
          <div className="card">
            <h2>Where Dominion and Georgia Power build close together</h2>
            <p>
              A team of AI agents reads Dominion Energy South Carolina&apos;s and Georgia Power&apos;s public construction
              plans, places every project on the map, and finds where they could build once instead of twice.
            </p>
            <div className="row2">
              <button className="primary" onClick={() => void startRun("live")}>Run pipeline</button>
              <button onClick={() => void startRun("replay")}>Replay a run</button>
              <button onClick={() => void showLatestResults()}>Jump to results</button>
            </div>
            <ResearchPicker />
            <ul className="hints">
              <li><b>Add a plan</b> (Sources, left): upload another utility&apos;s project list; its Reader joins every live run.</li>
              <li><b>Research</b>: before a live run, pick which other utilities the research team looks up near the river.</li>
              <li><b>Replay</b> plays back the newest recorded run and works offline.</li>
              <li><b>Template fallback</b> (top bar): if Gemini keeps failing during a live run, the text comes from a template instead of the agent failing.</li>
            </ul>
          </div>
        </div>
      )}
    </>
  );
}

function moveCamera(map: maplibregl.Map, c: ReturnType<typeof useUI.getState>["camera"]) {
  const opts = { padding: 40, duration: 1300, essential: false };
  if (c.kind === "us") map.fitBounds(US, opts);
  else if (c.kind === "border") map.fitBounds(BORDER, opts);
  else if (c.kind === "river") map.fitBounds(RIVER, { ...opts, duration: 1600 });
  else if (c.kind === "pair") {
    const a = run.projects[c.a], b = run.projects[c.b];
    if (!a?.lat || !b?.lat) return;
    const pad = 0.3 + Math.abs(a.lat! - b.lat!) * 0.3 + Math.abs(a.lon! - b.lon!) * 0.3;
    map.fitBounds([[Math.min(a.lon!, b.lon!) - pad, Math.min(a.lat!, b.lat!) - pad],
      [Math.max(a.lon!, b.lon!) + pad, Math.max(a.lat!, b.lat!) + pad]], { ...opts, maxZoom: 9.5 });
  } else if (c.kind === "point") {
    map.flyTo({ center: [c.lon, c.lat], zoom: Math.max(map.getZoom(), 8.5), duration: 1000 });
  } else if (c.kind === "project") {
    const p = run.projects[c.id];
    if (p?.lat != null) map.flyTo({ center: [p.lon!, p.lat!], zoom: Math.max(map.getZoom(), 8), duration: 1000 });
  }
}
