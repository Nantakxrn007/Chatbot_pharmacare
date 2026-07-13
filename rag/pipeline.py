"""
Pipeline: MD/CSV -> Chunk -> Embed -> Qdrant
==========================================
Run from project root (d:\\Fast):

    python rag/pipeline.py              # chunk + embed
    python rag/pipeline.py --chunk-only # chunk only
    python rag/pipeline.py --embed-only # embed only (requires chunks.jsonl)
    python rag/pipeline.py --reset      # delete old DB, re-chunk + re-embed

Paths come from backend.config (rag/data, rag/qdrant_db).
"""

from __future__ import annotations

import sys
import shutil
from pathlib import Path

# Allow `python rag/pipeline.py` from repo root
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.config import (
    DATA_DIR,
    QDRANT_DIR,
    CHUNKS_FILE,
    MD_FILES,
    DOSE_CSV,
    COLLECTION_NAME,
    DOSE_PDF_NAME,
)
from backend.md_chunker import ChunkConfig, chunk_md_file, save_chunks_jsonl, print_summary
from backend.dose_chunker import chunk_dose_csv, print_dose_summary
from backend.embed_to_qdrant import embed_to_qdrant

# ─── Chunk Config ────────────────────────────────────────────────────────────

cfg = ChunkConfig(
    max_tokens=500,
    overlap_tokens=80,  # Strategy C
    chars_per_token=4,
    max_heading_level=4,
    max_heading_chars=150,
    skip_figure=True,
    skip_page_number=True,
    skip_tags=[],
    skip_line_patterns=[
        r"Downloaded from",
        r"CME This clinical content",
        r"Author disclosure:",
        r"Patient information:",
        r"All other rights reserved",
    ],
    include_prefix=True,
    table_chunk_mode="full",
)

QDRANT_DIR_STR = str(QDRANT_DIR)
CHUNKS_FILE_STR = str(CHUNKS_FILE)


def run_chunk():
    all_chunks = []
    for md_path, source_name in MD_FILES:
        if not Path(md_path).exists():
            print(f"[WARN] File not found: {md_path} -- skipping")
            continue
        print(f"\n[CHUNK] {Path(md_path).name} (source: {source_name}) ...")
        chunks = chunk_md_file(md_path, source_name=source_name, config=cfg)
        print_summary(chunks)
        all_chunks.extend(chunks)

    if DOSE_CSV.is_file():
        print(f"\n[CHUNK] {DOSE_CSV.name} (source: Dose, PDF: {DOSE_PDF_NAME}) ...")
        dose_chunks = chunk_dose_csv(DOSE_CSV)
        print_dose_summary(dose_chunks)
        all_chunks.extend(dose_chunks)
    else:
        print(f"[WARN] Dose CSV not found: {DOSE_CSV} -- skipping")

    if all_chunks:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        save_chunks_jsonl(all_chunks, CHUNKS_FILE_STR)
        print(f"\n[DONE] Total {len(all_chunks)} chunks -> {CHUNKS_FILE_STR}")
    else:
        print("[ERROR] No chunks produced -- check your .md / Dose CSV files")
    return all_chunks


def run_embed():
    if not Path(CHUNKS_FILE_STR).exists():
        print(f"[ERROR] {CHUNKS_FILE_STR} not found -- run chunk first")
        return
    QDRANT_DIR.mkdir(parents=True, exist_ok=True)
    embed_to_qdrant(
        chunks_file=CHUNKS_FILE_STR,
        chroma_dir=QDRANT_DIR_STR,
        collection_name=COLLECTION_NAME,
    )


def run_reset():
    if QDRANT_DIR.exists():
        shutil.rmtree(QDRANT_DIR)
        print(f"[RESET] Deleted {QDRANT_DIR}")
    if Path(CHUNKS_FILE_STR).exists():
        Path(CHUNKS_FILE_STR).unlink()
        print(f"[RESET] Deleted {CHUNKS_FILE_STR}")
    run_chunk()
    run_embed()


if __name__ == "__main__":
    args = set(sys.argv[1:])

    if "--reset" in args:
        run_reset()
    elif "--chunk-only" in args:
        run_chunk()
    elif "--embed-only" in args:
        run_embed()
    else:
        run_chunk()
        run_embed()
