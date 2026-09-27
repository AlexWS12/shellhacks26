import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
# The first file to set a variable wins, and the process environment beats all of them.
# .env.local (gitignored) holds keys and settings for this machine only.
ENV_LOCAL_FILE = BACKEND_DIR / ".env.local"  # the setup screen writes keys here (local mode only)
ENV_FILES = (ENV_LOCAL_FILE, BACKEND_DIR.parent / ".env.local", BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env")
_FROM_SHELL = set(os.environ)  # set before any file was read: these beat every file, even after a restart
for _f in ENV_FILES:
    load_dotenv(_f)

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
RESEARCH_FILE = DATA_DIR / "research" / "other_utilities.json"
SUBMISSIONS_DIR = DATA_DIR / "submissions"  # plans added in the Sources menu
SOURCES_DB = Path(os.getenv("TANDEM_SOURCES_DB", DATA_DIR / "tandem.db"))  # the sources table (utilities as data)
MODELS_FILE = REPO_DIR / "config" / "models.json"  # which model does each job (committed, no secrets)
MODELS_LOCAL_FILE = REPO_DIR / "config" / "models.local.json"  # this machine's overrides (gitignored)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# LLMs: keys only. Which model does which job is in config/models.json.
GEMINI_API_KEY = env("GEMINI_API_KEY")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")  # Claude
OPENAI_API_KEY = env("OPENAI_API_KEY")
JEV_PROVIDER = env("JEV_PROVIDER")  # where Jev runs: typesafe | openrouter | cloudflare | mock | "" (off)
# Model names used to come from these. They're ignored now, so the API warns instead of silently changing models.
LEGACY_MODEL_VARS = [v for v in ("GEMINI_MODEL", "GEMINI_FALLBACK_MODEL", "GEMINI_RETRIES", "JEV_MODEL") if env(v)]

# local: the setup screen can take keys and writes them to backend/.env.local.
# hosted: keys come only from the server's environment, and the setup screen needs ADMIN_PASSCODE.
APP_MODE = "hosted" if env("APP_MODE").lower() == "hosted" else "local"
ADMIN_PASSCODE = env("ADMIN_PASSCODE")


def from_shell(var: str) -> bool:
    return var in _FROM_SHELL

# Geocoding
OSM_LIVE = env("OSM_LIVE", "true").lower() == "true"  # fetch OSM live when there is no cache yet
# OSM search area as "south,west,north,east". Empty: the union of the states of every active source.
OSM_BBOX = env("OSM_BBOX")
NOMINATIM_USER_AGENT = env("NOMINATIM_USER_AGENT", "tandem-shellhacks/0.1")
# Research team: other utilities near the river. Live = also run a Google-grounded Gemini search each run.
RESEARCH_LIVE = env("RESEARCH_LIVE", "false").lower() == "true"
NOMINATIM_BUDGET_S = env_float("NOMINATIM_BUDGET_S", 60.0)  # live lookup time per run (1 request/second)

# Storage (Tiger Data = managed Postgres + TimescaleDB). Empty = no database, files only.
DATABASE_URL = env("DATABASE_URL")

# Run pacing: 1.0 = demo speed, 0 = as fast as possible
PACE = env_float("TANDEM_PACE", 1.0)

# "Today" for the hide-finished filter and the past-date check. Fixed so runs are reproducible.
TODAY = env("TANDEM_TODAY", "2026-09-26")

# AI reader (readers/ai_reader.py): refuse a run whose estimated model cost is above this, in USD. 0 = no limit.
# The estimate needs the model's price in config/models.json "prices"; without one it is reported as unknown.
MAX_RUN_COST_USD = env_float("MAX_RUN_COST_USD", 0.0)
AI_READER_MIN_INTERVAL_S = env_float("AI_READER_MIN_INTERVAL_S", 0.0)  # pause between model calls (free-tier rate limits)
DRAFTS_DIR = DATA_DIR / "sources"  # an AI reader's output per source, waiting for review
UPLOADS_DIR = DATA_DIR / "uploads"  # filings added in the Sources menu, stored as {sha256}.pdf
UPLOAD_MAX_MB = env_float("UPLOAD_MAX_MB", 50.0)

# Cost model. No savings figure is shown unless a share AND its source are both set.
COST_MOBILIZATION_SHARE = env_float("COST_MOBILIZATION_SHARE", 0.0)
COST_SOURCE = env("COST_SOURCE")

CORS_ORIGINS = [o for o in env("CORS_ORIGINS", "http://localhost:3000").split(",") if o]
