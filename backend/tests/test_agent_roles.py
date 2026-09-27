# Each agent lists the model jobs it calls, so the setup screen can show which models an agent depends on.
import inspect

from app.clients import models
from app.pipeline import build_agents

NOT_AN_AGENT = {"watchdog", "smoke"}  # the run's watchdog and the startup check
ADDED_READERS = {"reader", "extract_submission"}  # only present when a filing or plan has been added


def test_agent_roles_are_real_jobs_called_by_that_agent():
    known = set(models.load())
    for a in build_agents():
        assert set(a.spec.roles) <= known, (a.spec.id, a.spec.roles)
        source = inspect.getsource(type(a))
        module = inspect.getsource(inspect.getmodule(type(a)))
        for role in a.spec.roles:
            assert f'"{role}"' in source or f'"{role}"' in module, f"{a.spec.id} lists {role} but never calls it"
        assert a.spec.public()["roles"] == a.spec.roles


def test_every_job_belongs_to_an_agent():
    covered = {r for a in build_agents() for r in a.spec.roles}
    assert set(models.load()) - covered - NOT_AN_AGENT - ADDED_READERS == set()
