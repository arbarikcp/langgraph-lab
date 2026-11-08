"""Ch 10: a durable post-mortem workflow with the functional API (@entrypoint + @task).

Run:  python -m chapters.ch10_functional_api.postmortem            # template draft, runs anywhere
      python -m chapters.ch10_functional_api.postmortem --ollama   # the LLM writes the draft
"""
import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint, task
from langgraph.types import Command, interrupt

from chapters.ch10_functional_api.sim.incident_sources import IncidentSources

JOBS = ["orders-enricher", "orders-sink"]


def template_draft(timeline: str, metrics: list[str]) -> str:
    return (f"Impact: orders pipeline lagged ~40 min.\nTimeline: {timeline}\n"
            f"Evidence: {' | '.join(metrics)}\nCause: enrich-join backpressure caused checkpoint timeouts.\n"
            "Fix: parallelism 4 -> 8.")


def llm_draft(timeline: str, metrics: list[str]) -> str:
    from common.llm import llm
    prompt = f"Write a 5-line blameless post-mortem.\nTimeline: {timeline}\nMetrics: {metrics}"
    return llm.invoke(prompt).content


def build_postmortem(sources: IncidentSources, use_llm: bool = False, checkpointer=None):
    """Tasks close over `sources`, so tests and demos each get their own fake systems.

    Pass a durable checkpointer (SqliteSaver / PostgresSaver) to pause in one process and
    resume in another, days later. See review_service.py.
    """

    @task
    def fetch_timeline(incident_id: str) -> str:
        return sources.timeline(incident_id)

    @task
    def fetch_metrics(job: str) -> str:
        return sources.metrics(job)

    @task
    def draft(timeline: str, metrics: list[str]) -> str:     # non-deterministic (LLM) -> must be a task
        return (llm_draft if use_llm else template_draft)(timeline, metrics)

    @task
    def publish(incident_id: str, doc: str) -> str:          # side effect -> must be a task
        return sources.publish(incident_id, doc)

    @entrypoint(checkpointer=checkpointer or InMemorySaver())
    def postmortem(incident_id: str) -> dict:
        # Plain Python control flow. This body re-runs on every resume;
        # each finished task returns its saved result instead of running again.
        timeline_f = fetch_timeline(incident_id)             # returns a future, starts now
        metric_fs = [fetch_metrics(j) for j in JOBS]         # these run in parallel
        doc = draft(timeline_f.result(), [f.result() for f in metric_fs]).result()
        review = interrupt({"review": doc})                  # pause for a human
        url = publish(incident_id, doc).result()
        return {"postmortem": doc, "review": review, "url": url}

    return postmortem


def main() -> None:
    #use_llm = "--ollama" in sys.argv
    use_llm = True

    print("▶ PM-4211: pause for review, then resume")
    sources = IncidentSources()
    workflow = build_postmortem(sources, use_llm)
    cfg = {"configurable": {"thread_id": "PM-4211"}}
    for event in workflow.stream("INC-4211", cfg):           # "updates": one event per finished task
        name, value = next(iter(event.items()))
        print(f"   [{name}] {' / '.join(str(value).splitlines())[:90]}")
    print(f"   calls before resume: {dict(sources.calls)}")
    out = workflow.invoke(Command(resume="LGTM, add action items"), cfg)
    print(f"   resumed -> review={out['review']!r} url={out['url']}")
    print(f"   calls after resume:  {dict(sources.calls)}   <- fetches did not run again")

    print("\n▶ PM-4300: the wiki is down once -> crash, then retry")
    sources = IncidentSources(wiki_failures=1)
    workflow = build_postmortem(sources, use_llm)
    cfg = {"configurable": {"thread_id": "PM-4300"}}
    workflow.invoke("INC-4300", cfg)
    try:
        workflow.invoke(Command(resume="LGTM"), cfg)
    except ConnectionError as e:
        print(f"   crashed in publish: {e}.  calls so far: {dict(sources.calls)}")
    out = workflow.invoke(None, cfg)                         # retry from the last checkpoint
    print(f"   retried -> url={out['url']}, review kept={out['review']!r}")
    print(f"   calls after retry:   {dict(sources.calls)}   <- only publish ran again")


if __name__ == "__main__":
    main()
