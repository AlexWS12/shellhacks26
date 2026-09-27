# Writes data/geo/state_bounds.json: a bounding box per US state (USPS code), from the Census state outlines the
# frontend already ships (frontend/public/geo/states.json, from us-atlas). The OSM search area is the union of the
# boxes of every active source's states.
#   uv run python scripts/build_state_bounds.py

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import GEO_DIR, REPO_DIR  # noqa: E402

USPS = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
        "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
        "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY",
        "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
        "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
        "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
        "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
        "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
        "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
        "Puerto Rico": "PR"}


def main() -> None:
    src = REPO_DIR / "frontend" / "public" / "geo" / "states.json"
    out = {}
    for f in json.loads(src.read_text(encoding="utf-8"))["features"]:
        code = USPS.get(f["properties"]["name"])
        if not code:
            continue
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        pts = [p for poly in polys for ring in poly for p in ring]
        out[code] = [round(min(y for _, y in pts), 4), round(min(x for x, _ in pts), 4),
                     round(max(y for _, y in pts), 4), round(max(x for x, _ in pts), 4)]
    (GEO_DIR / "state_bounds.json").write_text(json.dumps(
        {"source": "Census state outlines via us-atlas (frontend/public/geo/states.json)",
         "order": "south, west, north, east", "states": dict(sorted(out.items()))}, indent=0), encoding="utf-8")
    print(f"{len(out)} states")


if __name__ == "__main__":
    main()
