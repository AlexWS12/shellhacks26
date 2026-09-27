// The design tokens live in app/globals.css. MapLibre paints with literal colors, so this
// reads the same custom properties off :root instead of keeping a second copy in TypeScript.

const warned = new Set<string>();

export function token(name: `--${string}`): string {
  if (typeof document === "undefined") return "";
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!v && !warned.has(name)) {
    warned.add(name);
    console.warn(`design token ${name} is not defined in globals.css`);
  }
  return v || "#ff00ff"; // loud on purpose, so a missing token is caught in the demo
}

export function mapPalette() {
  return {
    zone: token("--zone"),
    zoneFar: token("--zone-far"),
    catElectric: token("--cat-electric"),
    catGas: token("--cat-gas"),
    catRoads: token("--cat-roads"),
    bg: token("--bg"),
    spark: token("--spark"),
    water: token("--map-water"),
    land: token("--map-land"),
    landFocus: token("--map-land-focus"),
    county: token("--map-county"),
    border: token("--map-border"),
  };
}
