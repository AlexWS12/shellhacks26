# Run once with internet, then commit data/cache/osm.
#   uv run python scripts/fetch_osm.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients import overpass  # noqa: E402

if __name__ == "__main__":
    features = overpass.fetch()
    ops = {}
    for f in features:
        ops[f["operator"] or "(no operator tag)"] = ops.get(f["operator"] or "(no operator tag)", 0) + 1
    print(f"{len(features)} named substations -> {overpass.OSM_FILE}")
    for op, n in sorted(ops.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {n:>5}  {op}")
