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

import asyncio
import json
import logging

from app import config
from app.agents.analysis import Analyst, CostEstimator, OverlapEngine, ReferenceChecker
from app.agents.classifier import Classifier
from app.agents.coordination import Advocate, Mediator
from app.agents.extractors import DESC_SOURCE, GA_SOURCE, SAMPLE_SOURCE, DescExtractor, GaExtractor, SampleReader
from app.agents.geocoder import Geocoder
from app.agents.validator import Validator
from app.runtime import watchdog
from app.runtime.agent import Agent
from app.runtime.executor import execute
from app.runtime.run import Run
from app.store import dataset, tiger

log = logging.getLogger("pipeline")
SOURCES = [DESC_SOURCE, GA_SOURCE, SAMPLE_SOURCE]


def build_agents() -> list[Agent]:
    return [SampleReader(), DescExtractor(), GaExtractor(), Geocoder(), Validator(), Classifier(), OverlapEngine(),
            ReferenceChecker(), CostEstimator(), Analyst(), Advocate("dominion"), Advocate("georgia"), Mediator()]


async def publish(run: Run) -> None:
    # Finished run -> API dataset, snapshot file, and Tiger Data if configured.
    snap = run.board.to_snapshot()
    snap["run_id"] = run.id
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
        await execute(run, build_agents(), SOURCES, on_done=publish)
    except Exception as e:  # agents report their own errors; this catches the rest
        log.exception("run failed")
        if not run.finished:
            run.emit("run.failed", message=f"{type(e).__name__}: {e}")
    finally:
        if watcher:
            watcher.cancel()
