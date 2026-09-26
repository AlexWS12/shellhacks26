# Optional: set DATABASE_URL. Without it everything runs from files.

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app import config
from app.runtime.run import Run

log = logging.getLogger("tiger")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, mode TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, stats JSONB);
CREATE TABLE IF NOT EXISTS projects (
  run_id TEXT, id TEXT, utility TEXT, sponsor TEXT, name TEXT, in_service_date DATE, build_start DATE,
  cost_total BIGINT, project_type TEXT, lat DOUBLE PRECISION, lon DOUBLE PRECISION, location_confidence TEXT,
  source_file TEXT, source_page INT, source_ref TEXT, record JSONB, PRIMARY KEY (run_id, id));
CREATE TABLE IF NOT EXISTS project_overlaps (
  run_id TEXT, id TEXT, project_a TEXT, project_b TEXT, distance_mi DOUBLE PRECISION, time_gap_days INT,
  windows_overlap BOOLEAN, pair_confidence TEXT, in_sponsor_sample BOOLEAN, rank INT, PRIMARY KEY (run_id, id));
CREATE TABLE IF NOT EXISTS checks (
  run_id TEXT, id TEXT, level TEXT, rule TEXT, title TEXT, detail TEXT, source TEXT, project_id TEXT, actor TEXT,
  PRIMARY KEY (run_id, id));
CREATE TABLE IF NOT EXISTS agent_events (
  ts TIMESTAMPTZ NOT NULL, run_id TEXT NOT NULL, seq INT NOT NULL, type TEXT NOT NULL, agent_id TEXT,
  actor TEXT, payload JSONB);
"""

TIMESCALE = [  # stops at the first step that fails
    "CREATE EXTENSION IF NOT EXISTS timescaledb",
    "SELECT create_hypertable('agent_events', by_range('ts'), if_not_exists => TRUE)",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS agent_activity_1m WITH (timescaledb.continuous) AS
       SELECT time_bucket('1 minute', ts) AS bucket, agent_id, count(*) AS events,
              count(*) FILTER (WHERE type = 'judgment') AS judgments
       FROM agent_events GROUP BY bucket, agent_id WITH NO DATA""",
    """SELECT add_continuous_aggregate_policy('agent_activity_1m', start_offset => INTERVAL '30 days',
       end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)""",
]


_ready = False


def enabled() -> bool:
    return bool(config.DATABASE_URL)


def _connect():
    import psycopg

    return psycopg.connect(config.DATABASE_URL, connect_timeout=5, autocommit=True)


def init_schema() -> bool:
    global _ready
    with _connect() as conn:
        conn.execute(SCHEMA)
        _ready = True
        for step in TIMESCALE:
            try:
                conn.execute(step)
            except Exception as e:  # plain Postgres: the tables still work
                log.warning("TimescaleDB step skipped (%s): %s", step.split("(")[0][:40], str(e).splitlines()[0][:160])
                return False
        return True


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _persist(run: Run) -> None:
    if not _ready:  # the database may not have been up when the API started
        init_schema()
    b = run.board
    ev = run.events
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO runs VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING",
                    (run.id, run.mode, _ts(ev[0]["ts"]), _ts(ev[-1]["ts"]),
                     json.dumps({"projects": len(b.projects), "overlaps": len(b.overlaps), "checks": len(b.checks)})))
        cur.executemany(
            "INSERT INTO projects VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [(run.id, p.id, p.utility, p.sponsor, p.name, p.in_service_date, p.build_start, p.cost_total, p.project_type,
              p.lat, p.lon, p.location_confidence, p.source_file, p.source_page, p.source_ref, p.model_dump_json())
             for p in b.projects.values()])
        cur.executemany(
            "INSERT INTO project_overlaps VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [(run.id, o.id, o.project_a, o.project_b, o.distance_mi, o.time_gap_days, o.windows_overlap,
              o.pair_confidence, o.in_sponsor_sample, o.rank) for o in b.overlaps])
        cur.executemany(
            "INSERT INTO checks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [(run.id, c.id, c.level, c.rule, c.title, c.detail, c.source, c.project_id, c.actor) for c in b.checks])
        with cur.copy("COPY agent_events (ts, run_id, seq, type, agent_id, actor, payload) FROM STDIN") as copy:
            for e in ev:
                rest = {k: v for k, v in e.items() if k not in ("ts", "run_id", "seq", "type", "agent_id", "actor")}
                copy.write_row((_ts(e["ts"]), run.id, e["seq"], e["type"], e.get("agent_id"), e.get("actor"),
                                json.dumps(rest, default=str)))
    log.info("Tiger: stored run %s (%d events)", run.id, len(ev))


async def persist_run(run: Run) -> None:
    if not enabled():
        return
    try:
        await asyncio.to_thread(_persist, run)
        run.emit("store.saved", target="tiger", events=len(run.events))
    except Exception as e:
        log.warning("Tiger persist failed: %s", str(e)[:300])
        run.emit("store.failed", target="tiger", message=f"{type(e).__name__}: {str(e)[:160]}")


def agent_stats(limit_runs: int = 10) -> list[dict[str, Any]]:
    sql = """
      SELECT r.run_id, r.started_at, e.agent_id, count(*) AS events,
             count(*) FILTER (WHERE e.type = 'judgment') AS judgments,
             extract(epoch FROM max(e.ts) - min(e.ts)) AS seconds
      FROM agent_events e JOIN (SELECT run_id, started_at FROM runs ORDER BY started_at DESC LIMIT %s) r USING (run_id)
      WHERE e.agent_id IS NOT NULL
      GROUP BY r.run_id, r.started_at, e.agent_id ORDER BY r.started_at DESC, e.agent_id"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit_runs,))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
