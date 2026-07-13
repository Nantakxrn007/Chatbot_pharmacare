"""
Central config — paths + RAG knobs
==================================
ทุกโมดูลควร import จากที่นี่ ห้าม hardcode ../data หรือ qdrant_db กระจาย

Layout:
  PROJECT_ROOT/
    .env
    backend/
    frontend/
    rag/
      data/          # MD, CSV, PDF, chunks.jsonl, users.json, chat_history.db
      qdrant_db/     # production vector store
      pipeline.py
      embed_log.txt
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ─── Project roots ───────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAG_DIR = PROJECT_ROOT / "rag"
DATA_DIR = RAG_DIR / "data"
QDRANT_DIR = RAG_DIR / "qdrant_db"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
CHUNKS_FILE = DATA_DIR / "chunks.jsonl"
EMBED_LOG_FILE = RAG_DIR / "embed_log.txt"
USERS_FILE = DATA_DIR / "users.json"
CHAT_HISTORY_DB = DATA_DIR / "chat_history.db"
DOSE_CSV = DATA_DIR / "Dose supportive.csv"
TEST_CASE_CSV = DATA_DIR / "test_case.csv"
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)

# ─── Source documents (chunk pipeline) ───────────────────────────────────────

MD_FILES: list[tuple[str, str]] = [
    (str(DATA_DIR / "AAFP.md"), "AAFP"),
    (str(DATA_DIR / "URI.md"), "URI"),
]

GUIDELINE_SOURCES: tuple[str, ...] = ("AAFP", "URI", "Dose")

# Frontend / Ref: source key → PDF filename under DATA_DIR (served at /data/...)
PDF_FILENAMES: dict[str, str] = {
    "AAFP": "AAFP_2022_Original.pdf",
    "URI": "P2_URI.pdf",
    "Dose": "Dose supportive.pdf",
}

DOSE_PDF_NAME = PDF_FILENAMES["Dose"]

# ─── Models & collections ────────────────────────────────────────────────────

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
EMBED_MODEL = os.getenv("EMBED_MODEL", "models/gemini-embedding-001")
CHAT_MODEL = os.getenv("CHAT_MODEL", "models/gemini-3.1-flash-lite")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "pharmacy_docs")
MEMORY_COLLECTION_NAME = os.getenv("MEMORY_COLLECTION_NAME", "chat_memory")

# ─── Retrieve / rerank knobs (env override) ──────────────────────────────────

TOP_K = int(os.getenv("TOP_K") or 5)
PER_SOURCE_TOP_K = int(os.getenv("PER_SOURCE_TOP_K") or 8)
MAX_HISTORY = int(os.getenv("MAX_HISTORY") or 10)
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD") or 0.69)
CANDIDATE_MIN_SCORE = float(os.getenv("CANDIDATE_MIN_SCORE") or 0.55)
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA") or 0.65)
# llm | bm25 | vector — LLM uses CHAT_MODEL
RERANK_MODE = (os.getenv("RERANK_MODE") or "llm").strip().lower()
RERANK_SNIPPET_CHARS = int(os.getenv("RERANK_SNIPPET_CHARS") or 420)

# ─── Auth ────────────────────────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET", "pharmacare-ai-secret-key-change-me-in-production")
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS") or 24)


def qdrant_path() -> str:
    """Qdrant local path as str (client expects str)."""
    return str(QDRANT_DIR)
