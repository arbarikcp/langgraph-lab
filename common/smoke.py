"""Ch 0 smoke check: is Ollama reachable, are the models pulled, does tool calling work?

Run:  python -m common.smoke
"""
import httpx
from langchain.tools import tool

from common.llm import EMBED_MODEL, MODEL, OLLAMA_URL, embeddings, llm


@tool
def flink_job_status(job: str) -> str:
    """Return consumer lag and last checkpoint status for a Flink job."""
    return f"{job}: lag=2,400,000 records; last checkpoint FAILED"  # canned reply; only the tool call matters here


def check_server() -> None:
    try:
        pulled = {m["name"] for m in httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()["models"]}
    except httpx.HTTPError as e:
        raise SystemExit(f"Ollama not reachable at {OLLAMA_URL}: {e}")
    for model in (MODEL, EMBED_MODEL):
        name = model.removeprefix("ollama:")
        ok = name in pulled or f"{name}:latest" in pulled
        print(f"[{'ok' if ok else 'MISSING'}] {name}" + ("" if ok else f"  -> ollama pull {name}"))


def main() -> None:
    print(f"Ollama: {OLLAMA_URL}")
    check_server()

    print("\n1) plain chat")
    print("  ", llm.invoke("Reply with exactly: pong").content.strip())

    print("\n2) tool calling")
    msg = llm.bind_tools([flink_job_status]).invoke("Is the Flink job orders-enricher healthy? Use the tool.")
    print("   tool_calls:", msg.tool_calls or "NONE - this model does not call tools well, try another LLM_MODEL")

    print("\n3) embeddings")
    print("   dims:", len(embeddings.embed_query("checkpoint timeout")))


if __name__ == "__main__":
    main()
