# Utilities as data: one row per source (an owner's filing), in SQLite at config.SOURCES_DB.
# Dominion (DESC) and Georgia Power (GPC) are seeded as built-in sources read by their own parsers. Every plan saved
# in the Sources menu gets a row too (reader 'sheet' for a column-matched spreadsheet, 'ai' for a PDF or web page),
# kept in step with data/submissions on every read.
#
# code is the owner: two filings of one owner share a code, and overlaps are only computed between different codes.
# utility_key is the Project.utility value its projects carry ('DESC', 'GA', 'own-<owner>'), so project ids, the
# benchmark and recordings made before this table existed keep working.

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app import config
from app.core.palette import next_color

Status = Literal["draft", "extracting", "review", "active", "failed"]
Reader = Literal["builtin:desc", "builtin:gpc", "sheet", "ai"]
STATUSES = ("draft", "extracting", "review", "active", "failed")
ORIGIN = "sources_menu"  # display.origin of the rows kept in step with data/submissions

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  display_name TEXT NOT NULL,
  states TEXT NOT NULL,                  -- JSON list of USPS codes
  osm_operator_patterns TEXT NOT NULL,   -- JSON list, lowercase substrings of an OSM operator tag
  color TEXT NOT NULL,
  reader TEXT NOT NULL,
  file_path TEXT,                        -- relative to the data directory
  file_sha256 TEXT,
  status TEXT NOT NULL CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
  created_at TEXT NOT NULL,
  utility_key TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 100, -- built-ins first, in seed order
  sponsors TEXT NOT NULL DEFAULT '[]',   -- JSON [{{code, name, default}}]: owners inside one filing, and which show by default
  display TEXT NOT NULL DEFAULT '{{}}'   -- JSON: short_name, ui_name, card_label, card_detail, expected_total, shape,
                                         --       date_label, costs, hq
)"""


class Source(BaseModel):
    id: str
    code: str
    display_name: str
    states: list[str]
    osm_operator_patterns: list[str] = Field(default_factory=list)
    color: str
    reader: Reader
    file_path: str | None = None
    file_sha256: str | None = None
    status: Status
    created_at: str
    utility_key: str
    position: int = 100
    sponsors: list[dict[str, Any]] = Field(default_factory=list)
    display: dict[str, Any] = Field(default_factory=dict)

    @property
    def builtin(self) -> bool:
        return self.reader.startswith("builtin:")

    @property
    def default_sponsors(self) -> set[str]:
        return {s["code"] for s in self.sponsors if s.get("default")}


def _rel(p: Path) -> str:
    return str(p.relative_to(config.DATA_DIR)) if p.is_relative_to(config.DATA_DIR) else str(p)


def _builtins() -> list[Source]:
    # The values the code used to hardcode, as data.
    now = "2026-09-26T00:00:00"
    return [
        Source(id="desc", code="DESC", display_name="Dominion Energy South Carolina", states=["SC"],
               # SCE&G and South Carolina Electric are Dominion's legacy names in OpenStreetMap operator tags
               osm_operator_patterns=["dominion", "sce&g", "south carolina electric", "santee cooper"],
               color="#2dd4bf", reader="builtin:desc", file_path=_rel(config.DESC_PDF), status="active",
               created_at=now, utility_key="DESC", position=0,
               sponsors=[{"code": "DESC", "name": "Dominion Energy South Carolina", "default": True}],
               display={"short_name": "Dominion", "ui_name": "Dominion Energy SC", "legend": "Dominion Energy SC",
                        "card_label": "Dominion Energy SC",
                        "card_detail": "Planned transmission projects $2M+, 2024–2028", "expected_total": 44,
                        "shape": "circle", "date_label": "In service", "costs": "public", "cite": "page",
                        "hq": {"lon": -81.074, "lat": 33.966, "label": "Dominion HQ, Cayce SC"}}),
        Source(id="gpc", code="GPC", display_name="Georgia Power", states=["GA"],
               osm_operator_patterns=["georgia power", "southern company", "georgia transmission", "meag"],
               color="#6ea8fe", reader="builtin:gpc", file_path=_rel(config.GA_PDF), status="active",
               created_at=now, utility_key="GA", position=1,
               sponsors=[{"code": "GPC", "name": "Georgia Power", "default": True},
                         {"code": "SAV", "name": "Georgia Power (Savannah)", "default": True},
                         {"code": "GTC", "name": "Georgia Transmission", "default": False},
                         {"code": "MEAG", "name": "MEAG Power", "default": False},
                         {"code": "DU", "name": "Dalton Utilities", "default": False}],
               display={"short_name": "Georgia", "ui_name": "Georgia Power", "legend": "Georgia",
                        "card_label": "Georgia Power IRP, Vol. 3",
                        "card_detail": "2024 GA ITS Ten-Year Plan, 2025–2034", "expected_total": 208,
                        "shape": "diamond", "date_label": "Needed by", "costs": "redacted", "cite": "page",
                        "show_status": False,
                        "hq": {"lon": -84.389, "lat": 33.759, "label": "Georgia Power HQ, Atlanta"}}),
    ]


_COLS = ["id", "code", "display_name", "states", "osm_operator_patterns", "color", "reader", "file_path", "file_sha256",
         "status", "created_at", "utility_key", "position", "sponsors", "display"]
_JSON = {"states", "osm_operator_patterns", "sponsors", "display"}
_ready: set[str] = set()
_generation = 0
_cache: tuple[tuple, list[Source]] | None = None


def _connect() -> sqlite3.Connection:
    path = Path(config.SOURCES_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if str(path) not in _ready:
        with con:
            con.execute(SCHEMA)
            for s in _builtins():  # seeded once; after that the table is the source of truth
                if con.execute("SELECT 1 FROM sources WHERE id = ?", (s.id,)).fetchone() is None:
                    s.file_sha256 = _sha256(config.DATA_DIR / s.file_path) if s.file_path else None
                    _insert(con, s)
        _ready.add(str(path))
    return con


def _sha256(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _insert(con: sqlite3.Connection, s: Source) -> None:
    d = s.model_dump()
    con.execute(f"INSERT OR REPLACE INTO sources ({', '.join(_COLS)}) VALUES ({', '.join('?' * len(_COLS))})",
                [json.dumps(d[c]) if c in _JSON else d[c] for c in _COLS])


def _row(r: sqlite3.Row | tuple) -> Source:
    d = dict(zip(_COLS, r))
    for c in _JSON:
        d[c] = json.loads(d[c])
    return Source(**d)


def _read() -> list[Source]:
    with closing(_connect()) as con:
        rows = con.execute(f"SELECT {', '.join(_COLS)} FROM sources").fetchall()
    return sorted((_row(r) for r in rows), key=lambda s: (s.position, s.code, s.created_at, s.id))


def _touch() -> None:
    global _generation, _cache
    _generation += 1
    _cache = None


def _submissions_stamp() -> tuple:
    d = Path(config.SUBMISSIONS_DIR)
    if not d.exists():
        return ()
    return tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(d) if e.name.endswith(".json")))


def owner_code(owner: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", owner.upper()).strip("-")[:24] or "PLAN"


def _sync_submissions(rows: list[Source]) -> bool:
    # One row per saved plan: draft until its columns are matched, then active. Returns whether anything changed.
    from app.store import submissions  # the plan store doesn't know about this table

    subs = {s.id: s for s in submissions.list_all()}
    mine = {r.id: r for r in rows if r.display.get("origin") == ORIGIN}  # only the rows this sync made
    changed = False
    with closing(_connect()) as con, con:
        for sid in set(mine) - set(subs):
            con.execute("DELETE FROM sources WHERE id = ?", (sid,))
            changed = True
        taken = [r.color for r in rows if r.id not in set(mine) - set(subs)]
        for sid, s in subs.items():
            status = "active" if s.status == "ready" else "draft"
            old = mine.get(sid)
            if old is not None:
                if old.status != status:
                    con.execute("UPDATE sources SET status = ? WHERE id = ?", (status, sid))
                    changed = True
                continue
            code = owner_code(s.owner)
            same_owner = next((r for r in rows if r.code == code), None)  # one owner keeps one color
            color = same_owner.color if same_owner else next_color(taken)
            taken.append(color)
            _insert(con, Source(
                id=sid, code=code, display_name=s.owner, states=[s.state], color=color,
                reader="sheet" if s.kind == "spreadsheet" else "ai", file_path=_rel(submissions.file_of(s)),
                file_sha256=s.sha256, status=status, created_at=s.created, utility_key=s.owner_key,
                display={"origin": ORIGIN, "short_name": s.owner, "ui_name": s.owner, "card_label": s.owner,
                         "card_detail": f"Submitted {s.kind}: {s.url or s.filename}", "shape": "circle",
                         "date_label": "In service", "costs": "stated", "cite": "row" if s.kind == "spreadsheet" else "page"}))
            changed = True
    return changed


def all_sources() -> list[Source]:
    # Every source, built-ins first. Cached until the table or the saved plans change.
    global _cache
    key = (str(config.SOURCES_DB), str(config.SUBMISSIONS_DIR), _generation, _submissions_stamp())
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    rows = _read()
    if _sync_submissions(rows):
        rows = _read()
    _cache = (key, rows)
    return rows


def active() -> list[Source]:
    return [s for s in all_sources() if s.status == "active"]


def get(source_id: str) -> Source | None:
    return next((s for s in all_sources() if s.id == source_id), None)


def add(code: str, display_name: str, states: list[str], reader: Reader = "ai", *, source_id: str | None = None,
        utility_key: str | None = None, status: Status = "draft", osm_operator_patterns: list[str] | None = None,
        sponsors: list[dict[str, Any]] | None = None, display: dict[str, Any] | None = None,
        file_path: str | None = None, color: str | None = None) -> Source:
    # A new source. Its color is generated unless given: readable on the dark UI and distinct from every other.
    code = owner_code(code)
    rows = all_sources()
    same_owner = next((r for r in rows if r.code == code), None)
    s = Source(id=source_id or f"{code.lower()}-{int(time.time() * 1000) % 100000:05d}", code=code,
               display_name=display_name, states=[x.upper() for x in states],
               osm_operator_patterns=[p.lower() for p in osm_operator_patterns or []],
               color=color or (same_owner.color if same_owner else next_color([r.color for r in rows])),
               reader=reader, file_path=file_path,
               file_sha256=_sha256(config.DATA_DIR / file_path) if file_path else None, status=status,
               created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), utility_key=utility_key or f"src-{code.lower()}",
               sponsors=sponsors or [], display={"short_name": display_name, "ui_name": display_name,
                                                 "card_label": display_name, "shape": "circle",
                                                 "date_label": "In service", "costs": "stated", **(display or {})})
    with closing(_connect()) as con, con:
        _insert(con, s)
    _touch()
    return s


def set_status(source_id: str, status: Status) -> None:
    with closing(_connect()) as con, con:
        con.execute("UPDATE sources SET status = ? WHERE id = ?", (status, source_id))
    _touch()


def remove(source_id: str) -> None:
    with closing(_connect()) as con, con:
        con.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    _touch()


BUILTIN_AGENT = {"builtin:desc": "extract_desc", "builtin:gpc": "extract_ga"}


def agent_id(s: Source) -> str:
    # The Reader agent that reads this source (the pipeline builds it from s.reader).
    return BUILTIN_AGENT.get(s.reader, f"extract_{s.id}")


def public(s: Source) -> dict[str, Any]:
    # What GET /api/sources shows. No keys or file contents, only where the file sits.
    return {**s.model_dump(), "builtin": s.builtin, "agent_id": agent_id(s)}


# ---- drafts: an AI reader's output for a source, waiting for review (the source stays out of runs until then)

def draft_path(source_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,64}", source_id):
        raise ValueError(f"bad source id {source_id!r}")
    return Path(config.DRAFTS_DIR) / source_id / "draft.json"


def save_draft(source_id: str, doc: dict[str, Any]) -> Path:
    p = draft_path(source_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def load_draft(source_id: str) -> dict[str, Any] | None:
    p = draft_path(source_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ---- a reviewed source's projects: what its Reader loads on every run once it's active

def published_path(source_id: str) -> Path:
    return draft_path(source_id).with_name("projects.json")


def save_published(source_id: str, projects: list[dict[str, Any]]) -> None:
    p = published_path(source_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(projects, indent=1, default=str), encoding="utf-8")


def load_published(source_id: str) -> list[dict[str, Any]]:
    p = published_path(source_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
