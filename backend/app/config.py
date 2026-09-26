import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / ".env")  # a .env in the repo root works too

REPO_DIR = Path(os.getenv("TANDEM_REPO_DIR", BACKEND_DIR.parent))
DATA_DIR = Path(os.getenv("TANDEM_DATA_DIR", REPO_DIR / "data"))
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
RUNS_DIR = DATA_DIR / "runs"
GEO_DIR = DATA_DIR / "geo"
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"

DESC_PDF = RAW_DIR / "2024-2028-2million-and-above-project-descriptions.pdf"
GA_PDF = RAW_DIR / "2025_IRP_Volume_3_PUBLIC_DISCLOSURE.pdf"
SAMPLE_XLSX = RAW_DIR / "Projects_Overlaps.xlsx"
OVERRIDES_CSV = DATA_DIR / "overrides" / "locations.csv"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# LLMs
GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.8-flash")
JEV_PROVIDER = env("JEV_PROVIDER")  # typesafe | openrouter | cloudflare | mock | "" (off)

# Geocoding
OSM_LIVE = env("OSM_LIVE", "true").lower() == "true"  # fetch OSM live when there is no cache yet
NOMINATIM_USER_AGENT = env("NOMINATIM_USER_AGENT", "tandem-shellhacks/0.1")

# Storage (Tiger Data = managed Postgres + TimescaleDB). Empty = no database, files only.
DATABASE_URL = env("DATABASE_URL")

# Run pacing: 1.0 = demo speed, 0 = as fast as possible
PACE = env_float("TANDEM_PACE", 1.0)

# "Today" for the hide-finished filter and the past-date check. Fixed so runs are reproducible.
TODAY = env("TANDEM_TODAY", "2026-09-26")

# Cost model. No savings figure is shown unless a share AND its source are both set.
COST_MOBILIZATION_SHARE = env_float("COST_MOBILIZATION_SHARE", 0.0)
COST_SOURCE = env("COST_SOURCE")

CORS_ORIGINS = [o for o in env("CORS_ORIGINS", "http://localhost:3000").split(",") if o]
