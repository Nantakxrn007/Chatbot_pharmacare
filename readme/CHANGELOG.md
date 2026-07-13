# Changelog — Session 12–13 Jul 2026

สรุปสิ่งที่แก้/ทำในรอบนี้ (Strategy C → Dose → **จัดโครงสร้าง rag/ + config สำหรับ handoff**)

---

## Repo layout handoff (13 Jul 2026, คืน)

- โซนใหม่ **`rag/`**: `data/` + `qdrant_db/` + `pipeline.py` + `embed_log.txt`
- **`backend/config.py`**: path + โมเดล + RAG knobs จุดเดียว
- Docker: mount `rag/data`, `rag/qdrant_db`, **`backend/`** (แก้โค้ดแล้ว restart พอ)
- เอกสาร: `HANDOFF_Uncle_Jack.md`
- Verify: container `GUIDELINE_SOURCES=(AAFP,URI,Dose)` + Qdrant **229** docs

---

## Dose supportive layer (13 Jul 2026, รอบเย็น)

- `backend/dose_chunker.py` — 1 แถวยา → adult/pediatric (+ renal/warnings); `source=Dose`
- **`page` จากคอลัมน์ Page ใน CSV = เลขหน้า `Dose supportive.pdf`** (เปิด `#page=N` ได้)
- `rag/pipeline.py` รวม Dose CSV หลัง AAFP/URI → **229 chunks** (38 + 94 + 97)
- `rag_engine.py` / `config.py`: `GUIDELINE_SOURCES = (AAFP, URI, Dose)`
- Frontend: source มี `DOSE` → เปิด `Dose supportive.pdf`
- Smoke: ถาม Paracetamol ผู้ใหญ่ → Dose page **13** ขึ้นอันดับ 1 (LLM rerank)

### LLM rerank — ใช้ตัวไหน

| รายการ | ค่า |
|---|---|
| Mode (default) | `RERANK_MODE=llm` |
| โมเดล | **`models/gemini-3.1-flash-lite`** (= `CHAT_MODEL` ใน `backend/config.py`) |
| ทำอะไร | จัดอันดับ candidate จากทุกเล่ม → JSON `ranked_ids` |
| Fallback | ล้มเหลว → BM25; หรือตั้ง `RERANK_MODE=bm25` / `vector` |

---

## ผลทดลองล่าสุด — Run 3 (13 Jul 2026)

- Eval **25 cases** (จาก `test_case.csv` 57 แถว; กรอง AAFP+URI)
- Retrieve = production: filter → per-source (`PER_SOURCE_TOP_K=8`) → LLM rerank
- Strategy **C**: Source Recall **1.00** | MRR **0.695** | Page (journal) **0.64** | Group **1.00**

---

## Production — Chunking & Retrieval (Strategy C)

### `backend/md_chunker.py`
- Strategy C: section buffer, overlap **80**, table isolation, prefix, `patient_group`
- **parser fix** `</table>` + **journal_page**

### `backend/embed_to_qdrant.py`
- payload: `patient_group`, `tokens_approx`, `journal_page`, `drug_name`, `pdf_file`

### `backend/rag_engine.py`
- inclusive `patient_group` filter
- ดึงแยกเล่ม AAFP / URI / **Dose** + source coverage
- **LLM rerank** default (`RERANK_MODE=llm`) ด้วย **gemini-3.1-flash-lite**; fallback BM25 / vector
- `PER_SOURCE_TOP_K=8` (ขยาย pool)
- `retrieve_chunks(client, collection, ...)` ใช้ร่วมกับ experiment notebook ได้

### `pipeline.py`
- `--reset` → **229 chunks** (AAFP 38 + URI 94 + Dose 97)

### `requirements.txt`
- เพิ่ม `rank_bm25`

---

## Documentation

- `README.md` — สรุปแผน 9 ข้อ + ผล Run 1–3 + Dose + โมเดล LLM rerank
- `plan_2.md` / `STRATEGY_C_explained.md` — อัปเดตสถานะ + ตัวเลข Run 3
- `CHANGELOG.md` (ไฟล์นี้)

---

## ยังไม่ได้ทำ (out of scope)

- วางไฟล์ `data/Dose supportive.pdf` ในโฟลเดอร์ (ต้องมีถึงจะเปิดจาก UI ได้)
- Cross-encoder / heavy local rerank model
- Table row-splitting (ตารางใหญ่ยังเป็น chunk เดียว)
- Migrate `google.generativeai` → `google.genai`
- Safety gate เด็ก <4 ปี
- Frontend แสดง `journal_page`
- mL calculator
- วัดคุณภาพคำตอบ LLM end-to-end
