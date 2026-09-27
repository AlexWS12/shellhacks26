# Agent graph:
#   sample, extract_desc, extract_ga   start together
#   geocoder    <- sample, extract_desc, extract_ga
#   validator   <- sample, extract_desc, extract_ga
#   classifier  <- extract_desc, extract_ga
#   overlap     <- geocoder
#   reference   <- overlap, sample
#   cost        <- overlap, classifier
#   analyst     <- cost, validator, reference
#   advocate_desc, advocate_ga <- analyst
#   mediator    <- advocate_desc, advocate_ga
#   research_electric, research_gas, research_roads_water   start with the readers (research team)
#   third_party <- overlap, research_*
#   writer      <- mediator, third_party   (the final report)
#   extract_<plan>  one Reader per plan saved in the Sources menu; geocoder, validator and classifier wait for them

import asyncio
import json
import logging
from dataclasses import replace

from app import config
from app.agents.analysis import Analyst, CostEstimator, OverlapEngine, ReferenceChecker
from app.agents.classifier import Classifier
from app.agents.coordination import Advocate, Mediator
from app.agents.extractors import DESC_SOURCE, GA_SOURCE, SAMPLE_SOURCE, DescExtractor, GaExtractor, SampleReader
from app.agents.geocoder import Geocoder
from app.agents.research import OtherUtilities, ResearchScout
from app.agents.submissions import SubmissionReader
from app.agents.validator import Validator
from app.agents.writer import Writer
from app.core.models import RESEARCH_CATEGORIES
from app.runtime import watchdog
from app.runtime.agent import Agent
from app.runtime.executor import execute
from app.runtime.run import Run
from app.store import dataset, submissions, tiger

log = logging.getLogger("pipeline")
SOURCES = [DESC_SOURCE, GA_SOURCE, SAMPLE_SOURCE]


def build_pipeline() -> tuple[list[Agent], list[dict]]:
    # The agents and sources for one run, read from the saved plans at that moment.
    readers = [SubmissionReader(s) for s in submissions.list_all() if s.status == "ready"]
    ids = [r.spec.id for r in readers]
    core: list[Agent] = [SampleReader(), DescExtractor(), GaExtractor(), *readers, Geocoder(), Validator(), Classifier()]
    for a in core:
        if a.spec.id in ("geocoder", "validator", "classifier") and ids:
            a.spec = replace(a.spec, depends_on=[*a.spec.depends_on, *ids])
    agents = [*core, OverlapEngine(), ReferenceChecker(), CostEstimator(), Analyst(), Advocate("dominion"),
              Advocate("georgia"), Mediator(), *(ResearchScout(c) for c in RESEARCH_CATEGORIES), OtherUtilities(), Writer()]
    return agents, [*SOURCES, *(r.source() for r in readers)]


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
        if config.JEV_PROVIDER:
            watcher = asyncio.create_task(watchdog.watch(run))
        agents, sources = build_pipeline()
        await execute(run, agents, sources, on_done=publish)
    except Exception as e:  # agents report their own errors; this catches the rest
        log.exception("run failed")
        if not run.finished:
            run.emit("run.failed", message=f"{type(e).__name__}: {e}")
    finally:
        if watcher:
            watcher.cancel()
