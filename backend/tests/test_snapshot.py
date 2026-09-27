# The Dominion-Georgia overlap output, recorded before utilities became data (tests/fixtures/overlaps_snapshot.json),
# must come out identical: the pipeline's overlaps, every filter view, each project's location, and the
# Sperry-schema columns of the export. Re-record only for an intended change: tests/fixtures/capture_overlaps.py.

import json
from pathlib import Path

from tests.fixtures.capture_overlaps import OUT, capture


def test_dominion_georgia_output_is_unchanged():
    want = json.loads(Path(OUT).read_text(encoding="utf-8"))
    got = json.loads(json.dumps(capture(), default=str))
    for part in ("pipeline", "views", "projects", "export_projects", "export_overlaps"):
        assert got[part] == want[part], part
