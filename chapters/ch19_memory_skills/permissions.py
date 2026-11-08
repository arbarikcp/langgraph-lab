"""Ch 19: filesystem permissions — ordered allow / deny / interrupt rules, first match wins.

The commander may not touch secrets or edit policy files; writing a lesson needs a human (interrupt).
A read-only auditor subagent reviews the report. Subagent rules REPLACE the parent's, so the
auditor repeats the secrets deny before its write deny.
Runs anywhere with scripted models.
Run:  python -m chapters.ch19_memory_skills.permissions
"""
from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from chapters.ch19_memory_skills.sim.scripted_model import ScriptedChatModel, call
from chapters.ch19_memory_skills.sim.workspace import fresh_workspace

DENY_SECRETS = FilesystemPermission(operations=["read", "write"], paths=["/secrets/**", "/.env"], mode="deny")

PARENT_RULES = [                                                  # evaluated top to bottom
    DENY_SECRETS,
    FilesystemPermission(operations=["write"], paths=["/skills/**", "/AGENTS.md"], mode="deny"),
    FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="interrupt"),
    FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="allow"),
]
AUDITOR_RULES = [DENY_SECRETS,                                    # repeated: subagent rules replace the parent's
                 FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]


def build_agent(model, auditor_model, workspace: str, auditor_rules=AUDITOR_RULES):
    auditor = {"name": "auditor", "description": "Read-only review of /report.md against /findings/.",
               "system_prompt": "Check /report.md against the evidence. List unsupported claims.",
               **({"permissions": auditor_rules} if auditor_rules is not None else {}),
               **({"model": auditor_model} if auditor_model else {})}
    return create_deep_agent(model=model, tools=[], system_prompt="You are the incident commander.",
                             backend=FilesystemBackend(root_dir=workspace, virtual_mode=True),
                             permissions=PARENT_RULES, subagents=[auditor], checkpointer=InMemorySaver())


def turn(name: str, **args) -> AIMessage:
    return AIMessage("", tool_calls=[call(name, **args)])


def commander_script() -> list[AIMessage]:
    return [turn("read_file", file_path="/secrets/trino.env"),
            turn("ls", path="/"),
            turn("grep", pattern="PASSWORD", path="/", output_mode="content"),
            turn("edit_file", file_path="/AGENTS.md", old_string="= P2", new_string="= P3"),
            turn("write_file", file_path="/report.md", content="# INC-4211\nRoot cause: enrich-join backpressure."),
            turn("task", subagent_type="auditor", description="Review /report.md."),
            turn("write_file", file_path="/memories/lessons.md", content="- enrich-join backpressures at p=4."),
            AIMessage("Report written and reviewed; lesson saved.")]


def auditor_script() -> list[AIMessage]:
    return [turn("read_file", file_path="/report.md"),
            turn("write_file", file_path="/review.md", content="looks fine"),
            turn("read_file", file_path="/secrets/trino.env"),
            AIMessage("1 unsupported claim: no metric cited for 'backpressure'.")]


def results(ns_filter, stream) -> list[tuple[str, str]]:
    rows = []
    for ns, update in stream:
        for delta in update.values():
            for msg in (delta or {}).get("messages", []) if isinstance(delta, dict) else []:
                if msg.type == "tool" and ns_filter(ns):
                    rows.append((msg.name, msg.content.splitlines()[0][:80]))
    return rows


def main() -> None:
    workspace = fresh_workspace()
    agent = build_agent(ScriptedChatModel(label="cmd", replies=commander_script()),
                        ScriptedChatModel(label="aud", replies=auditor_script()), workspace)
    cfg = {"configurable": {"thread_id": "INC-4211"}}
    events = list(agent.stream({"messages": [{"role": "user", "content": "Write and review the report."}]},
                               cfg, stream_mode="updates", subgraphs=True))

    print("commander (PARENT_RULES):")
    for name, text in results(lambda ns: not ns, events):
        print(f"  {name:<11} → {text}")
    print("auditor (AUDITOR_RULES):")
    for name, text in results(lambda ns: bool(ns), events):
        print(f"  {name:<11} → {text}")

    request = agent.get_state(cfg).interrupts[0].value["action_requests"][0]
    print(f"\n⏸  interrupt rule: approve {request['name']}({request['args']['file_path']})?")
    out = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    print(f"▶  approved → {out['messages'][-2].content}")


if __name__ == "__main__":
    main()
