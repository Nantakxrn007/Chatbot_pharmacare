"""Fix FAST_DIR resolution in experiment_chunking.ipynb cell 3."""
import json
from pathlib import Path

NB_PATH = Path(__file__).parent / "experiment_chunking.ipynb"

PATHS_BLOCK = '''# ── Paths ──────────────────────────────────────────────────────
def find_fast_root() -> Path:
    """หา project root จาก cwd — รันได้ทั้ง d:\\\\Fast และ experiments\\\\chunking"""
    for p in [Path.cwd(), *Path.cwd().parents]:
        if (p / "backend" / "md_chunker.py").is_file():
            return p
    raise FileNotFoundError(
        f"ไม่พบ backend/md_chunker.py — cwd={Path.cwd()}\\n"
        "เปิด notebook จากโฟลเดอร์ Fast หรือ experiments/chunking ก็ได้"
    )

FAST_DIR = find_fast_root()
DATA_DIR = FAST_DIR / "rag" / "data"
BACKEND_DIR = FAST_DIR / "backend"
EXPERIMENTS_CHUNKING_DIR = FAST_DIR / "experiments" / "chunking"

# project root สำหรับ `from backend.xxx` ใน md_chunker
sys.path.insert(0, str(FAST_DIR))
sys.path.insert(0, str(EXPERIMENTS_CHUNKING_DIR))

# Load env
load_dotenv(FAST_DIR / ".env")
GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY")
TYPHOON_API_KEY = os.getenv("TYPHOON_API_KEY")

print(f"✅ FAST_DIR       : {FAST_DIR.resolve()}")
print(f"✅ BACKEND_DIR    : {BACKEND_DIR.resolve()}")
print(f"✅ GOOGLE_API_KEY : {'set' if GOOGLE_API_KEY else '❌ NOT SET'}")
print(f"✅ TYPHOON_API_KEY: {'set' if TYPHOON_API_KEY else '❌ NOT SET'}")

# ── Run 2 config ──────────────────────────────────────────────
RUN_LABEL = "run2_parser_fix"
RUN1_BASELINE = {
    "C_chunks": 72,
    "Source Recall@5": 0.84,
    "Page Recall@5_pdf": 0.12,
    "MRR": 0.553,
    "Group Accuracy": 1.00,
}

print(f"✅ DATA_DIR       : {DATA_DIR.resolve()}")
'''

CELL_3_PREFIX = '''import sys
import os
import json
import re
import csv
import math
from pathlib import Path
from copy import deepcopy
from dataclasses import dataclass, field
from collections import defaultdict

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from dotenv import load_dotenv

'''


CELL_26 = '''import gc
import google.generativeai as genai
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct
)

genai.configure(api_key=GOOGLE_API_KEY)

# Qdrant local — เก็บใน experiments/chunking (แยกจาก production qdrant_db)
EXP_QDRANT_DIR = str(EXPERIMENTS_CHUNKING_DIR / "qdrant_experiment_fair")

# ปิด client เก่าก่อน re-run cell นี้ (กัน lock error)
_old_qclient = globals().get("qclient")
if _old_qclient is not None:
    try:
        _old_qclient.close()
    except Exception:
        pass
    del _old_qclient
    gc.collect()

qclient = QdrantClient(path=EXP_QDRANT_DIR)
print(f"✅ Qdrant experiment client: {EXP_QDRANT_DIR}")

GEMINI_EMBED_MODEL = "models/gemini-embedding-001"
GEMINI_DIM = 3072

def embed_gemini(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT", batch_size: int = 10) -> list[list[float]]:
    """Embed list of texts ด้วย Gemini embedding-001"""
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        result = genai.embed_content(
            model=GEMINI_EMBED_MODEL,
            content=batch,
            task_type=task_type,
        )
        all_embeddings.extend(result["embedding"])
        print(f"  Gemini embedded {min(i+batch_size, len(texts))}/{len(texts)}", end="\\r")
    print()
    return all_embeddings

print("✅ Gemini embed function ready")
'''


def main():
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    nb["cells"][3]["source"] = [CELL_3_PREFIX + PATHS_BLOCK]
    nb["cells"][3]["outputs"] = []
    nb["cells"][3]["execution_count"] = None

    cell4 = "".join(nb["cells"][4]["source"])
    cell4 = cell4.replace("from md_chunker import", "from backend.md_chunker import")
    cell4 = cell4.replace("from patient_group import", "from backend.patient_group import")
    nb["cells"][4]["source"] = [cell4]
    nb["cells"][4]["outputs"] = []
    nb["cells"][4]["execution_count"] = None

    nb["cells"][26]["source"] = [CELL_26]
    nb["cells"][26]["outputs"] = []
    nb["cells"][26]["execution_count"] = None

    NB_PATH.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Fixed paths + imports + Qdrant cell in {NB_PATH}")


if __name__ == "__main__":
    main()
