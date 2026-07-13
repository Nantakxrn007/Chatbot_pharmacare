"""Run experiment_chunking.ipynb cells (skip PDF inspection)."""
import json
import sys
import traceback
from pathlib import Path

import pandas as pd

FAST_DIR = Path(__file__).resolve().parent.parent.parent
NOTEBOOK = Path(__file__).resolve().parent / "experiment_chunking.ipynb"
sys.path.insert(0, str(FAST_DIR))
sys.path.insert(0, str(FAST_DIR / "experiments" / "chunking"))

SKIP = {9, 10, 11}  # PDF inspection (IPython + pymupdf)
USE_GEMINI_ONLY = True  # skip BGE-M3 / torch

with open(NOTEBOOK, encoding="utf-8") as f:
    nb = json.load(f)

g = {"pd": pd}
for i, cell in enumerate(nb["cells"]):
    if i in SKIP:
        print(f"SKIP cell {i}")
        continue
    if cell["cell_type"] != "code":
        continue
    src = "".join(cell.get("source", []))
    if USE_GEMINI_ONLY and "SentenceTransformer" in src:
        print(f"SKIP cell {i} (BGE-M3)")
        continue
    if not src.strip() or src.strip().startswith("# !pip"):
        continue
    print(f"\n--- cell {i} ---")
    try:
        exec(compile(src, f"cell_{i}", "exec"), g)
    except Exception as e:
        print(f"FAIL cell {i}: {e}")
        traceback.print_exc()
        # LLM demo cell is optional — continue to metrics/summary
        if i == 45:
            print("WARN cell 45 skipped — LLM demo optional")
            continue
        sys.exit(1)

print("\n✅ Experiment notebook completed successfully")
