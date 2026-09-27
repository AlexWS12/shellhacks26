import asyncio

import pytest

from app import config
from app.runtime.executor import execute
from app.runtime.run import new_run


@pytest.fixture(scope="session")
def finished_run():
    # One offline run over the real files: no Gemini, no Jev (local rules decide), no live OSM or
    # Nominatim (committed caches only). Nothing is written to data/.
    from app.pipeline import SOURCES, build_agents

    config.GEMINI_API_KEY = ""
    config.JEV_PROVIDER = ""
    config.OSM_LIVE = False
    run = new_run("live")
    run.pace = 0
    asyncio.run(execute(run, build_agents(), SOURCES))
    return run
