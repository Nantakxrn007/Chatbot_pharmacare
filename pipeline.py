"""Shim: pipeline moved to rag/pipeline.py — keeps old command working."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

print("[WARN] pipeline.py moved → use: python rag/pipeline.py", file=sys.stderr)
sys.argv[0] = str(Path(__file__).resolve().parent / "rag" / "pipeline.py")
runpy.run_path(str(Path(__file__).resolve().parent / "rag" / "pipeline.py"), run_name="__main__")
