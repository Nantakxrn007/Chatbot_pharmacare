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
DRUGS_JSON = DATA_DIR / "drugs.json"
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
# ดึงต่อเล่มให้ลึกพอ (12) เพื่อให้ "ตารางขนาดยา/ทางเลือกแพ้ยา" ที่ embed ได้คะแนนต่ำ (มัก rank ~9-10)
# ยังติดอยู่ใน candidate pool -> dose-table-aware pass (_inject_relevant_tables) ถึงจะดึงกลับมาได้
PER_SOURCE_TOP_K = int(os.getenv("PER_SOURCE_TOP_K") or 12)
MAX_HISTORY = int(os.getenv("MAX_HISTORY") or 10)
# ต่ำกว่านี้ = ถือว่า context เกี่ยวข้องต่ำ/นอกขอบเขต (in-scope จริงวัดได้ ~0.69-0.79,
# out-of-scope ~0.61) -> 0.66 ให้ margin กัน in-scope ก้ำกึ่งโดนตัดเป็น weak
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD") or 0.66)
CANDIDATE_MIN_SCORE = float(os.getenv("CANDIDATE_MIN_SCORE") or 0.55)
# ต่ำกว่านี้ = ไม่แสดงเป็นแหล่งอ้างอิงให้ผู้ใช้ (กันอ้าง chunk ที่ไม่เกี่ยวข้อง)
SOURCE_MIN_SIMILARITY = float(os.getenv("SOURCE_MIN_SIMILARITY") or 0.60)
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA") or 0.65)
# llm | bm25 | vector
# Default = "vector": LLM rerank costs 8-18s/query (a full extra API call) but selects the SAME
# key clinical chunks as pure vector ordering — because doc-expansion (whole tables/dose rows) +
# dose-table-aware injection already guarantee the antibiotic/dose tables reach context regardless
# of rerank. Switching to vector removes that call -> ~8-17s faster per clinical answer, no quality
# loss (validated opt7). Set RERANK_MODE=llm to restore the (slow) LLM reranker.
RERANK_MODE = (os.getenv("RERANK_MODE") or "vector").strip().lower()
RERANK_SNIPPET_CHARS = int(os.getenv("RERANK_SNIPPET_CHARS") or 420)

# ─── Generation config (LLM answer tuning) ───────────────────────────────────
# Clinical answers must be deterministic and guideline-faithful, not creative.
# Low temperature reduces hallucination and keeps dose/duration numbers stable
# across repeated asks. Override via env if needed.
CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE") or 0.2)
CHAT_TOP_P = float(os.getenv("CHAT_TOP_P") or 0.9)
CHAT_MAX_OUTPUT_TOKENS = int(os.getenv("CHAT_MAX_OUTPUT_TOKENS") or 2048)


def chat_generation_config() -> dict:
    """Generation config for the main chat answer model (dict form for genai)."""
    return {
        "temperature": CHAT_TEMPERATURE,
        "top_p": CHAT_TOP_P,
        "max_output_tokens": CHAT_MAX_OUTPUT_TOKENS,
    }


# ─── Query rewriting (retrieval recall, latency-free) ────────────────────────
# Static rule-based Thai→English medical-term expansion applied ONLY at
# embed/retrieval time (patient-group inference + rerank still use the raw query
# to preserve true intent). "static" = 0ms; "off" = disable.
QUERY_REWRITE_MODE = (os.getenv("QUERY_REWRITE_MODE") or "static").strip().lower()

# ─── Document-level retrieval expansion ──────────────────────────────────────
# After top_k selection, pull sibling chunks so the LLM sees WHOLE tables and
# WHOLE dose rows (not a single cell). Prevents "data exists but bot says none"
# and truncated dose/duration. Capped so context does not explode.
DOC_EXPANSION = (os.getenv("DOC_EXPANSION") or "true").strip().lower() in ("1", "true", "yes", "on")
MAX_CONTEXT_CHUNKS = int(os.getenv("MAX_CONTEXT_CHUNKS") or 12)

# ─── External URL verification (reference integrity) ─────────────────────────
# Only external reference URLs that actually resolve should be shown to the user.
# Verification runs only on out-of-scope answers (external refs are rare), so it
# does not affect the common in-guideline path.
VERIFY_EXTERNAL_URLS = (os.getenv("VERIFY_EXTERNAL_URLS") or "true").strip().lower() in ("1", "true", "yes", "on")
URL_VERIFY_TIMEOUT = float(os.getenv("URL_VERIFY_TIMEOUT") or 4.0)
# Best-effort content match: fetch the external HTML page and confirm the answer's key terms
# actually appear there; drop the URL on a clear mismatch (keep the source name). Only runs on
# out-of-scope external refs (rare), HTML only (PDFs/unparseable -> trusted via reachability).
VERIFY_EXTERNAL_CONTENT = (os.getenv("VERIFY_EXTERNAL_CONTENT") or "true").strip().lower() in ("1", "true", "yes", "on")

# ─── Query embedding cache (latency) ─────────────────────────────────────────
# Cache query embeddings so repeated/identical questions skip a network round-trip.
EMBED_CACHE_SIZE = int(os.getenv("EMBED_CACHE_SIZE") or 256)

# ─── Conversation continuity (anti-drift context management) ─────────────────
# Follow-up questions that name no disease/age drift toward whichever chunks are
# semantically closest (often the wrong disease/age group). The case anchor
# derives the active case (disease + age group) from recent history and scopes
# retrieval + generation to it. Disable with CASE_ANCHOR=off.
CASE_ANCHOR = (os.getenv("CASE_ANCHOR") or "on").strip().lower() in ("1", "true", "yes", "on")
# Recent window: messages (not turns) sent verbatim to the LLM. Older content is
# carried by immutable compaction blocks + semantic recall instead of raw dumps.
RECENT_WINDOW = int(os.getenv("RECENT_WINDOW") or 10)
# Compaction: summarize this many oldest raw messages into one immutable block
# once the session exceeds COMPACT_THRESHOLD raw messages. Blocks are never
# re-summarized (no summary-of-summary drift).
COMPACT_THRESHOLD = int(os.getenv("COMPACT_THRESHOLD") or 50)
COMPACT_BATCH = int(os.getenv("COMPACT_BATCH") or 30)
# Max compaction blocks passed to the LLM per question (most relevant first).
SUMMARY_BLOCK_MAX = int(os.getenv("SUMMARY_BLOCK_MAX") or 3)
# Semantic recall of old raw messages: only when the session is long enough that
# the recent window no longer covers everything, and only hits above this floor.
MEMORY_MIN_SIMILARITY = float(os.getenv("MEMORY_MIN_SIMILARITY") or 0.55)
MEMORY_RECALL_TOP_K = int(os.getenv("MEMORY_RECALL_TOP_K") or 3)
MEMORY_MIN_SESSION_MESSAGES = int(os.getenv("MEMORY_MIN_SESSION_MESSAGES") or 12)

# ─── Intent / retrieval gate (context-bleeding guard) ────────────────────────
# Social messages (thanks/greeting/acknowledgement) must NOT trigger retrieval or
# re-answer the previous clinical case. Rule-based (0ms) + conservative: anything
# that looks clinical stays clinical. Disable with INTENT_GATE=off.
INTENT_GATE = (os.getenv("INTENT_GATE") or "on").strip().lower() in ("1", "true", "yes", "on")

# ─── Transient-error retry (LLM 503 / overloaded) ────────────────────────────
# Gemini occasionally returns 503 "high demand". Retry a couple of times with a
# short backoff before surfacing the error, so a transient spike does not fail
# the whole answer. Kept small so the user never waits long.
LLM_RETRY_MAX = int(os.getenv("LLM_RETRY_MAX") or 2)          # extra attempts after the first
LLM_RETRY_BACKOFF = float(os.getenv("LLM_RETRY_BACKOFF") or 1.2)  # seconds, grows linearly

# ─── Auth ────────────────────────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET", "pharmacare-ai-secret-key-change-me-in-production")
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS") or 24)


def qdrant_path() -> str:
    """Qdrant local path as str (client expects str)."""
    return str(QDRANT_DIR)
