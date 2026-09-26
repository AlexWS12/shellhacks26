# Uses pdftotext if it's installed, otherwise the text cache (so Windows works without poppler).

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import CACHE_DIR

TEXT_CACHE = CACHE_DIR / "pdftext"


@dataclass
class PdfText:
    pages: list[str]  # index 0 = page 1
    method: str  # "pdftotext" | "cache"
    sha: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def read_pdf(path: Path) -> PdfText:
    sha = _sha(path)
    cached = TEXT_CACHE / f"{sha}.txt"
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout
        text, method = out.decode("utf-8", errors="replace"), "pdftotext"
        if not cached.exists():
            TEXT_CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
    elif cached.exists():
        text, method = cached.read_text(encoding="utf-8"), "cache"
    else:
        raise RuntimeError(f"pdftotext is not installed and there is no text cache for {path.name} ({cached})")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return PdfText(pages=pages, method=method, sha=sha)
