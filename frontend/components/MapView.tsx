"use client";

// Streets basemap online (OpenFreeMap dark, no key), bundled basemap offline.

import maplibregl, { type GeoJSONSource, type LngLatBoundsLike, type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef } from "react";

import { activeIn, visible } from "@/lib/filters";
import { engineColor } from "@/lib/format";
import { run, useRev } from "@/lib/run";
import type { Overlap, Project } from "@/lib/types";
import { showLatestResults, startRun, useUI } from "@/lib/ui";

const STREETS = "https://tiles.openfreemap.org/styles/dark";
const US: LngLatBoundsLike = [[-125, 24.3], [-66.5, 49.5]];
const BORDER: LngLatBoundsLike = [[-85.7, 30.3], [-78.4, 35.3]];
const RING_MI = 25;
const PIN_DROP_MS = 800;
const YEARS = { min: 2023, max: 2034 };

const COLORS = { desc: "#2dd4bf", gpc: "#6ea8fe", zone: "#f5b841", ink: "#e7eaf0", bg: "#07090d" };

type FC = GeoJSON.FeatureCollection;
const fc = (features: GeoJSON.Feature[]): FC => ({ type: "FeatureCollection", features });

function simpleStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      states: { type: "geojson", data: "/geo/states.json" },
      counties: { type: "geojson", data: "/geo/counties_sc_ga.json" },
      borders: { type: "geojson", data: "/geo/state_borders.json" },
    },
    layers: [
      { id: "water", type: "background", paint: { "background-color": "#05070a" } },
      { id: "land", type: "fill", source: "states", paint: { "fill-color": ["case", ["get", "focus"], "#111723", "#0b0f16"] } },
      { id: "counties", type: "line", source: "counties", paint: { "line-color": "#1a2130", "line-width": 0.5 } },
      { id: "borders", type: "line", source: "borders", paint: { "line-color": "#2c3548", "line-width": 1 } },
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

function selectedPair(): { a: string; b: string } | null {
  const p = useUI.getState().panel;
  return p.kind === "pair" ? { a: p.a, b: p.b } : null;
}

function buildData() {
  const { filters, health, year } = useUI.getState();
  const today = health?.today ?? "2026-09-26";
  const shown = Object.values(run.projects).filter((p) => visible(p, filters, today) && activeIn(p, year));
  const ids = new Set(shown.map((p) => p.id));
  const hollow = (p: Project) => !["verified", "confirmed_osm"].includes(p.location_confidence);
  const points = fc(shown.map((p) => ({
    type: "Feature",
    properties: { id: p.id, u: p.utility, hollow: hollow(p), name: p.name },
    geometry: { type: "Point", coordinates: [p.lon!, p.lat!] },
  })));
  const lines = fc(shown.flatMap((p) => {
    const eps = p.endpoints.filter((e) => e.lat != null && e.lon != null);
    if (eps.length !== 2) return [];
    return [{ type: "Feature", properties: { id: p.id, u: p.utility, hollow: hollow(p) },
      geometry: { type: "LineString", coordinates: eps.map((e) => [e.lon!, e.lat!]) } } as GeoJSON.Feature];
  }));
  const sel = selectedPair();
  const pairs = currentOverlaps().filter((o) => ids.has(o.project_a) && ids.has(o.project_b));
  const links: GeoJSON.Feature[] = [];
  const labels: GeoJSON.Feature[] = [];
  for (const o of pairs) {
    const a = run.projects[o.project_a], b = run.projects[o.project_b];
    const isSel = sel ? sel.a === o.project_a && sel.b === o.project_b : false;
    const path = arc([a.lon!, a.lat!], [b.lon!, b.lat!]);
    const props = { a: o.project_a, b: o.project_b, sel: isSel, dim: Boolean(sel) && !isSel, together: o.windows_overlap === true };
    links.push({ type: "Feature", properties: props, geometry: { type: "LineString", coordinates: path } });
    labels.push({ type: "Feature", properties: { ...props, label: `${o.distance_mi.toFixed(1)} mi` },
      geometry: { type: "Point", coordinates: path[12] } });
  }
  const ring = fc(sel && run.projects[sel.a]?.lat != null ? [circle(run.projects[sel.a].lon!, run.projects[sel.a].lat!, RING_MI)] : []);
  return { points, lines, links: fc(links), labels: fc(labels), ring };
}

function addDataLayers(map: maplibregl.Map) {
  const color = ["match", ["get", "u"], "DESC", COLORS.desc, COLORS.gpc] as maplibregl.ExpressionSpecification;
  for (const id of ["ring", "lines", "links", "labels", "points", "pulse"]) {
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
    paint: { "line-color": ["case", ["get", "together"], COLORS.zone, "#c9a45c"], "line-width": ["case", ["get", "sel"], 2.6, 1.5],
      "line-opacity": linkOpacity }, layout: { "line-cap": "round" } });
  map.addLayer({ id: "links-hit", type: "line", source: "links", paint: { "line-color": "#000", "line-opacity": 0, "line-width": 14 } });
  map.addLayer({ id: "pulse", type: "circle", source: "pulse",
    paint: { "circle-radius": ["get", "r"], "circle-color": color, "circle-opacity": 0, "circle-stroke-color": color,
      "circle-stroke-width": 2, "circle-stroke-opacity": ["get", "o"] } });
  map.addLayer({ id: "points-glow", type: "circle", source: "points",
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 7, 9, 13], "circle-color": color, "circle-blur": 1, "circle-opacity": 0.35 } });
  map.addLayer({ id: "points", type: "circle", source: "points",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 3, 9, 6],
      "circle-color": ["case", ["get", "hollow"], COLORS.bg, color],
      "circle-stroke-color": color,
      "circle-stroke-width": ["case", ["get", "hollow"], 1.6, 0],
    } });
  map.addLayer({ id: "link-labels", type: "symbol", source: "labels",
    filter: ["any", ["get", "sel"], [">=", ["zoom"], 6.3]],
    layout: { "text-field": ["get", "label"], "text-size": ["case", ["get", "sel"], 14, 12], "text-font": ["Noto Sans Bold"],
      "text-allow-overlap": false, "text-padding": 4 },
    paint: { "text-color": COLORS.zone, "text-halo-color": COLORS.bg, "text-halo-width": 2.5, "text-opacity": ["case", ["get", "dim"], 0.2, 1] } });
}

// One labeled beacon per working agent, gliding to whatever it's working on.
class AgentMarkers {
  private markers = new Map<string, { m: maplibregl.Marker; cur: [number, number]; label: HTMLElement }>();
  constructor(private map: maplibregl.Map) {}

  sync() {
    const live = new Set<string>();
    for (const [id, pos] of Object.entries(run.agentPos)) {
      const agent = run.agents[id];
      if (!agent || agent.status !== "working") continue;
      live.add(id);
      let entry = this.markers.get(id);
      if (!entry) {
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
        entry = { m, cur: [pos.lon, pos.lat], label: em };
        this.markers.set(id, entry);
      }
      entry.label.textContent = pos.label.length > 28 ? `${pos.label.slice(0, 26)}…` : pos.label;
    }
    for (const [id, entry] of this.markers) {
      if (!live.has(id)) {
        entry.m.remove();
        this.markers.delete(id);
      }
    }
  }

  step(): boolean {
    let moving = false;
    for (const [id, entry] of this.markers) {
      const target = run.agentPos[id];
      if (!target) continue;
      const dx = target.lon - entry.cur[0], dy = target.lat - entry.cur[1];
      if (Math.abs(dx) + Math.abs(dy) < 0.0005) continue;
      entry.cur = [entry.cur[0] + dx * 0.18, entry.cur[1] + dy * 0.18];
      entry.m.setLngLat(entry.cur);
      moving = true;
    }
    return moving;
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
    map.on("mouseenter", "points", (e) => {
      map.getCanvas().style.cursor = "pointer";
      const f = e.features?.[0];
      if (f) popup.setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number]).setText(String(f.properties.name)).addTo(map);
    });
    map.on("mouseleave", "points", () => { map.getCanvas().style.cursor = ""; popup.remove(); });
    map.on("mouseenter", "links-hit", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "links-hit", () => (map.getCanvas().style.cursor = ""));
    map.on("click", "points", (e) => {
      const id = e.features?.[0]?.properties.id;
      if (id) useUI.getState().setPanel({ kind: "project", id: String(id) });
    });
    map.on("click", "links-hit", (e) => {
      const p = e.features?.[0]?.properties;
      if (p) {
        useUI.getState().setPanel({ kind: "pair", a: String(p.a), b: String(p.b) });
        useUI.getState().flyTo({ kind: "pair", a: String(p.a), b: String(p.b) });
      }
    });

    function refresh() {
      if (!map.isStyleLoaded() || !map.getSource("points")) return;
      const d = buildData();
      (map.getSource("points") as GeoJSONSource).setData(d.points);
      (map.getSource("lines") as GeoJSONSource).setData(d.lines);
      (map.getSource("links") as GeoJSONSource).setData(d.links);
      (map.getSource("labels") as GeoJSONSource).setData(d.labels);
      (map.getSource("ring") as GeoJSONSource).setData(d.ring);
      agents.sync();
      animate();
    }

    let raf = 0;
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
          return [{ type: "Feature", properties: { u: p.utility, r: 3 + 18 * k, o: 0.9 * (1 - k) },
            geometry: { type: "Point", coordinates: [p.lon!, p.lat!] } } as GeoJSON.Feature];
        })));
        const moving = agents.step();
        raf = drops.length || moving ? requestAnimationFrame(tick) : 0;
      };
      raf = requestAnimationFrame(tick);
    }

    const unsubRev = useRev.subscribe(refresh);
    const unsubUI = useUI.subscribe((s, prev) => {
      if (s.results !== prev.results || s.filters !== prev.filters || s.panel !== prev.panel || s.health !== prev.health || s.year !== prev.year) refresh();
      if (s.camera !== prev.camera) moveCamera(map, s.camera);
    });
    return () => {
      unsubRev();
      unsubUI();
      cancelAnimationFrame(raf);
      clearTimeout(fallback);
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
        <button onClick={() => useUI.getState().setBasemap(basemap === "streets" ? "simple" : "streets")}
          title="Streets needs internet; Simple works offline">
          {basemap === "streets" ? "Simple" : "Streets"}
        </button>
      </div>
      <div className="legend" aria-label="Legend">
        <span><i className="sw" style={{ background: COLORS.desc }} />Dominion Energy SC</span>
        <span><i className="sw" style={{ background: COLORS.gpc }} />Georgia</span>
        <span><i className="sw" style={{ border: `1.5px solid ${COLORS.ink}` }} />Hollow = approximate location</span>
        <span><i className="sw" style={{ background: COLORS.zone }} />Under 25 mi apart</span>
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
            <h2>Two utilities. One river. Separate plans.</h2>
            <p>
              A team of AI agents reads Dominion Energy South Carolina&apos;s and Georgia Power&apos;s public construction
              plans, places every project on the map, and finds where they could build once instead of twice.
            </p>
            <div className="row2">
              <button className="primary" onClick={() => void startRun("live")}>Run pipeline</button>
              <button onClick={() => void startRun("replay")}>Replay a run</button>
              <button onClick={() => void showLatestResults()}>Jump to results</button>
            </div>
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
  else if (c.kind === "pair") {
    const a = run.projects[c.a], b = run.projects[c.b];
    if (!a?.lat || !b?.lat) return;
    const pad = 0.3 + Math.abs(a.lat! - b.lat!) * 0.3 + Math.abs(a.lon! - b.lon!) * 0.3;
    map.fitBounds([[Math.min(a.lon!, b.lon!) - pad, Math.min(a.lat!, b.lat!) - pad],
      [Math.max(a.lon!, b.lon!) + pad, Math.max(a.lat!, b.lat!) + pad]], { ...opts, maxZoom: 9.5 });
  } else if (c.kind === "project") {
    const p = run.projects[c.id];
    if (p?.lat != null) map.flyTo({ center: [p.lon!, p.lat!], zoom: Math.max(map.getZoom(), 8), duration: 1000 });
  }
}
