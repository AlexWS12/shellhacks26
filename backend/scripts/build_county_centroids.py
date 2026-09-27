# Builds data/geo/county_centroids_sc_ga.json from the Census county outlines the frontend ships.
#   uv run python scripts/build_county_centroids.py
# Area-weighted centroid of each county's largest ring; the state comes from states_sc_ga.json.

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import GEO_DIR, REPO_DIR  # noqa: E402
from app.core.places import States  # noqa: E402

SRC = REPO_DIR / "frontend" / "public" / "geo" / "counties_sc_ga.json"


def centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    a /= 2
    if a == 0:
        return 0.0, 0.0, 0.0  # degenerate sliver (tiny islands after rounding)
    return cy / (6 * a), cx / (6 * a), abs(a)  # lat, lon, area


def main() -> None:
    states = States()
    rows = []
    for f in json.loads(SRC.read_text(encoding="utf-8"))["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        lat, lon, _ = max((centroid(p[0]) for p in polys), key=lambda c: c[2])
        state = states.state_of(lat, lon)
        if state:
            rows.append([f["properties"]["name"], state, round(lat, 5), round(lon, 5)])
    out = GEO_DIR / "county_centroids_sc_ga.json"
    out.write_text(json.dumps({"source": "US Census cartographic boundaries (us-atlas counties-10m, public domain); "
                               "area-weighted centroids by scripts/build_county_centroids.py",
                               "rows": sorted(rows, key=lambda r: (r[1], r[0]))}, indent=0), encoding="utf-8")
    print(f"{len(rows)} counties -> {out}")


if __name__ == "__main__":
    main()
