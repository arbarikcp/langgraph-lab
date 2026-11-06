"""Ch 2: what the add_messages reducer does (append, update by id, delete), called directly.

Run:  python -m chapters.ch02_state_reducers.messages_reducer
"""
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import add_messages


def show(label: str, msgs) -> None:
    print(f"{label:<22}", [f"{m.id}:{m.content}" for m in msgs])


def main() -> None:
    history = [HumanMessage("orders-enricher is lagging", id="h1"),
               AIMessage("Checking job status...", id="a1")]
    show("start", history)

    history = add_messages(history, [AIMessage("Checkpoint FAILED, lag 2.4M", id="a2")])
    show("append (new id)", history)

    history = add_messages(history, [AIMessage("Checkpoint FAILED after 10m timeout", id="a2")])
    show("update (same id)", history)

    history = add_messages(history, [RemoveMessage(id="a1")])
    show("delete (RemoveMessage)", history)


if __name__ == "__main__":
    main()
