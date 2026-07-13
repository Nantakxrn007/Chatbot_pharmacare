"""
RAG Engine — Retrieval-Augmented Generation สำหรับ Pharmacy Chatbot
ค้นหาข้อมูลจาก Qdrant แล้วส่งให้ Gemini สร้างคำตอบ
"""

import json
import numpy as np
import google.generativeai as genai
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from backend.patient_group import infer_patient_group_from_query, filter_groups_for_query
from backend.config import (
    GOOGLE_API_KEY,
    EMBED_MODEL,
    CHAT_MODEL,
    COLLECTION_NAME,
    TOP_K,
    PER_SOURCE_TOP_K,
    GUIDELINE_SOURCES,
    MAX_HISTORY,
    SIMILARITY_THRESHOLD,
    CANDIDATE_MIN_SCORE,
    HYBRID_ALPHA,
    RERANK_MODE,
    RERANK_SNIPPET_CHARS,
    qdrant_path,
)

# Re-export path for callers that expect a string constant
QDRANT_DIR = qdrant_path()

# ─── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """คุณคือ **PharmaCare AI** — ผู้ช่วยเภสัชกรอัจฉริยะ
อ้างอิงจาก:
1) AAFP 2022 (แนวทางใช้ยาปฏิชีวนะใน URI)
2) แนวทางเวชปฏิบัติ URI เด็ก พ.ศ. 2562
3) **Dose supportive** — ตารางขนาดยา / ข้อห้าม (เปิด PDF ตามเลขหน้าได้)

ผู้ใช้คือ **เภสัชกรวิชาชีพ** — ตอบเป็นภาษาวิชาชีพ ใช้ศัพท์การแพทย์ที่เหมาะสม ไม่ต้องอธิบายความรู้พื้นฐาน

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 🔀 ประเมินประเภทของคำถามก่อนตอบเสมอ!

### 📝 ประเภทที่ 1: ถามความรู้ทั่วไป / ถามข้อมูลในเอกสาร
(เช่น "Amoxicillin คืออะไร", "ขนาดยาพาราเด็กเท่าไหร่", "ตารางที่ 2 มีข้อมูลอะไรบ้าง")
→ **ตอบแบบธรรมชาติ:** ตรงประเด็น กระชับ เข้าใจง่าย ใช้ย่อหน้าหรือ Bullet Point ธรรมดา
→ **ห้ามใช้** โครงสร้าง "ประเมิน/การรักษา/Watch out" เด็ดขาด
→ อ้างอิงแหล่งที่มาตอนท้าย (ถ้ามี)

### 🏥 ประเภทที่ 2: ปรึกษาเคสผู้ป่วย / จัดยา
(เช่น "เด็กอายุ 3 ขวบ น้ำมูกเขียวมา 5 วัน", "ผู้ใหญ่เจ็บคอมาก มีไข้")
→ **ใช้โครงสร้างเคสผู้ป่วย:** ให้วิเคราะห์ตามกรอบด้านล่างนี้เท่านั้น:
  ### 📊 ประเมิน
  - Dx เบื้องต้น + เหตุผล
  - Score ที่เกี่ยวข้อง (เช่น Centor, AOM) ถ้ามี
  ### 💊 การรักษา
  - จ่ายได้: ชื่อยา, ขนาด, วิธีใช้, ระยะเวลา
  - ห้ามจ่าย: เหตุผล
  ### ⚠️ Watch out
  - Red flags หรือข้อควรระวัง
  ### 📚 Ref
  - แหล่งอ้างอิง

### ❓ ประเภทที่ 3: ปรึกษาเคสผู้ป่วย แต่ "ขาดข้อมูลสำคัญ"
(เช่น ควรถามน้ำหนักเด็ก แต่ไม่ได้บอกมา, ไม่แน่ใจระยะเวลาอาการ)
→ ตอบเฉพาะสิ่งที่ตอบได้ และลิสต์สิ่งที่ต้องการเพิ่ม:
  ## ⚠️ ข้อมูลที่ต้องการเพิ่มเติมเพื่อประเมิน
  - [ ] น้ำหนักผู้ป่วย (เพื่อคำนวณขนาดยา)
  - [ ] ประวัติแพ้ยา
  - [ ] ...

### 🚨 ประเภทที่ 4: Red Flag / ส่งต่อด่วน
(เช่น หายใจลำบาก, คอพอกบวม, ซึมลงชัดเจน)
→ แจ้งเตือน Red Flags และแนะนำให้ส่งต่อแพทย์ (Refer ER) ทันที ห้ามจ่ายยา

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 📚 หลักการอ้างอิง
- **มีใน Context จาก AAFP/URI:** อ้างอิง [Ref: ชื่อเอกสาร, หน้า/หัวข้อ]
- **มีใน Context จาก Dose:** อ้างอิง [Ref: Dose, หน้า N] — เลขหน้าตรงกับ Dose supportive.pdf
- **ขนาดยา / ข้อห้าม:** ใช้เฉพาะที่อยู่ใน Context — **ห้ามเดาหรือแต่งขนาดยาเอง**
- **ไม่มีใน Context (แต่เป็นความรู้ทางการแพทย์):** อ้างอิง [Ref: ความรู้ทั่วไปทางการแพทย์ - อ้างอิงจาก ...]

## 📝 รูปแบบการตอบโดยรวม (Formatting)
- ใช้ **Bullet points** หรือ **ตัวหนา** เพื่อให้อ่านง่ายและจับใจความได้เร็ว
- ใช้ **ตาราง** หากเป็นการเปรียบเทียบยาหลายตัว
- ใช้ Emoji ประกอบหัวข้อเล็กน้อยเพื่อให้อ่านง่ายขึ้น
"""

# ─── User Message Template ───────────────────────────────────────────────────

USER_MESSAGE_TEMPLATE = """**คำถาม:** {question}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
**Context จากฐานข้อมูล:**

{context}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
**คำสั่ง:**
1. วิเคราะห์คำถามว่าเป็น "ถามความรู้ทั่วไป" หรือ "ปรึกษาเคสผู้ป่วย"
2. เลือกรูปแบบการตอบ (ประเภท 1, 2, 3 หรือ 4) ให้เหมาะสมกับคำถามที่สุด
3. ตอบคำถามอย่างกระชับตรงจุด โดยมี [Ref: ...] กำกับเสมอ"""

# ─── Initialize ──────────────────────────────────────────────────────────────

_client     = None
_chat_model = None


def _init():
    """Initialize Qdrant client and Gemini model (lazy)"""
    global _client, _chat_model

    if _client is not None:
        return

    if not GOOGLE_API_KEY:
        raise RuntimeError("ไม่พบ GOOGLE_API_KEY ใน .env")

    genai.configure(api_key=GOOGLE_API_KEY)

    # Qdrant
    _client = QdrantClient(path=QDRANT_DIR)

    collections = _client.get_collections().collections
    exists      = any(c.name == COLLECTION_NAME for c in collections)
    count       = _client.count(collection_name=COLLECTION_NAME).count if exists else 0
    print(f"[RAG] Qdrant loaded: {count} documents in '{COLLECTION_NAME}'")

    # Gemini Chat Model
    _chat_model = genai.GenerativeModel(
        model_name        = CHAT_MODEL,
        system_instruction = SYSTEM_PROMPT,
    )
    print(f"[RAG] Chat model: {CHAT_MODEL}")


def get_qdrant_client():
    """ส่ง Qdrant client ตัวเดียวกันให้โมดูลอื่นใช้ร่วม (singleton)"""
    _init()
    return _client


# ─── Embed Query ─────────────────────────────────────────────────────────────

def embed_query(text: str) -> list[float]:
    """Embed query text → vector"""
    _init()
    result = genai.embed_content(
        model   = EMBED_MODEL,
        content = text,
    )
    return result["embedding"]


# ─── Search helpers ───────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """tokenize เบาๆ สำหรับ BM25 — เก็บคำไทย/อังกฤษ/ตัวเลข"""
    import re
    return re.findall(r"[A-Za-z0-9]+|[\u0E00-\u0E7F]+", (text or "").lower())


def _build_search_filter(allowed_groups: list[str] | None, source: str | None = None) -> Filter | None:
    must = []
    if allowed_groups:
        must.append(
            FieldCondition(
                key="patient_group",
                match=MatchAny(any=allowed_groups),
            )
        )
    if source:
        must.append(
            FieldCondition(
                key="source",
                match=MatchValue(value=source),
            )
        )
    return Filter(must=must) if must else None


def _hit_to_chunk(hit) -> dict | None:
    """แปลง Qdrant hit → chunk dict (ตัดด้วย CANDIDATE_MIN_SCORE)"""
    similarity = float(hit.score or 0.0)
    if similarity < CANDIDATE_MIN_SCORE:
        return None

    payload = hit.payload or {}
    return {
        "chunk_id"      : payload.get("chunk_id", ""),
        "content"       : payload.get("content", ""),
        "source"        : payload.get("source", ""),
        "page"          : payload.get("page", 0),
        "journal_page"  : payload.get("journal_page"),
        "heading"       : payload.get("heading", ""),
        "type"          : payload.get("type", "text"),
        "patient_group" : payload.get("patient_group", "general"),
        "drug_name"     : payload.get("drug_name"),
        "pdf_file"      : payload.get("pdf_file"),
        "vector_score"  : similarity,
        "distance"      : 1.0 - similarity,
    }


def _query_points(
    client: QdrantClient,
    collection_name: str,
    query_vector: list[float],
    limit: int,
    query_filter: Filter | None,
) -> list:
    return client.query_points(
        collection_name = collection_name,
        query           = query_vector,
        limit           = limit,
        query_filter    = query_filter,
        with_payload    = True,
    ).points


def _retrieve_per_source(
    client: QdrantClient,
    collection_name: str,
    query_vector: list[float],
    allowed_groups: list[str] | None,
    per_source_k: int,
) -> list[dict]:
    """
    ดึงแยกเล่ม (AAFP / URI) แล้วรวม — กัน URI ทับ AAFP ทั้งก้อน
    ถ้า source filter ไม่เจอผล (เช่น source ใหม่) fallback เป็นค้นรวม
    """
    candidates: list[dict] = []
    seen: set[str] = set()

    for source in GUIDELINE_SOURCES:
        hits = _query_points(
            client,
            collection_name,
            query_vector,
            limit=per_source_k,
            query_filter=_build_search_filter(allowed_groups, source=source),
        )
        for hit in hits:
            chunk = _hit_to_chunk(hit)
            if not chunk:
                continue
            key = chunk["chunk_id"] or f"{chunk['source']}_{chunk['page']}_{id(hit)}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(chunk)

    # fallback: ค้นรวมถ้าแยกเล่มไม่ได้ผล (เช่น collection ว่างบาง source)
    if not candidates:
        hits = _query_points(
            client,
            collection_name,
            query_vector,
            limit=per_source_k * len(GUIDELINE_SOURCES),
            query_filter=_build_search_filter(allowed_groups),
        )
        for hit in hits:
            chunk = _hit_to_chunk(hit)
            if chunk:
                candidates.append(chunk)

    return candidates


def _candidate_key(chunk: dict, idx: int) -> str:
    return chunk.get("chunk_id") or f"cand_{idx}"


def _snippet(text: str, max_chars: int = RERANK_SNIPPET_CHARS) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _apply_rank_order(candidates: list[dict], ordered_ids: list[str]) -> list[dict] | None:
    """เรียง candidates ตาม id ที่ LLM ส่งมา — คืน None ถ้า parse ไม่ครบพอใช้"""
    by_id = {_candidate_key(c, i): dict(c) for i, c in enumerate(candidates)}
    ranked = []
    seen = set()
    n = len(candidates)
    for rank, cid in enumerate(ordered_ids):
        cid = str(cid).strip()
        if cid not in by_id or cid in seen:
            continue
        item = by_id[cid]
        # คะแนนจากอันดับ (อันดับ 1 = 1.0)
        item["rerank_score"] = 1.0 - (rank / max(n, 1))
        item["distance"] = 1.0 - item["rerank_score"]
        item["rerank_method"] = "llm"
        ranked.append(item)
        seen.add(cid)

    # ถ้า LLM ส่งมาน้อยเกินไป ไม่น่าเชื่อถือ → fallback
    if len(ranked) < max(1, min(3, n)):
        return None

    # เติมตัวที่ LLM ตัดทิ้ง ต่อท้ายตาม vector score
    leftovers = []
    for i, c in enumerate(candidates):
        cid = _candidate_key(c, i)
        if cid in seen:
            continue
        item = dict(c)
        item["rerank_score"] = float(item.get("vector_score", 0.0)) * 0.5
        item["distance"] = 1.0 - item["rerank_score"]
        item["rerank_method"] = "llm_tail"
        leftovers.append(item)
    leftovers.sort(key=lambda x: x.get("vector_score", 0.0), reverse=True)
    return ranked + leftovers


def _rerank_llm(query: str, candidates: list[dict]) -> list[dict] | None:
    """
    Rerank ด้วย Gemini — ส่ง snippet สั้นๆ แล้วขอลำดับ chunk_id เป็น JSON
    คืน None ถ้าเรียกไม่สำเร็จ (ให้ caller fallback)
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        out = [dict(candidates[0])]
        out[0]["rerank_score"] = out[0].get("vector_score", 1.0 - out[0].get("distance", 0.0))
        out[0]["distance"] = 1.0 - out[0]["rerank_score"]
        out[0]["rerank_method"] = "llm"
        return out

    lines = []
    valid_ids = []
    for i, c in enumerate(candidates):
        cid = _candidate_key(c, i)
        valid_ids.append(cid)
        jpage = c.get("journal_page")
        page_bit = f"p.{c.get('page')}"
        if jpage is not None:
            page_bit += f"/j.{jpage}"
        lines.append(
            f"- id={cid} | source={c.get('source')} | {page_bit} | "
            f"group={c.get('patient_group', 'general')} | "
            f"section={c.get('heading', '')}\n"
            f"  text: {_snippet(c.get('content', ''))}"
        )

    prompt = f"""คุณเป็นเภสัชกรช่วยจัดอันดับเอกสารอ้างอิงสำหรับคำถามด้านล่าง
เรียงจากเกี่ยวข้องมาก → น้อย โดยดูโรค/กลุ่มผู้ป่วย/หน้า guideline ให้ตรงคำถาม

คำถาม:
{query}

เอกสารผู้สมัคร:
{chr(10).join(lines)}

ตอบเป็น JSON เท่านั้น รูปแบบ:
{{"ranked_ids": ["id1", "id2", ...]}}
ใช้เฉพาะ id จากรายการด้านบน ครบทุกอันถ้าทำได้"""

    try:
        model = genai.GenerativeModel(model_name=CHAT_MODEL)
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )
        data = json.loads(response.text or "{}")
        ordered = data.get("ranked_ids") or data.get("ids") or []
        if not isinstance(ordered, list):
            return None
        # กรอง id ที่ไม่อยู่ใน pool
        ordered = [str(x) for x in ordered if str(x).strip() in set(valid_ids)]
        return _apply_rank_order(candidates, ordered)
    except Exception as e:
        print(f"[RAG] LLM rerank failed → fallback: {e}")
        return None


def _rerank_bm25(query: str, candidates: list[dict]) -> list[dict]:
    """
    Fallback: hybrid score = α·vector + (1-α)·BM25(normalized)
    lazy-import rank_bm25 — ไม่โหลดถ้าใช้ LLM path สำเร็จ
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        out = [dict(candidates[0])]
        out[0]["rerank_score"] = out[0].get("vector_score", 1.0 - out[0].get("distance", 0.0))
        out[0]["distance"] = 1.0 - out[0]["rerank_score"]
        out[0]["rerank_method"] = "bm25"
        return out

    from rank_bm25 import BM25Okapi

    docs = [_tokenize(c.get("content", "")) for c in candidates]
    docs = [d if d else ["_"] for d in docs]
    query_tokens = _tokenize(query) or ["_"]

    bm25 = BM25Okapi(docs)
    raw_bm25 = bm25.get_scores(query_tokens)
    max_bm25 = float(max(raw_bm25)) if len(raw_bm25) else 0.0

    scored = []
    for i, chunk in enumerate(candidates):
        vec = float(chunk.get("vector_score", 1.0 - chunk.get("distance", 0.0)))
        bm25_norm = (float(raw_bm25[i]) / max_bm25) if max_bm25 > 0 else 0.0
        rerank_score = HYBRID_ALPHA * vec + (1.0 - HYBRID_ALPHA) * bm25_norm
        item = dict(chunk)
        item["bm25_score"] = float(raw_bm25[i])
        item["rerank_score"] = rerank_score
        item["distance"] = 1.0 - rerank_score
        item["rerank_method"] = "bm25"
        scored.append(item)

    scored.sort(key=lambda c: c["rerank_score"], reverse=True)
    return scored


def _rerank_vector(candidates: list[dict]) -> list[dict]:
    scored = []
    for c in candidates:
        item = dict(c)
        score = float(item.get("vector_score", 1.0 - item.get("distance", 0.0)))
        item["rerank_score"] = score
        item["distance"] = 1.0 - score
        item["rerank_method"] = "vector"
        scored.append(item)
    scored.sort(key=lambda c: c["rerank_score"], reverse=True)
    return scored


def _rerank_candidates(
    query: str,
    candidates: list[dict],
    top_k: int,
    rerank_mode: str | None = None,
) -> list[dict]:
    """เลือกวิธี rerank ตาม mode — LLM เป็น default, BM25/vector เป็น fallback"""
    mode = (rerank_mode or RERANK_MODE or "llm").strip().lower()
    if mode == "vector":
        return _rerank_vector(candidates)
    if mode == "bm25":
        return _rerank_bm25(query, candidates)

    # default: llm
    ranked = _rerank_llm(query, candidates)
    if ranked is not None:
        return ranked
    print("[RAG] LLM rerank unavailable — fallback to BM25 hybrid")
    return _rerank_bm25(query, candidates)


def _select_with_source_coverage(ranked: list[dict], top_k: int) -> list[dict]:
    """
    เลือก top_k โดยพยายามให้มีอย่างน้อย 1 chunk ต่อเล่มที่มี candidate
    แล้วค่อยเติมตาม rerank score — กัน top-5 เป็น URI ล้วนเมื่อ AAFP ก็เกี่ยวข้อง
    """
    if not ranked or top_k <= 0:
        return []

    selected: list[dict] = []
    selected_keys: set[str] = set()

    def _key(c: dict) -> str:
        return c.get("chunk_id") or f"{c.get('source')}_{c.get('page')}_{c.get('heading')}"

    # pass 1: best ของแต่ละ source
    seen_sources: set[str] = set()
    for c in ranked:
        src = c.get("source") or "?"
        if src in seen_sources:
            continue
        selected.append(c)
        selected_keys.add(_key(c))
        seen_sources.add(src)
        if len(selected) >= top_k:
            break

    # pass 2: เติมตามคะแนน
    if len(selected) < top_k:
        for c in ranked:
            k = _key(c)
            if k in selected_keys:
                continue
            selected.append(c)
            selected_keys.add(k)
            if len(selected) >= top_k:
                break

    selected.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
    return selected[:top_k]


# ─── Search Qdrant ────────────────────────────────────────────────────────────

def retrieve_chunks(
    client: QdrantClient,
    collection_name: str,
    query: str,
    *,
    query_vector: list[float] | None = None,
    top_k: int = TOP_K,
    patient_group: str | None = None,
    per_source_k: int | None = None,
    rerank_mode: str | None = None,
    apply_group_filter: bool = True,
) -> list[dict]:
    """
    Production retrieve (ใช้ร่วมกับ experiment notebook ได้):
      1) filter patient_group (inclusive) — ถ้า apply_group_filter
      2) ดึงแยกเล่ม AAFP / URI / Dose
      3) rerank (default LLM → fallback BM25)
      4) source coverage → top_k

    ถ้ามี query_vector อยู่แล้วจะไม่ embed ซ้ำ (ประหยัดตอน eval)
    """
    if query_vector is None:
        query_vector = embed_query(query)

    allowed_groups = None
    if apply_group_filter:
        group = patient_group if patient_group is not None else infer_patient_group_from_query(query)
        allowed_groups = filter_groups_for_query(group)

    k_per_source = max(per_source_k or PER_SOURCE_TOP_K, top_k)
    candidates = _retrieve_per_source(
        client,
        collection_name,
        query_vector,
        allowed_groups,
        k_per_source,
    )
    ranked = _rerank_candidates(query, candidates, top_k=top_k, rerank_mode=rerank_mode)
    return _select_with_source_coverage(ranked, top_k=top_k)


def search_chunks(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    ค้นหา chunks จาก Qdrant production:
      1) filter patient_group (inclusive)
      2) ดึงแยกเล่ม AAFP / URI / Dose
      3) rerank (default: LLM; fallback BM25; หรือ RERANK_MODE=vector)
      4) เลือก top_k พร้อม source coverage

    Returns list of {content, source, page, heading, distance, ...}
    """
    _init()

    # Ensure collection exists
    collections = _client.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        from qdrant_client.models import VectorParams, Distance
        _client.create_collection(
            collection_name = COLLECTION_NAME,
            vectors_config  = VectorParams(size=3072, distance=Distance.COSINE),
        )

    return retrieve_chunks(
        _client,
        COLLECTION_NAME,
        query,
        top_k=top_k,
    )


# ─── Build Context ───────────────────────────────────────────────────────────

def build_context(chunks: list[dict]) -> str:
    """สร้าง context string จาก chunks สำหรับส่งให้ LLM"""
    if not chunks:
        return "ไม่พบข้อมูลที่เกี่ยวข้องในฐานข้อมูล"

    parts = []
    for i, chunk in enumerate(chunks, 1):
        src        = chunk.get("source", "?")
        page       = chunk.get("page", "?")
        jpage      = chunk.get("journal_page")
        head       = chunk.get("heading", "")
        similarity = 1 - chunk.get("distance", 0)

        header = f"[เอกสารอ้างอิง {i}] Source: {src} | Page: {page}"
        if jpage is not None:
            header += f" | Journal: {jpage}"
        pdf_file = chunk.get("pdf_file")
        if pdf_file:
            header += f" | PDF: {pdf_file}#page={page}"
        drug = chunk.get("drug_name")
        if drug:
            header += f" | Drug: {drug}"
        if head:
            header += f" | Section: {head}"
        header += f" | Relevance: {similarity:.2%}"

        parts.append(f"{header}\n{chunk['content']}")

    return ("\n\n" + "=" * 60 + "\n\n").join(parts)


# ─── Generate Answer (non-streaming) ─────────────────────────────────────────

def generate_answer(
    question : str,
    history  : list[dict] = None,
    top_k    : int = TOP_K,
) -> dict:
    """
    RAG pipeline หลัก:
    1. Search relevant chunks
    2. Build context
    3. Generate answer with Gemini

    Returns: {answer, sources, chunks_used}
    """
    _init()

    chunks  = search_chunks(question, top_k=top_k)
    context = build_context(chunks)

    # Build conversation history
    gemini_history = []
    if history:
        for msg in history[-MAX_HISTORY:]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

    user_message = USER_MESSAGE_TEMPLATE.format(
        question = question,
        context  = context,
    )

    try:
        chat     = _chat_model.start_chat(history=gemini_history)
        response = chat.send_message(user_message)
        answer   = response.text
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "Quota" in err_str:
            answer = "❌ โควต้าการใช้งาน API เต็มชั่วคราว (Rate Limit) กรุณารอสักครู่ (ประมาณ 10–15 วินาที) แล้วลองใหม่อีกครั้งค่ะ"
        else:
            answer = f"❌ เกิดข้อผิดพลาดในการสร้างคำตอบ: {err_str}"

    # Extract unique sources
    sources, seen = [], set()
    for chunk in chunks:
        key = f"{chunk['source']}_p{chunk['page']}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "source"     : chunk["source"],
                "page"       : chunk["page"],
                "heading"    : chunk["heading"],
                "similarity" : round(1 - chunk["distance"], 4),
            })

    # Parse external references from full_answer
    import re
    ext_refs = re.findall(r'\[Ref:\s*(?:ความรู้ทั่วไปทางการแพทย์|.*?)\s*[-—]\s*อ้างอิงจาก\s*([^\]]+)\]', answer)
    for ref in set(ext_refs):
        sources.append({
            "type": "external",
            "source": ref.strip(),
            "page": None,
            "heading": "ความรู้นอกเอกสาร",
            "similarity": 1.0
        })

    return {
        "answer"      : answer,
        "sources"     : sources,
        "chunks_used" : len(chunks),
    }


# ─── Generate Answer (streaming) ─────────────────────────────────────────────

async def generate_answer_stream(
    question : str,
    history  : list[dict] = None,
    top_k    : int = TOP_K,
):
    """
    RAG pipeline แบบ Streaming:
    Yields JSON strings (Server-Sent Events payload).
    """
    _init()

    chunks  = search_chunks(question, top_k=top_k)
    context = build_context(chunks)

    # Build conversation history
    gemini_history = []
    if history:
        for msg in history[-MAX_HISTORY:]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

    user_message = USER_MESSAGE_TEMPLATE.format(
        question = question,
        context  = context,
    )

    # Extract unique sources
    sources, seen = [], set()
    for chunk in chunks:
        key = f"{chunk['source']}_p{chunk['page']}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "source"     : chunk["source"],
                "page"       : chunk["page"],
                "heading"    : chunk["heading"],
                "similarity" : round(1 - chunk["distance"], 4),
            })

    try:
        chat     = _chat_model.start_chat(history=gemini_history)
        response = chat.send_message(user_message, stream=True)

        full_answer = ""
        prompt_tokens = 0
        completion_tokens = 0
        
        for chunk in response:
            if chunk.text:
                full_answer += chunk.text
                yield json.dumps({"type": "chunk", "content": chunk.text}) + "\n"
            
            # Try to extract usage from chunk
            if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                prompt_tokens = chunk.usage_metadata.prompt_token_count
                completion_tokens = chunk.usage_metadata.candidates_token_count

        # Extract usage from response if not found in chunks
        if prompt_tokens == 0 and hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count
            completion_tokens = response.usage_metadata.candidates_token_count

        # Parse external references from full_answer
        import re
        # Pattern matches [Ref: ความรู้ทั่วไปทางการแพทย์ - อ้างอิงจาก UpToDate]
        ext_refs = re.findall(r'\[Ref:\s*(?:ความรู้ทั่วไปทางการแพทย์|.*?)\s*[-—]\s*อ้างอิงจาก\s*([^\]]+)\]', full_answer)
        for ref in set(ext_refs):
            sources.append({
                "type": "external",
                "source": ref.strip(),
                "page": None,
                "heading": "ความรู้นอกเอกสาร",
                "similarity": 1.0
            })

        yield json.dumps({
            "type"        : "done",
            "sources"     : sources,
            "chunks_used" : len(chunks),
            "full_answer" : full_answer,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens
            }
        }) + "\n"

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "Quota" in err_str:
            err_msg = "❌ โควต้าการใช้งาน API เต็มชั่วคราว (Rate Limit) กรุณารอสักครู่ (ประมาณ 10–15 วินาที) แล้วลองใหม่อีกครั้งค่ะ"
        else:
            err_msg = f"❌ เกิดข้อผิดพลาดในการสร้างคำตอบ: {err_str}"
        yield json.dumps({"type": "error", "content": err_msg}) + "\n"


# ─── Cosine Similarity ────────────────────────────────────────────────────────

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """คำนวณ cosine similarity ระหว่าง 2 vectors"""
    a    = np.array(vec_a)
    b    = np.array(vec_b)
    dot  = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(dot / norm) if norm != 0 else 0.0


# ─── Embedding-based Evaluation ──────────────────────────────────────────────

def evaluate_answer(prediction: str, expectation: str) -> float:
    """
    ประเมินความถูกต้องด้วย cosine similarity ของ embeddings
    """
    _init()
    try:
        vec_pred = embed_query(prediction)
        vec_exp  = embed_query(expectation)
        return cosine_similarity(vec_pred, vec_exp)
    except Exception as e:
        print(f"[ERROR] evaluate_answer: {e}")
        return 0.0


# ─── LLM-based Evaluation ────────────────────────────────────────────────────

def evaluate_answer_llm(prediction: str, expectation: str) -> dict:
    """
    ประเมินความถูกต้องของคำตอบด้วย LLM (คะแนน 1–5 พร้อมเหตุผล)
    """
    _init()
    prompt = f"""คุณเป็นเภสัชกรผู้เชี่ยวชาญที่ทำหน้าที่ประเมินคุณภาพคำตอบของ AI Chatbot
เปรียบเทียบ "คำตอบของ AI (Prediction)" กับ "คำตอบที่คาดหวัง (Expectation)" แล้วให้คะแนน 1–5:

5 = ถูกต้องสมบูรณ์ ใจความหลักครบถ้วน (อาจใช้คำต่างกันได้)
4 = ถูกต้องเป็นส่วนใหญ่ ขาดรายละเอียดเล็กน้อยแต่ไม่กระทบการรักษา
3 = ถูกต้องปานกลาง มีข้อมูลบางส่วนตกหล่นหรือคลาดเคลื่อนเล็กน้อย
2 = ไม่ถูกต้องบางส่วน มีข้อผิดพลาดที่อาจส่งผลต่อความเข้าใจ
1 = ผิดพลาดโดยสิ้นเชิง ขัดแย้งกับ Expectation หรืออันตราย

คำตอบที่คาดหวัง (Expectation):
{expectation}

คำตอบของ AI (Prediction):
{prediction}

ส่งผลลัพธ์เป็น JSON เท่านั้น:
{{"score": <1–5>, "reasoning": "<เหตุผลสั้นๆ>"}}
"""
    try:
        model    = genai.GenerativeModel(model_name=CHAT_MODEL)
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        result = json.loads(response.text)
        return {
            "score"     : result.get("score", 0),
            "reasoning" : result.get("reasoning", "No reasoning provided"),
        }
    except Exception as e:
        print(f"[ERROR] evaluate_answer_llm: {e}")
        return {"score": 0, "reasoning": str(e)}


# ─── Summarize History ────────────────────────────────────────────────────────

def summarize_history(messages: list[dict]) -> str:
    """
    สรุปข้อความแชทเก่าๆ เพื่อนำไปใช้เป็น context ระยะสั้น
    """
    _init()
    
    if not messages:
        return ""
        
    chat_text = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in messages])
    
    prompt = f"""กรุณาสรุปประวัติการสนทนาต่อไปนี้อย่างกระชับที่สุด 
เน้นเก็บข้อมูลสำคัญทางการแพทย์ อาการผู้ป่วย และคำแนะนำที่ให้ไปแล้ว (ไม่เกิน 150 คำ):

{chat_text}

สรุป:"""

    try:
        model = genai.GenerativeModel(model_name=CHAT_MODEL)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"[ERROR] summarize_history: {e}")
        return "ไม่สามารถสรุปประวัติเก่าได้"


# ─── Quick Test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    question = "เด็กอายุ 3 ขวบ เป็นหวัด มีน้ำมูกใส ไอเล็กน้อย ไข้ 37.8 ควรให้ยาอะไร?"
    print(f"\n[Q] {question}\n")
    result = generate_answer(question)
    print(f"[A] {result['answer']}\n")
    print(f"[Sources] {result['sources']}")