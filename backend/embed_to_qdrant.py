"""
Embed chunks.jsonl → ChromaDB
ใช้ gemini-embedding-001 + Google API

Install:
    pip install chromadb google-generativeai python-dotenv

Usage:
    วาง chunks.jsonl ไว้ในโฟลเดอร์เดียวกัน แล้วรัน
    python embed_to_chroma.py
"""

import os
import sys
import io
import json
import time
from pathlib import Path
from datetime import datetime
import google.generativeai as genai

from backend.config import (
    GOOGLE_API_KEY,
    EMBED_MODEL,
    COLLECTION_NAME,
    CHUNKS_FILE as _CHUNKS_FILE,
    EMBED_LOG_FILE,
    qdrant_path,
)

# ─── Config ──────────────────────────────────────────────────────────────────

CHUNKS_FILE = str(_CHUNKS_FILE)
CHROMA_DIR = qdrant_path()  # legacy param name; value is Qdrant path
LOG_FILE = str(EMBED_LOG_FILE)

# Rate limit gemini-embedding-001
DELAY_BETWEEN = 0.1
BATCH_SIZE = 50
RETRIES = 5

# ─── Logger ──────────────────────────────────────────────────────────────────

def log(level, msg):
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def log_info(msg):  log("INFO ", msg)
def log_ok(msg):    log("OK   ", msg)
def log_warn(msg):  log("WARN ", msg)
def log_error(msg): log("ERROR", msg)
def log_sep():
    line = "-" * 55
    print(line, flush=True)
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ─── Embed function ──────────────────────────────────────────────────────────

def embed_text(text: str) -> list[float] | None:
    """
    embed text 1 chunk → vector
    retry + exponential backoff

    Rate limit handling:
    ┌──────────────────┬───────────────────────────────────────┐
    │ Error            │ Action                                │
    ├──────────────────┼───────────────────────────────────────┤
    │ 429 Rate limit   │ backoff: 10→20→40→80→160s            │
    │ 401 Key ผิด      │ หยุดทันที                             │
    │ Network/Timeout  │ รอ 10s retry                         │
    │ ล้มเหลว 5 ครั้ง  │ return None                          │
    └──────────────────┴───────────────────────────────────────┘
    """
    for attempt in range(1, RETRIES + 1):
        try:
            result = genai.embed_content(
                model   = EMBED_MODEL,
                content = text,
            )
            return result["embedding"]

        except Exception as e:
            err = str(e).lower()

            # 401 — key ผิด หยุดทันที
            if "401" in str(e) or "api key" in err or "unauthorized" in err:
                log_error(f"API Key ผิดหรือไม่มีสิทธิ์ — หยุดทันที: {e}")
                raise SystemExit(1)

            # 429 — rate limit
            elif "429" in str(e) or "quota" in err or "rate" in err:
                wait = 10 * (2 ** (attempt - 1))
                log_warn(f"Rate limit (429) attempt {attempt}/{RETRIES} — รอ {wait}s")
                time.sleep(wait)

            # timeout / network
            elif any(k in err for k in ["timeout", "connection", "network", "socket"]):
                log_warn(f"Network error attempt {attempt}/{RETRIES} — รอ 10s: {e}")
                time.sleep(10)

            # อื่นๆ
            else:
                log_warn(f"Error attempt {attempt}/{RETRIES} — รอ 10s: {e}")
                time.sleep(10)

            if attempt == RETRIES:
                log_error(f"ล้มเหลวครบ {RETRIES} ครั้ง — ข้าม chunk นี้ไป")
                return None

    return None

# ─── Main ────────────────────────────────────────────────────────────────────

def embed_to_qdrant(
    chunks_file     : str = CHUNKS_FILE,
    chroma_dir      : str = CHROMA_DIR,
    collection_name : str = COLLECTION_NAME,
):
    # เคลียร์ log
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== Embed Log {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")

    # ── ตรวจสอบ API Key ───────────────────────────────────────────────────
    if not GOOGLE_API_KEY:
        log_error("ไม่พบ GOOGLE_API_KEY ใน .env — หยุดทำงาน")
        raise SystemExit(1)
    genai.configure(api_key=GOOGLE_API_KEY)
    log_info(f"API Key: ...{GOOGLE_API_KEY[-6:]} (6 ตัวท้าย)")

    # ── ตรวจสอบ chunks file ───────────────────────────────────────────────
    if not Path(chunks_file).exists():
        log_error(f"ไม่พบไฟล์ {chunks_file} — รัน md_chunker.py ก่อนนะครับ")
        raise SystemExit(1)

    # ── โหลด chunks ───────────────────────────────────────────────────────
    with open(chunks_file, encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]
    log_info(f"โหลด chunks: {len(chunks)} chunks จาก {chunks_file}")

    # ── เปิด Qdrant ─────────────────────────────────────────────────────
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Distance, VectorParams, PointStruct
    import uuid

    client = QdrantClient(path=chroma_dir)
    
    # Determine vector size dynamically using the first actual chunk
    if not chunks:
        log_error("No chunks found.")
        return
    sample_vector = embed_text(chunks[0]["content"])
    if not sample_vector:
        log_error("Failed to get sample vector to determine dimension.")
        return
    v_size = len(sample_vector)
    
    # Check if collection exists, if not create it
    collections = client.get_collections().collections
    if not any(c.name == collection_name for c in collections):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=v_size, distance=Distance.COSINE),
        )
    log_info(f"Qdrant: {chroma_dir} | collection: {collection_name}")

    # ── Resume: ข้าม chunk ที่ embed ไปแล้ว ──────────────────────────────
    # For Qdrant, we can scroll to get existing chunk_id from payload if needed
    # But to keep it simple, we'll embed all for now, or just trust the reset
    done_ids = set()
    todo     = [c for c in chunks if c["chunk_id"] not in done_ids]
    log_info(f"embed แล้ว: {len(done_ids)} | ที่ต้องทำ: {len(todo)}")

    if not todo:
        log_ok("ทุก chunk embed แล้ว ไม่มีอะไรต้องทำ")
        return

    log_sep()
    log_info(f"[Model]  {EMBED_MODEL}")
    log_info(f"[Delay]  {DELAY_BETWEEN}s ต่อ request")
    log_info(f"[Time]   ~{len(todo) * DELAY_BETWEEN / 60:.1f} นาที")
    log_sep()

    # ── Embed loop ────────────────────────────────────────────────────────
    success = 0
    fail_ids = []
    t_start  = time.time()

    for i, chunk in enumerate(todo, 1):
        cid     = chunk["chunk_id"]
        content = chunk["content"]

        log_info(f"[{i}/{len(todo)}] embed: {cid} ({chunk['tokens_approx']} tokens) ...")

        vector = embed_text(content)

        if vector is None:
            fail_ids.append(cid)
            log_error(f"[{i}/{len(todo)}] FAIL: {cid}")
            continue

        # บันทึกลง Qdrant ทีละ chunk (auto-persist)
        point_id = str(uuid.uuid4())
        client.upsert(
            collection_name=collection_name,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "chunk_id"     : cid,
                        "content"      : content,
                        "source"       : chunk.get("source", ""),
                        "page"         : chunk.get("page", 0),
                        "journal_page" : chunk.get("journal_page"),
                        "heading"      : chunk.get("heading", ""),
                        "type"         : chunk.get("type", "text"),
                        "patient_group": chunk.get("patient_group", "general"),
                        "tokens_approx": chunk.get("tokens_approx", 0),
                        "drug_name"    : chunk.get("drug_name"),
                        "pdf_file"     : chunk.get("pdf_file"),
                    }
                )
            ]
        )

        success += 1
        elapsed  = time.time() - t_start
        avg      = elapsed / success
        eta      = (len(todo) - i) * avg / 60
        log_ok(f"[{i}/{len(todo)}] OK: {cid} | รวม {success} | ETA ~{eta:.1f} min")

        # delay rate limit
        if i < len(todo):
            time.sleep(DELAY_BETWEEN)

        # progress ทุก BATCH_SIZE
        if i % BATCH_SIZE == 0:
            log_sep()
            log_info(f"[PROGRESS] {i}/{len(todo)} chunks | สำเร็จ {success} | ล้มเหลว {len(fail_ids)}")
            log_sep()

    # ── สรุป ──────────────────────────────────────────────────────────────
    total_time = (time.time() - t_start) / 60
    total_docs = client.count(collection_name=collection_name).count

    log_sep()
    log_ok(f"[DONE] embed {success}/{len(todo)} chunks | ใช้เวลา {total_time:.1f} นาที")
    log_info(f"[DB]   Qdrant มีทั้งหมด {total_docs} docs ใน collection '{collection_name}'")
    if fail_ids:
        log_error(f"[FAIL] chunk ที่ล้มเหลว ({len(fail_ids)}): {fail_ids}")
        log_warn(f"       run ใหม่ได้เลย จะ resume ต่อเองครับ")
    log_info(f"[LOG]  ดูทั้งหมดที่: {LOG_FILE}")

