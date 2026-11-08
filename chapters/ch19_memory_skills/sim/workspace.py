"""A fresh copy of the oncall_ws/ template per run, so demos never modify the files in the repo."""
import shutil
import tempfile
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "oncall_ws"


def fresh_workspace() -> str:
    root = tempfile.mkdtemp(prefix="oncall_ws_")
    shutil.copytree(TEMPLATE, root, dirs_exist_ok=True)
    return root
