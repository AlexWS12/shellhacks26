// Builds the offline basemap layers from us-atlas (Census cartographic boundaries, public domain).
// Output: public/geo/states.json (all states), public/geo/counties_sc_ga.json (SC + GA counties),
// and ../data/geo/states_sc_ga.json (SC + GA outlines for the backend).
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { feature, mesh } from "topojson-client";

const require = createRequire(import.meta.url);
const states = JSON.parse(readFileSync(require.resolve("us-atlas/states-10m.json"), "utf8"));
const counties = JSON.parse(readFileSync(require.resolve("us-atlas/counties-10m.json"), "utf8"));

const round = (geom) => JSON.parse(JSON.stringify(geom, (k, v) => (typeof v === "number" ? Math.round(v * 1e4) / 1e4 : v)));
const FOCUS = { "13": "Georgia", "45": "South Carolina" };

const st = feature(states, states.objects.states);
st.features = st.features.map((f) => ({ type: "Feature", properties: { name: f.properties.name, focus: String(f.id) in FOCUS }, geometry: round(f.geometry) }));
writeFileSync("public/geo/states.json", JSON.stringify(st));
// the backend ranks location candidates by state with these two outlines
const ABBR = { Georgia: "GA", "South Carolina": "SC" };
writeFileSync("../data/geo/states_sc_ga.json", JSON.stringify({
  source: "us-atlas states-10m (US Census cartographic boundaries, public domain), via frontend/scripts/build-geo.mjs",
  type: "FeatureCollection",
  features: st.features.filter((f) => f.properties.name in ABBR).map((f) => ({ type: "Feature", properties: { state: ABBR[f.properties.name] }, geometry: f.geometry })),
}));

const borders = mesh(states, states.objects.states, (a, b) => a !== b);
writeFileSync("public/geo/state_borders.json", JSON.stringify({ type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: round(borders) }] }));

const ct = feature(counties, counties.objects.counties);
ct.features = ct.features
  .filter((f) => String(f.id).slice(0, 2) in FOCUS)
  .map((f) => ({ type: "Feature", properties: { name: f.properties.name }, geometry: round(f.geometry) }));
writeFileSync("public/geo/counties_sc_ga.json", JSON.stringify(ct));
console.log("states", st.features.length, "counties", ct.features.length);
