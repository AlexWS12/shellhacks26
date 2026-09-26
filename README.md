# Tandem

Neighboring utilities plan their construction years ahead, usually without seeing each other's plans.
Tandem is a team of AI agents that reads two utilities' public filings (Dominion Energy South Carolina and
Georgia Power), puts every planned project on a map, and finds where they could share crews, equipment,
right-of-way and outages because they are building close together.

Built at ShellHacks 2026 for the Gridlock challenge.

## What it does

- **Reads the filings live.** 44 Dominion projects and 208 Georgia projects, parsed straight from the PDFs.
- **Places every project.** OpenStreetMap substations and towns, with each fuzzy match confirmed by Jev.
- **Checks the data.** Malformed costs, spending after a project is done, text copied between projects.
- **Finds overlaps.** Every pair under 25 miles apart, plus how many days apart they finish, ranked.
- **Explains them.** Gemini writes each opportunity up, and two utility advocates plus a mediator draft the
  coordination meeting.
- **Shows its work.** Every agent appears on the map and in the pipeline graph while it runs; every number
  links back to a page in the filing.
- **Scores itself.** 6 of 6 known overlaps reproduced exactly (to 0.01 mi and to the day).

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
