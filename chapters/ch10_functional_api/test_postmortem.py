from langgraph.types import Command

from chapters.ch10_functional_api.postmortem import build_postmortem
from chapters.ch10_functional_api.sim.incident_sources import IncidentSources


def test_resume_replays_finished_tasks():
    sources = IncidentSources()
    workflow, cfg = build_postmortem(sources), {"configurable": {"thread_id": "t"}}
    workflow.invoke("INC-1", cfg)
    out = workflow.invoke(Command(resume="LGTM"), cfg)
    assert out["review"] == "LGTM"
    assert sources.calls["timeline"] == 1 and sources.calls["publish"] == 1


def test_retry_after_crash_reruns_only_the_failed_task():
    sources = IncidentSources(wiki_failures=1)
    workflow, cfg = build_postmortem(sources), {"configurable": {"thread_id": "t"}}
    workflow.invoke("INC-1", cfg)
    try:
        workflow.invoke(Command(resume="LGTM"), cfg)
    except ConnectionError:
        pass
    assert workflow.invoke(None, cfg)["url"].endswith("/INC-1")
    assert sources.calls["timeline"] == 1 and sources.calls["publish"] == 2
