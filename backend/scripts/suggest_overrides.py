# Suggests rows for data/overrides/locations.csv from Nominatim, for a person to check.
# Nothing is written: copy a row only after confirming it, and fill source_note (rows without one are ignored).
#   uv run python scripts/suggest_overrides.py [--run RUN_ID]
#
# Endpoints come from the project.unlocated events of a recorded run (default: the newest complete one).
# Nominatim usage policy: 1 request/s, a real User-Agent (NOMINATIM_USER_AGENT). Answers are cached.

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CACHE_DIR, NOMINATIM_USER_AGENT, RUNS_DIR  # noqa: E402
from app.runtime.replay import recorded_runs  # noqa: E402

URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DIR = CACHE_DIR / "osm" / "nominatim"
STATES = {  # left, top, right, bottom (Nominatim viewbox order)
    "GA": ("Georgia", (-85.61, 35.00, -80.84, 30.36)),
    "SC": ("South Carolina", (-83.36, 35.22, -78.54, 32.03)),
}
_last_call = 0.0


def search(name: str, state: str) -> list[dict]:
    global _last_call
    full, box = STATES[state]
    params = {"q": name, "format": "jsonv2", "countrycodes": "us", "addressdetails": 1, "limit": 5,
              "viewbox": ",".join(map(str, box)), "bounded": 1}
    path = NOMINATIM_DIR / f"{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["results"]
    time.sleep(max(0.0, _last_call + 1.1 - time.monotonic()))
    r = httpx.get(URL, params=params, headers={"User-Agent": NOMINATIM_USER_AGENT}, timeout=30)
    _last_call = time.monotonic()
    r.raise_for_status()
    results = [x for x in r.json() if x.get("address", {}).get("state") == full]  # the box overlaps neighbors
    NOMINATIM_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"params": params, "source": URL, "license": "ODbL, (c) OpenStreetMap contributors",
                                "results": results}, indent=0), encoding="utf-8")
    return results


def unlocated(run_id: str) -> dict[tuple[str, str], list[dict]]:
    # (endpoint, state) -> projects that name it
    projects, out = {}, {}
    for line in (RUNS_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["type"] == "project.extracted":
            projects[e["project"]["id"]] = e["project"]
        elif e["type"] == "project.unlocated":
            p = projects[e["project_id"]]
            state = "SC" if p["utility"] == "DESC" else "GA"
            for ep in e["endpoints"]:
                if ep.get("lat") is None:
                    out.setdefault((ep["name"], state), []).append(p)
    return out


def main(run_id: str | None) -> int:
    if "your-team-email" in NOMINATIM_USER_AGENT:
        print("Set NOMINATIM_USER_AGENT in backend/.env to a real contact first.", file=sys.stderr)
        return 1
    if not run_id:
        done = [r for r in recorded_runs() if r["complete"]]
        if not done:
            print("No complete run in data/runs.", file=sys.stderr)
            return 1
        run_id = done[0]["run_id"]
    todo = unlocated(run_id)
    print(f"# {len(todo)} unlocated endpoints from {run_id}. Check each candidate, then copy the row into")
    print("# data/overrides/locations.csv and fill source_note (e.g. the OSM link and why it is the right place).")
    print("endpoint,state,lat,lon,source_note")
    for (name, state), projects in sorted(todo.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"\n# {name} ({state}), named by {len(projects)} project(s):")
        for p in projects:
            print(f"#   {p['id']}: {p['name']} | {(p.get('description') or '')[:160]!r}")
        results = search(name, state)
        if not results:
            print("#   no Nominatim result")
        for x in results:
            print(f"#   {x['category']}/{x['type']}: {x['display_name']}  "
                  f"https://www.openstreetmap.org/{x['osm_type']}/{x['osm_id']}")
            print(f"{name},{state},{float(x['lat']):.6f},{float(x['lon']):.6f},")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="run id in data/runs (default: newest complete run)")
    sys.exit(main(ap.parse_args().run))
