# UtiliTies

Neighboring utilities plan their construction years ahead, usually without seeing each other's plans.
UtiliTies is a team of AI agents that reads two utilities' public filings (Dominion Energy South Carolina and
Georgia Power), puts every planned project on a map, and finds where they could share crews, equipment,
right-of-way and outages because they are building close together.

Built at ShellHacks 2026 for the Gridlock challenge.

## What it does

- **Reads the filings live.** 44 Dominion projects and 208 Georgia projects, parsed straight from the PDFs.
- **Places every project.** OpenStreetMap substations and towns, with each fuzzy match confirmed by Jev.
- **Checks the data.** Malformed costs, spending after a project is done, text copied between projects.
- **Finds overlaps.** Every pair under 25 miles (40 km) apart at their closest points, so a long line passing near a
  substation counts, ranked by the challenge's tiers: touching or crossing, under 1 mile (share the land), under 5
  miles (share site logistics), under 25 miles (share crews and equipment). Plus whether their build windows overlap.
- **Looks past the two utilities.** A research team maps other owners' projects near the river: other
  electric utilities, gas pipelines, roads and water. You choose which before each run. Every record cites its
  sources, and each one near both sides of an opportunity is flagged.
- **Takes any utility's plan.** Add a spreadsheet, PDF or link in the Sources menu; it gets its own Reader and its
  projects are compared with every other owner's.
- **Writes the report.** A Writer agent sums up the opportunities at the end; every number in it comes from code.
- **Explains them.** Gemini writes each opportunity up, and two utility advocates plus a mediator draft the
  coordination meeting.
- **Shows its work.** Every agent appears on the map and in the pipeline graph while it runs; every number
  links back to a page in the filing.
- **Scores itself.** 6 of 6 known overlaps reproduced exactly (to 0.01 mi and to the day), using the benchmark's
  center-to-center rule.

## Stack

| | |
|---|---|
| Agents | Python, FastAPI, our own DAG executor, server-sent events |
| AI | Jev (TypeSafe) for fast typed decisions, Gemini for reading and writing |
| Data | Tiger Data (Postgres + TimescaleDB) for runs and the agent event stream |
| Map | Next.js, MapLibre, OpenFreeMap |
| Hosting | DigitalOcean App Platform |

## Run it

```bash
# backend -> http://localhost:8000/api/health
cd backend
cp .env.example .env      # add GEMINI_API_KEY, TYPESAFE_API_KEY, DATABASE_URL
uv sync
uv run uvicorn app.main:app --port 8000

# frontend -> http://localhost:3000
cd frontend
npm install
npm run dev
```

Tests: `cd backend && uv run pytest`

Models: the **Models** button opens a setup screen: keys, the model for each job and its backups, a test of every
model, and Save. It opens by itself on first launch, and when a run is refused because a job has no working model.
On a hosted server set `APP_MODE=hosted` and `ADMIN_PASSCODE`: the screen then needs the passcode, and keys come only
from the server's environment. Behind it, `config/models.json` lists, for each job, the models to try in order. A
model that fails is retried or skipped, and the run log records each fallback. Keys come only from env vars. To override a job on your machine,
use `config/models.local.json`; keys for your machine only go in `.env.local`. Both files are gitignored. To check
every configured model against its key: `cd backend && uv run python scripts/smoke.py`.
