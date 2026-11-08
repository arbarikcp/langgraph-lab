"""Ch 18: an incident commander deep agent.

Plans with todos, runs two investigators in parallel (subagent specs), confirms with a hand-built
verified-RCA graph (CompiledSubAgent), writes /report.md, saves a lesson to /memories/ (Store),
and asks a human before restart_job (interrupt_on).
Runs anywhere with one scripted model per agent; --ollama uses the real LLM for all of them.
Run:  python -m chapters.ch18_deepagents_api.incident_commander [--ollama]
"""
import sys

from deepagents import CompiledSubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from chapters.ch18_deepagents_api.oncall_tools import (
    flink_job_status, restart_job, run_trino_query, taskmanager_metrics,
)
from chapters.ch18_deepagents_api.rca_graph import build_rca_graph
from chapters.ch18_deepagents_api.sim import platform
from chapters.ch18_deepagents_api.sim.scripted_model import ScriptedChatModel, call

COMMANDER_PROMPT = ("You are the incident commander for the data platform. Plan with todos, delegate one "
                    "investigation per component in parallel, confirm the root cause with verified-rca, "
                    "write /report.md, save durable lessons to /memories/lessons.md, then propose the fix.")
INCIDENT = ("Sales dashboards stale since 14:00. orders-enricher is lagging; sales.orders scans are slow. "
            "Find the root cause and fix it.")


def subagent_specs(models: dict) -> list:
    """models: name → chat model (scripted demo) or {} to use the commander's model."""
    def spec(name, description, system_prompt, tools):
        return {"name": name, "description": description, "system_prompt": system_prompt, "tools": tools,
                **({"model": models[name]} if name in models else {})}

    return [
        spec("flink-investigator",
             "Investigates ONE Flink job: lag, checkpoints, backpressure. Pass the job name.",
             "Investigate the job. Write raw evidence to /findings/<job>.md. Reply with a 2-line summary.",
             [flink_job_status, taskmanager_metrics]),
        spec("lakehouse-investigator",
             "Investigates ONE Iceberg table: snapshots, small files, Trino scan performance.",
             "Use read-only SQL. Write evidence to /findings/<table>.md. Reply with a 2-line summary.",
             [run_trino_query]),
        CompiledSubAgent(name="verified-rca",
                         description="Budgeted, self-verified root cause for one well-scoped question.",
                         runnable=build_rca_graph(models.get("verified-rca") or models["__default__"])),
    ]


def build_commander(model, models: dict, store):
    return create_deep_agent(
        model=model,
        tools=[restart_job],
        system_prompt=COMMANDER_PROMPT,
        subagents=subagent_specs(models),
        middleware=[TodoListMiddleware()],
        backend=CompositeBackend(default=StateBackend(), routes={
            "/memories/": StoreBackend(namespace=lambda runtime: ("oncall", "memories"), store=store)}),
        store=store,
        checkpointer=InMemorySaver(),
        interrupt_on={"restart_job": True},            # human approval for the production action
        name="incident-commander",
    )


# --- scripted replies -------------------------------------------------------------------------

def tool_turn(*calls: dict) -> AIMessage:
    return AIMessage("", tool_calls=list(calls))


def scripted_models() -> tuple[ScriptedChatModel, dict]:
    todos = [{"content": c, "status": "pending"} for c in
             ("Investigate orders-enricher", "Investigate sales.orders", "Confirm root cause",
              "Write /report.md", "Save lesson", "Fix")]
    commander = ScriptedChatModel(label="commander", replies=[
        tool_turn(call("write_todos", todos=todos)),
        tool_turn(call("task", subagent_type="flink-investigator", description="Investigate orders-enricher."),
                  call("task", subagent_type="lakehouse-investigator", description="Investigate sales.orders.")),
        tool_turn(call("task", subagent_type="verified-rca",
                       description="Is enrich-join backpressure the root cause of the orders-enricher lag?")),
        tool_turn(call("ls", path="/findings")),
        tool_turn(call("write_file", file_path="/report.md", content=(
            "# INC-4211\nRoot cause: enrich-join backpressure (heap 92%, parallelism 4) → lag 2.4M → stale "
            "dashboards. Also: sales.orders has 1,240 snapshots / 38% small files → slow scans.\n"
            "Fix: restart orders-enricher at parallelism 8; expire snapshots + compact sales.orders."))),
        tool_turn(call("write_file", file_path="/memories/lessons.md",
                       content="orders-enricher at parallelism 4 backpressures on enrich-join under peak load.")),
        tool_turn(call("restart_job", job="orders-enricher", parallelism=8)),
        AIMessage("orders-enricher restarted at parallelism 8. Report in /report.md.")])
    flink = ScriptedChatModel(label="flink", replies=[
        tool_turn(call("flink_job_status", job="orders-enricher"), call("taskmanager_metrics", job="orders-enricher")),
        tool_turn(call("write_file", file_path="/findings/orders-enricher.md",
                       content="lag=2,400,000; checkpoint FAILED; backpressure HIGH on enrich-join; heap 92%")),
        AIMessage("orders-enricher: backpressure HIGH on enrich-join (heap 92%), lag 2.4M.\nHypothesis: under-scaled.")])
    lake = ScriptedChatModel(label="lake", replies=[
        tool_turn(call("run_trino_query", sql='SELECT * FROM "sales.orders$snapshots"'),
                  call("run_trino_query", sql='SELECT * FROM "sales.orders$files"')),
        tool_turn(call("write_file", file_path="/findings/sales.orders.md",
                       content="1,240 snapshots; newest 47 min old; 38% files < 16 MB")),
        AIMessage("sales.orders: 1,240 snapshots, 38% small files → slow scans.\nData 47 min old (writer behind).")])
    rca = ScriptedChatModel(label="rca", replies=[
        tool_turn(call("taskmanager_metrics", job="orders-enricher")),
        AIMessage("Confirmed: backpressure HIGH on 'enrich-join', heap=92% at parallelism=4. Scale to 8.")])
    return commander, {"flink-investigator": flink, "lakehouse-investigator": lake, "verified-rca": rca}


# --- demo -------------------------------------------------------------------------------------

def describe(ns: tuple, node: str, delta) -> str | None:
    who = "commander" if not ns else f"subagent {ns[0].split(':')[1][:8]}"
    for msg in (delta or {}).get("messages", []) if isinstance(delta, dict) else []:
        if msg.type == "ai" and msg.tool_calls:
            return f"[{who}] {[c['name'] + ('→' + c['args']['subagent_type'] if c['name'] == 'task' else '') for c in msg.tool_calls]}"
        if msg.type == "tool" and msg.name == "task":
            return f"[{who}] task returned: {msg.content.splitlines()[0][:80]}"
    return None


def main() -> None:
    store = InMemoryStore()
    platform.OPS = platform.FlinkOps()
    if "--ollama" in sys.argv:
        from common.llm import llm
        commander, models = llm, {"__default__": llm}
    else:
        commander, models = scripted_models()
    agent = build_commander(commander, models, store)
    cfg = {"configurable": {"thread_id": "INC-4211"}}

    for ns, update in agent.stream({"messages": [{"role": "user", "content": INCIDENT}]}, cfg,
                                   stream_mode="updates", subgraphs=True):
        for node, delta in update.items():
            if line := describe(ns, node, delta):
                print(line)

    state = agent.get_state(cfg)
    request = state.interrupts[0].value["action_requests"][0]
    print(f"\n⏸  paused before {request['name']}({request['args']}); restarts so far: {platform.OPS.restarts}")
    out = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    print(f"▶  approved → restarts: {platform.OPS.restarts}")
    print(f"\nfinal: {out['messages'][-1].content}")
    print(f"todos: {len(out['todos'])} · thread files: {list(out['files'])}")
    print(f"Store (/memories/): {[item.key for item in store.search(('oncall', 'memories'))]}")


if __name__ == "__main__":
    main()
