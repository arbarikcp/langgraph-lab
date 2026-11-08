"""Shared model config. Every chapter gets its chat model and embeddings from here.

Override any default with env vars, e.g.
    export LLM_MODEL=ollama:llama3.1:8b OLLAMA_BASE_URL=http://gpu-box:11434
"""
import os

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("LLM_MODEL", "ollama:qwen3.5:4b")            # must support tool calling
SMALL_MODEL = os.getenv("SMALL_LLM_MODEL", MODEL)              # for summaries / narrow subagents
EMBED_MODEL = os.getenv("EMBED_MODEL", "ollama:nomic-embed-text")
EMBED_DIMS = int(os.getenv("EMBED_DIMS", "768"))               # nomic-embed-text -> 768

# Thinking models (qwen3, deepseek-r1) are slower and noisier with thinking on.
# LLM_THINK=true turns it on; LLM_THINK=default leaves it to the model.
_THINK = {"true": True, "false": False, "default": None}[os.getenv("LLM_THINK", "false").lower()]

# Local tracing: PHOENIX_TRACING=true sends spans to a local Phoenix server
# (start it with `uv run phoenix serve`, UI at http://localhost:6006). Nothing leaves the machine.
if os.getenv("PHOENIX_TRACING", "false").lower() == "true":
    from phoenix.otel import register

    register(
        project_name=os.getenv("PHOENIX_PROJECT", "langgraph-lab"),
        endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces"),
        auto_instrument=True,  # picks up openinference-instrumentation-langchain
        batch=False,           # export each span immediately; short scripts won't lose spans on exit
        verbose=False,
    )


def make_llm(model: str = MODEL, **kwargs):
    """Build an Ollama chat model with the lab defaults (temperature 0, shared base URL)."""
    kwargs.setdefault("temperature", 0)
    return init_chat_model(model, base_url=OLLAMA_URL, reasoning=_THINK, **kwargs)


llm = make_llm()
small_llm = make_llm(SMALL_MODEL)
embeddings = init_embeddings(EMBED_MODEL, base_url=OLLAMA_URL)
