# Agent graph:
#   sample, extract_desc, extract_ga   start together
#   geocoder    <- sample, extract_desc, extract_ga
#   validator   <- sample, extract_desc, extract_ga
#   classifier  <- extract_desc, extract_ga
#   overlap     <- geocoder
#   reference   <- overlap, sample
#   cost_research <- overlap, classifier   (filed, published or benchmark cost per project)
#   cost        <- cost_research            (savings range per pair, checked by Jev)
#   analyst     <- cost, validator, reference
#   advocate_desc, advocate_ga <- analyst
#   mediator    <- advocate_desc, advocate_ga
#   research_electric, research_gas, research_roads_water   start with the readers (research team)
#   third_party <- overlap, research_*
#   writer      <- mediator, third_party   (the final report)
#   Readers come from the sources table (store/sources.py): reader builtin:desc -> extract_desc, builtin:gpc ->
#   extract_ga, sheet / ai -> extract_<plan> (a plan saved in the Sources menu). geocoder, validator and classifier
#   wait for every Reader.

import asyncio
import json
import logging
from dataclasses import replace

from app import config
from app.clients import models
from app.clients.errors import describe
from app.agents.analysis import Analyst, OverlapEngine, ReferenceChecker
from app.agents.classifier import Classifier
from app.agents.coordination import Advocate, Mediator
from app.agents.costs import CostResearcher, SavingsCalculator
from app.agents.extractors import SAMPLE_SOURCE, DescExtractor, GaExtractor, SampleReader, card
from app.agents.geocoder import Geocoder
from app.agents.research import OtherUtilities, ResearchScout
from app.agents.submissions import SubmissionReader
from app.agents.validator import Validator
from app.agents.writer import Writer
from app.readers.approved import ApprovedReader
from app.core.models import RESEARCH_CATEGORIES, Project
from app.core.owners import book
from app.runtime import watchdog
from app.runtime.agent import Agent
from app.runtime.executor import execute
from app.runtime.run import Run
from app.store import dataset, sources, submissions, tiger

log = logging.getLogger("pipeline")
# reader -> the Reader agent for that kind of source
BUILTIN_READERS = {"builtin:desc": DescExtractor, "builtin:gpc": GaExtractor}


def readers() -> list[tuple[Agent, dict, bool]]:
    # One Reader per active source, in the sources table's order: (agent, its row in the pipeline panel, built-in).
    out: list[tuple[Agent, dict, bool]] = []
    for src in sources.active():
        if src.reader in BUILTIN_READERS:
            out.append((BUILTIN_READERS[src.reader](src), card(src), True))
        elif src.reader in ("sheet", "ai"):
            sub = submissions.get(src.id)
            if sub is not None and sub.status == "ready":  # a plan saved in the Sources menu's spreadsheet / link form
                reader = SubmissionReader(sub)
                out.append((reader, reader.source(), False))
            elif src.reader == "ai" and sources.published_path(src.id).exists():  # an AI-read filing after review
                out.append((ApprovedReader(src), card(src), False))
        else:
            log.warning("source %s: no Reader for '%s'", src.id, src.reader)
    return out


def build_pipeline() -> tuple[list[Agent], list[dict]]:
    # The agents and sources for one run, read from the sources table at that moment. The geocoder, validator and
    # classifier wait for every Reader. Panel order: built-in filings, the benchmark, then added plans.
    found = readers()
    reading = [a for a, _, _ in found]
    ids = ["sample", *(r.spec.id for r in reading)]
    core: list[Agent] = [SampleReader(), *reading, Geocoder(), Validator(), Classifier()]
    for a in core:
        if a.spec.id in ("geocoder", "validator", "classifier"):
            a.spec = replace(a.spec, depends_on=ids)
    agents = [*core, OverlapEngine(), ReferenceChecker(), CostResearcher(), SavingsCalculator(), Analyst(), Advocate("dominion"),
              Advocate("georgia"), Mediator(), *(ResearchScout(c) for c in RESEARCH_CATEGORIES), OtherUtilities(), Writer()]
    cards = [c for _, c, b in found if b] + [SAMPLE_SOURCE] + [c for _, c, b in found if not b]
    return agents, cards


def build_agents() -> list[Agent]:
    return build_pipeline()[0]


async def publish(run: Run) -> None:
    # Finished run -> API dataset, snapshot file, and Tiger Data if configured.
    snap = run.board.to_snapshot()
    snap["run_id"] = run.id
    started = next((e for e in run.events if e["type"] == "run.started"), {})
    snap["agents"], snap["sources"] = started.get("agents", []), started.get("sources", [])
    dataset.load_snapshot(snap)
    text = json.dumps(snap, indent=0, default=str)
    config.SNAPSHOT_PATH.write_text(text, encoding="utf-8")
    (config.RUNS_DIR / f"{run.id}.snapshot.json").write_text(text, encoding="utf-8")  # replays restore it
    await tiger.persist_run(run)


async def run_live(run: Run) -> None:
    watcher = None
    try:
        run.open_log()
        models.start(run.emit)  # before the watchdog, so it shares the run's breaker and events
        if config.JEV_PROVIDER:
            watcher = asyncio.create_task(watchdog.watch(run))
        agents, sources = build_pipeline()
        await execute(run, agents, sources, on_done=publish)
    except Exception as e:  # agents report their own errors; this catches the rest
        log.exception("run failed")
        if not run.finished:
            run.emit("run.failed", message=describe(e))
    finally:
        if watcher:
            watcher.cancel()


async def run_extraction(run: Run, source_id: str, pages: str | None = None) -> None:
    # One AI reader over one source, as its own recorded run (the panel and replay show it like any other). Nothing is
    # published: the projects go to the source's draft, for review.
    from app.readers.ai_reader import AIReader

    try:
        run.open_log()
        models.start(run.emit)
        src = sources.get(source_id)
        if src is None:
            raise ValueError(f"no source '{source_id}'")
        reader = AIReader(src, pages)
        await execute(run, [reader], [card(src)])
    except Exception as e:
        log.exception("extraction failed")
        if not run.finished:
            run.emit("run.failed", message=describe(e))


async def run_activation(run: Run, source_id: str) -> None:
    # A reviewed source goes live: its projects join the shown results, only they are geocoded, and overlaps are
    # recomputed across every source. The write-ups of the last full run are kept; a new full run adds the source to
    # them. Recorded like any run, so the panel shows it and it can be replayed.
    try:
        run.open_log()
        models.start(run.emit)
        src = sources.get(source_id)
        if src is None:
            raise ValueError(f"no source '{source_id}'")
        new_ids = {row["id"] for row in sources.load_published(source_id)}
        # The benchmark file's surveyed points, as in a full run, so the new projects are placed the same way a full
        # run would place them (e.g. Purrysburg is only there).
        from app.core.sample import read_sample

        run.board.sample = read_sample(config.SAMPLE_XLSX)
        owners = book()
        for p in dataset.CURRENT.projects.values():  # the results on screen, without this source's earlier projects
            if not _belongs(p, src, owners):
                run.board.projects[p.id] = p.model_copy(deep=True)
        reader = ApprovedReader(src)
        geo = Geocoder(only=new_ids, tag=source_id)
        geo.spec = replace(geo.spec, depends_on=[reader.spec.id])
        await execute(run, [reader, geo, OverlapEngine()], [card(src)], on_done=lambda r: publish_merged(r, src))
    except Exception as e:
        log.exception("activation failed")
        if not run.finished:
            run.emit("run.failed", message=describe(e))


def _belongs(p, src, owners) -> bool:
    s = owners.of(p)
    return p.source_id == src.id or (s is not None and s.id == src.id)


def _snapshot() -> dict:
    snap = json.loads(config.SNAPSHOT_PATH.read_text(encoding="utf-8")) if config.SNAPSHOT_PATH.exists() else {}
    return {"projects": [], "checks": [], "reference": [], "overlaps": [], "sources": [], "agents": [], **snap}


def _write_snapshot(snap: dict, run_id: str | None = None) -> None:
    dataset.load_snapshot(snap)
    text = json.dumps(snap, indent=0, default=str)
    config.SNAPSHOT_PATH.write_text(text, encoding="utf-8")
    if run_id:
        (config.RUNS_DIR / f"{run_id}.snapshot.json").write_text(text, encoding="utf-8")  # replays restore it


async def publish_merged(run: Run, src) -> None:
    # The shown results plus the activated source: its projects and checks in, overlaps recomputed.
    snap = _snapshot()
    b = run.board
    draft = sources.load_draft(src.id) or {}
    mine = {p.id for p in b.projects.values() if p.source_id == src.id}
    snap["projects"] = [p.model_dump() for p in b.projects.values()]
    snap["overlaps"] = [o.model_dump() for o in b.overlaps]
    keep = [c for c in snap["checks"] if c.get("project_id") not in mine and not str(c.get("id", "")).endswith(src.id)
            and src.id not in str(c.get("id", ""))]
    snap["checks"] = keep + [c.model_dump() for c in b.checks] + draft.get("checks", [])
    snap["sources"] = [c for c in snap["sources"] if c.get("id") != src.id] + [card(src)]
    reader = ApprovedReader(src).spec.public()
    snap["agents"] = [a for a in snap["agents"] if a.get("id") != reader["id"]] + [reader]
    snap.setdefault("run_id", run.id)
    _write_snapshot(snap, run.id)


def remove_from_results(src) -> int:
    # A source deactivated or deleted: its projects, their overlaps and checks leave the shown results.
    snap = _snapshot()
    owners = book()
    gone = {p["id"] for p in snap["projects"] if p.get("source_id") == src.id or p.get("utility") == src.utility_key
            or _belongs(Project(**p), src, owners)}
    snap["projects"] = [p for p in snap["projects"] if p["id"] not in gone]
    snap["overlaps"] = [o for o in snap["overlaps"] if o["project_a"] not in gone and o["project_b"] not in gone]
    snap["checks"] = [c for c in snap["checks"] if c.get("project_id") not in gone and src.id not in str(c.get("id", ""))]
    snap["sources"] = [c for c in snap["sources"] if c.get("id") != src.id]
    snap["agents"] = [a for a in snap["agents"] if a.get("id") != sources.agent_id(src)]
    _write_snapshot(snap)
    return len(gone)
