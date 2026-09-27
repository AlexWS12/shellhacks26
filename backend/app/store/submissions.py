# Plans people submit in the Sources menu. Saved until removed: data/submissions/<id>.json plus the raw file.
# Each ready submission becomes its own Reader agent on every live run.

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app import config

MAX_BYTES = 20 * 1024 * 1024
MAX_SUBMISSIONS = 12
Kind = Literal["spreadsheet", "pdf", "url"]
# Legacy spellings of the built-in owners; submitting them again would pair a utility with itself.
BUILT_IN = re.compile(r"\b(georgia power|dominion|sce ?& ?g|scana|south carolina electric (and|&) gas)\b|^(desc|gpc)$")


class Submission(BaseModel):
    id: str  # 'santee-cooper-3f2a'; the Reader is 'extract_<id>'
    owner: str  # 'Santee Cooper'
    label: str  # short name for the Reader agent
    owner_key: str  # Project.utility for its projects; plans from the same owner share it
    state: Literal["SC", "GA"]  # for rows that don't say
    kind: Kind  # how it is read: spreadsheet (code) or pdf / url text (Gemini)
    filename: str  # original file name, or the link's last path segment
    url: str | None = None
    stored: str  # file name under data/submissions
    content_type: str = ""
    sha256: str
    size: int
    created: str
    columns: list[str] = Field(default_factory=list)
    mapping: dict[str, str] = Field(default_factory=dict)  # field -> column (spreadsheets)
    status: Literal["needs_mapping", "ready"] = "ready"


def _dir() -> Path:
    return config.SUBMISSIONS_DIR


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def list_all() -> list[Submission]:
    if not _dir().exists():
        return []
    out = []
    for p in _dir().glob("*.json"):
        try:
            out.append(Submission(**json.loads(p.read_text(encoding="utf-8"))))
        except (ValueError, OSError):
            continue  # a broken record is skipped, not fatal
    return sorted(out, key=lambda s: s.created)


def get(sid: str) -> Submission | None:
    if not re.fullmatch(r"[a-z0-9-]{1,64}", sid):
        return None
    p = _dir() / f"{sid}.json"
    return Submission(**json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None


def file_of(s: Submission) -> Path:
    return _dir() / s.stored


def _write(s: Submission) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    (_dir() / f"{s.id}.json").write_text(s.model_dump_json(indent=1), encoding="utf-8")


def check_owner(owner: str) -> str:
    owner = " ".join(owner.split())
    if not owner or len(owner) > 80:
        raise ValueError("Give the utility's name (up to 80 characters).")
    from app.store import sources  # built-in sources, by name and code; BUILT_IN keeps their legacy spellings

    low = owner.lower().strip()
    builtin = [x for x in sources.all_sources() if x.builtin]
    if (BUILT_IN.search(re.sub(r"[^a-z&\s]", " ", low).strip()) or BUILT_IN.search(low)
            or any(low in (x.display_name.lower(), x.code.lower()) for x in builtin)):
        names = " and ".join(x.display.get("ui_name", x.display_name) for x in builtin) or "The built-in utilities"
        raise ValueError(f"{names} are already built in. Submit another utility's plan.")
    return owner


def create(owner: str, label: str | None, state: str, kind: str, filename: str, data: bytes, ext: str,
           url: str | None = None, content_type: str = "", columns: list[str] | None = None) -> Submission:
    owner = check_owner(owner)
    if len(list_all()) >= MAX_SUBMISSIONS:
        raise ValueError(f"Up to {MAX_SUBMISSIONS} saved plans. Remove one first.")
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"Files up to {MAX_BYTES // (1024 * 1024)} MB.")
    label = " ".join((label or owner).split())[:40]
    sid = f"{slug(label)[:24] or 'plan'}-{uuid.uuid4().hex[:4]}"
    stored = f"{sid}{ext}"  # never the user's file name
    _dir().mkdir(parents=True, exist_ok=True)
    (_dir() / stored).write_bytes(data)
    s = Submission(id=sid, owner=owner, label=label, owner_key=f"own-{slug(owner)[:40]}", state=state, kind=kind,
                   filename=Path(filename).name[:120] or stored, url=url, stored=stored, content_type=content_type,
                   sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                   created=time.strftime("%Y-%m-%dT%H:%M:%S"), columns=columns or [],
                   status="needs_mapping" if kind == "spreadsheet" else "ready")
    _write(s)
    return s


def set_mapping(sid: str, mapping: dict[str, str]) -> Submission:
    s = get(sid)
    if not s:
        raise KeyError(sid)
    s.mapping, s.status = mapping, "ready"
    _write(s)
    return s


def delete(sid: str) -> bool:
    s = get(sid)
    if not s:
        return False
    file_of(s).unlink(missing_ok=True)
    (_dir() / f"{s.id}.json").unlink(missing_ok=True)
    return True
