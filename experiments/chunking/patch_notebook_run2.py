"""Patch experiment_chunking.ipynb for Run 2 (parser fix + journal_page metrics)."""
import json
from pathlib import Path

NB_PATH = Path(__file__).parent / "experiment_chunking.ipynb"

CELL_0 = """# 🧪 Experiment: Chunking Strategy Comparison
## Fast Project — Retrieval Accuracy Benchmark

> **Run 2 (Jul 2026):** Parser fix (ตาราง 7/7 AAFP) + `journal_page` metric  
> **Run 1 baseline:** 72 chunks | Page Recall 0.12 (เทียบ `page` PDF กับเลขวารสาร — ไม่ fair)

**เป้าหมาย:** เปรียบเทียบ 3 Chunking Strategies (Gemini only, fair eval AAFP+URI)

| Strategy | คำอธิบาย | Run 2 source |
|---|---|---|
| **A** (Baseline) | Recursive split + table parser เก่า (บั๊กกลืนตาราง) | `legacy_chunker.py` |
| **B** (Semantic) | Split บน Heading + Prefix | notebook `chunk_v2_semantic` |
| **C** (Production) | Strategy C + parser fix + `patient_group` + `journal_page` | `md_chunker.chunk_md_file()` |

**Metrics ใหม่ Run 2:**
- `Page Recall@5 (journal)` — AAFP เทียบ `journal_page` (628–636) ตาม test case
- `Page Recall@5 (pdf)` — เทียบ `page` (PDF) แบบ Run 1 (อ้างอิง)
"""

CELL_3_PATHS = """# ── Paths ──────────────────────────────────────────────────────
def find_fast_root() -> Path:
    \"\"\"หา project root จาก cwd — รันได้ทั้ง d:\\\\Fast และ experiments\\\\chunking\"\"\"
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
"""

CELL_3_EXTRA = CELL_3_PATHS  # kept for patch_cell3 compat

CELL_11 = """---
## Section 2: Strategy A — Baseline (Legacy)

ใช้ `legacy_chunker.py` — recursive split + table parser เก่า (ก่อน parser fix Jul 2026)
"""

CELL_12 = """# ── Strategy A: legacy baseline ───────────────────────────────
from legacy_chunker import chunk_strategy_a

cfg_A = ChunkConfig(overlap_tokens=100)  # config เดิมก่อน Strategy C

chunks_A_aafp = chunk_strategy_a(str(DATA_DIR / "AAFP.md"), source_name="AAFP", config=cfg_A)
chunks_A_uri  = chunk_strategy_a(str(DATA_DIR / "URI.md"),  source_name="URI",  config=cfg_A)
chunks_A = chunks_A_aafp + chunks_A_uri

print(f"Strategy A — AAFP: {len(chunks_A_aafp)} chunks | URI: {len(chunks_A_uri)} chunks | Total: {len(chunks_A)}")
aafp_tables_A = sum(1 for c in chunks_A_aafp if c["type"] == "table_html")
print(f"  AAFP table_html: {aafp_tables_A} (Run2 production ควรได้ 7)")
print_summary(chunks_A)
"""

CELL_19 = """---
## Section 4: Strategy C — Production (`md_chunker.py`)

ใช้ production code โดยตรง (Strategy C + parser fix + `journal_page`)  
Fair eval: AAFP + URI เท่านั้น (ยังไม่รวม Dose supportive)
"""

CELL_20 = """# ── Strategy C: production chunk_md_file ─────────────────────
cfg_C = ChunkConfig(
    max_tokens=500,
    overlap_tokens=80,
    chars_per_token=4,
    include_prefix=True,
    table_chunk_mode="full",
)

chunks_C_aafp = chunk_md_file(str(DATA_DIR / "AAFP.md"), source_name="AAFP", config=cfg_C)
chunks_C_uri  = chunk_md_file(str(DATA_DIR / "URI.md"),  source_name="URI",  config=cfg_C)
chunks_C = chunks_C_aafp + chunks_C_uri

print(f"Strategy C — AAFP: {len(chunks_C_aafp)} chunks | URI: {len(chunks_C_uri)} chunks | Total: {len(chunks_C)}")
aafp_tables_C = sum(1 for c in chunks_C_aafp if c["type"] == "table_html")
print(f"  AAFP table_html: {aafp_tables_C} (คาดหวัง 7)")
from collections import Counter
print(f"  patient_group: {dict(Counter(c.get('patient_group','?') for c in chunks_C))}")
print(f"  มี journal_page: {sum(1 for c in chunks_C if 'journal_page' in c)} chunks")
print_summary(chunks_C)
"""

CELL_28 = """def create_or_reset_collection(client: QdrantClient, name: str, dim: int):
    \"\"\"สร้าง collection ใหม่ (ลบเก่าถ้ามี)\"\"\"
    existing = [c.name for c in client.get_collections().collections]
    if name in existing:
        client.delete_collection(name)
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"  ✅ Collection '{name}' created (dim={dim})")


def upsert_chunks(client: QdrantClient, collection_name: str, chunks: list[dict], vectors: list[list[float]]):
    \"\"\"Upsert chunks + vectors เข้า Qdrant\"\"\"
    points = [
        PointStruct(
            id=i,
            vector=vectors[i],
            payload={
                "chunk_id"     : chunks[i]["chunk_id"],
                "source"       : chunks[i]["source"],
                "page"         : chunks[i]["page"],
                "journal_page" : chunks[i].get("journal_page"),
                "heading"      : chunks[i]["heading"],
                "type"         : chunks[i]["type"],
                "content"      : chunks[i]["content"],
                "tokens_approx": chunks[i]["tokens_approx"],
                "patient_group": chunks[i].get("patient_group", "general"),
            },
        )
        for i in range(len(chunks))
    ]
    batch_size = 100
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=collection_name, points=points[i:i + batch_size])
    print(f"  ✅ Upserted {len(points)} chunks → '{collection_name}'")


print("✅ Helper functions ready")
"""

CELL_37 = """from qdrant_client.models import Filter, FieldCondition, MatchAny

GUIDELINE_SOURCES = {"AAFP", "URI"}


def _normalize_source(source: str) -> str:
    if not source:
        return ""
    return str(source).replace(".md", "").strip()


def _page_for_match(r: dict, exp_source: str) -> int | None:
    \"\"\"AAFP → journal_page (เลขวารสาร), URI/อื่น → pdf page\"\"\"
    if exp_source == "AAFP" and r.get("journal_page") is not None:
        try:
            return int(r["journal_page"])
        except (TypeError, ValueError):
            pass
    try:
        return int(r.get("page"))
    except (TypeError, ValueError):
        return None


def _page_matches(page_val, expected_pages: list[int]) -> bool:
    if not expected_pages or page_val is None:
        return False
    try:
        page = int(page_val)
    except (TypeError, ValueError):
        return False
    if page in expected_pages:
        return True
    return any(abs(page - exp) <= 1 for exp in expected_pages)


def retrieve_vector(client: QdrantClient, collection: str, query_vec: list[float],
                    top_k: int = 5, patient_group_filter: str = None) -> list[dict]:
    \"\"\"Vector search — filter แบบ inclusive (general/both ยังค้นหาได้)\"\"\"
    query_filter = None
    allowed_groups = filter_groups_for_query(patient_group_filter)
    if allowed_groups:
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="patient_group",
                    match=MatchAny(any=allowed_groups),
                )
            ]
        )

    results = client.query_points(
        collection_name=collection,
        query=query_vec,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    ).points

    return [
        {
            "chunk_id"     : r.payload["chunk_id"],
            "source"       : _normalize_source(r.payload["source"]),
            "page"         : r.payload["page"],
            "journal_page" : r.payload.get("journal_page"),
            "heading"      : r.payload["heading"],
            "patient_group": r.payload.get("patient_group", "general"),
            "score"        : r.score,
            "content"      : r.payload["content"][:200],
        }
        for r in results
        if _normalize_source(r.payload.get("source")) in GUIDELINE_SOURCES
    ]


def compute_metrics(retrieved: list[dict], expected: dict, k: int = 5) -> dict:
    \"\"\"Recall@k, MRR — Page Recall แยก journal (AAFP) vs pdf\"\"\"
    exp_source = expected.get("expected_source")
    exp_pages  = expected.get("expected_pages") or []
    exp_page   = expected.get("expected_page")
    exp_group  = expected.get("patient_group_query")

    if exp_page and exp_page not in exp_pages:
        exp_pages = [exp_page] + exp_pages

    source_matches = [r for r in retrieved if r["source"] == exp_source]

    page_matches_journal = [
        r for r in source_matches
        if _page_matches(_page_for_match(r, exp_source), exp_pages)
    ]
    page_matches_pdf = [
        r for r in source_matches
        if _page_matches(r.get("page"), exp_pages)
    ]
    group_matches = [
        r for r in retrieved
        if groups_compatible(exp_group, r.get("patient_group", "general"))
    ]

    recall_source = 1 if source_matches else 0
    recall_page_journal = (1 if page_matches_journal else 0) if exp_pages else None
    recall_page_pdf = (1 if page_matches_pdf else 0) if exp_pages else None
    recall_group = 1 if group_matches else 0

    mrr = 0.0
    for rank, r in enumerate(retrieved, 1):
        if r["source"] == exp_source:
            mrr = 1.0 / rank
            break

    top = retrieved[0] if retrieved else {}
    return {
        "recall_source"        : recall_source,
        "recall_page"          : recall_page_journal,  # default = journal (Run 2)
        "recall_page_journal"  : recall_page_journal,
        "recall_page_pdf"      : recall_page_pdf,
        "patient_group_correct": recall_group,
        "mrr"                  : mrr,
        "top1_source"          : top.get("source"),
        "top1_page"            : top.get("page"),
        "top1_journal_page"    : top.get("journal_page"),
        "top1_group"           : top.get("patient_group"),
    }

print("✅ Retrieval metric functions ready (Run 2: journal_page + pdf page)")
"""

CELL_39 = """# รัน Retrieval Evaluation (Gemini only)
CONFIGS = [
    {"name": "A_gemini", "collection": "exp_A_gemini"},
    {"name": "B_gemini", "collection": "exp_B_gemini"},
    {"name": "C_gemini", "collection": "exp_C_gemini"},
]

all_results = {}

for cfg in CONFIGS:
    print(f"\\n🔍 Evaluating: {cfg['name']}...")
    results_per_case = []

    for _, row in eval_df.iterrows():
        query = row["input"]
        expected = {
            "expected_source"    : row["expected_source"],
            "expected_page"      : row["expected_page"],
            "expected_pages"     : row.get("expected_pages", []),
            "patient_group_query": row["patient_group_query"],
        }

        q_vec = embed_gemini([query], task_type="RETRIEVAL_QUERY")[0]
        use_filter = row["patient_group_query"] if "C" in cfg["name"] else None
        retrieved = retrieve_vector(qclient, cfg["collection"], q_vec, top_k=5, patient_group_filter=use_filter)

        metrics = compute_metrics(retrieved, expected)
        metrics["case_id"] = row["id"]
        metrics["case"] = row.get("case", "")
        metrics["query"] = query
        metrics["expected_source"] = expected["expected_source"]
        metrics["expected_pages"] = expected.get("expected_pages", [])
        metrics["patient_group_query"] = expected["patient_group_query"]
        metrics["retrieved_top5"] = [
            {
                "rank": rank,
                "chunk_id": r["chunk_id"],
                "source": r["source"],
                "page": r["page"],
                "journal_page": r.get("journal_page"),
                "patient_group": r.get("patient_group", "general"),
                "score": round(float(r.get("score", 0)), 4),
            }
            for rank, r in enumerate(retrieved[:5], start=1)
        ]
        metrics["retrieved_top3"] = [
            f"{h['chunk_id']} | {h['source']} pdf.{h['page']} j.{h.get('journal_page','-')} | {h['patient_group']}"
            for h in metrics["retrieved_top5"][:3]
        ]
        results_per_case.append(metrics)

    all_results[cfg["name"]] = results_per_case
    avg_mrr = sum(r["mrr"] for r in results_per_case) / len(results_per_case)
    avg_src = sum(r["recall_source"] for r in results_per_case) / len(results_per_case)
    avg_pg  = sum(r["patient_group_correct"] for r in results_per_case) / len(results_per_case)
    page_j  = [r["recall_page_journal"] for r in results_per_case if r["recall_page_journal"] is not None]
    page_p  = [r["recall_page_pdf"] for r in results_per_case if r["recall_page_pdf"] is not None]
    avg_pj  = sum(page_j) / len(page_j) if page_j else None
    avg_pp  = sum(page_p) / len(page_p) if page_p else None
    pj_str  = f"{avg_pj:.3f}" if avg_pj is not None else "n/a"
    pp_str  = f"{avg_pp:.3f}" if avg_pp is not None else "n/a"
    print(f"  MRR={avg_mrr:.3f} | Source={avg_src:.3f} | Page(journal)={pj_str} | Page(pdf)={pp_str} | Group={avg_pg:.3f}")
"""

CELL_49 = """# ── สรุปผลการทดลองทั้งหมด ─────────────────────────────────────
summary_rows = []

for cfg_name, results in all_results.items():
    strategy = cfg_name.split("_")[0]
    page_j_vals = [r["recall_page_journal"] for r in results if r["recall_page_journal"] is not None]
    page_p_vals = [r["recall_page_pdf"] for r in results if r["recall_page_pdf"] is not None]
    row = {
        "Strategy"              : strategy.upper(),
        "Embedding"             : "Gemini",
        "Config"                : cfg_name,
        "MRR"                   : round(sum(r["mrr"] for r in results) / len(results), 3),
        "Source Recall@5"       : round(sum(r["recall_source"] for r in results) / len(results), 3),
        "Page Recall@5 (journal)": round(sum(page_j_vals) / len(page_j_vals), 3) if page_j_vals else None,
        "Page Recall@5 (pdf)"   : round(sum(page_p_vals) / len(page_p_vals), 3) if page_p_vals else None,
        "Group Accuracy"        : round(sum(r["patient_group_correct"] for r in results) / len(results), 3),
        "Cases w/ page"         : len(page_j_vals),
    }
    summary_rows.append(row)

summary_df = pd.DataFrame(summary_rows)
summary_df = summary_df.sort_values(["Source Recall@5", "MRR"], ascending=False)

print("\\n🏆 RESULTS SUMMARY — Run 2 (Gemini only)")
print("=" * 90)
print(summary_df.to_string(index=False))

print("\\n📌 Run 1 baseline (C, pre-parser-fix, 72 chunks):")
print(f"   Source Recall@5={RUN1_BASELINE['Source Recall@5']} | Page(pdf)={RUN1_BASELINE['Page Recall@5_pdf']} | MRR={RUN1_BASELINE['MRR']}")
"""

CELL_52 = """# ── สรุปข้อเสนอแนะ ────────────────────────────────────────────
best = summary_df.iloc[0]
c_row = summary_df[summary_df["Strategy"] == "C"].iloc[0] if (summary_df["Strategy"] == "C").any() else best

print("\\n" + "=" * 70)
print("🏆 RECOMMENDATION — Run 2")
print("=" * 70)
print(f\"\"\"
Strategy ที่ดีที่สุด : {best['Strategy']}
MRR                 : {best['MRR']}
Source Recall@5     : {best['Source Recall@5']}
Page Recall (journal): {c_row.get('Page Recall@5 (journal)', 'n/a')}
Page Recall (pdf)    : {c_row.get('Page Recall@5 (pdf)', 'n/a')}  ← Run1 ใช้ตัวนี้ได้ 0.12

สถานะ production:
✅ Strategy C + parser fix + patient_group อยู่ใน md_chunker.py แล้ว
✅ page = PDF (เว็บใช้ #page=N) | journal_page = เลขวารสาร (eval)

ถัดไป:
1. Dose supportive layer
2. ปรับ frontend แสดง journal_page ใน [Ref] (ถ้าต้องการ)
3. BM25/hybrid rerank (ablation ใน Section 6.5)
\"\"\")
print("=" * 70)
"""

CELL_40 = """---
## Section 5.5: เปรียบเทียบผลจริงรายเคส (A vs B vs C)

ด้านล่างแสดง **ผลทดลองจริงทีละ case** — ไม่ใช่แค่คะแนนเฉลี่ยตอนท้าย

### Metrics ที่วัด (ต่อ 1 test case)

| Metric | ความหมาย | คำนวณยังไง |
|---|---|---|
| **MRR** | อันดับของ chunk ที่ source ถูก | `1/rank` ของ hit แรกที่ `source == expected_source` (0 ถ้าไม่เจอใน top-5) |
| **Source Recall@5** | เจอเอกสารถูกมั้ย | `1` ถ้า top-5 มี chunk จาก `expected_source` (AAFP/URI), `0` ถ้าไม่มี |
| **Page (journal)** | เจอเลขหน้าวารสารถูกมั้ย | AAFP ใช้ `journal_page` เทียบ `expected_pages` (±1) — **ตัวหลัก Run 2** |
| **Page (pdf)** | เทียบ `page` PDF แบบ Run 1 | อ้างอิงเท่านั้น (Run 1 ได้ 0.12 เพราะ metric ไม่ fair) |
| **Group Accuracy** | กลุ่มผู้ป่วยสอดคล้องมั้ย | `1` ถ้า top-5 มี chunk ที่ `patient_group` เข้ากับ query |

### สิ่งที่ต่างกันระหว่าง Strategy (Run 2)

| | A (Legacy) | B (Heading split) | C (Production) |
|---|---|---|---|
| Chunks | ~79 | ~72 | ~132 |
| Table isolation | ไม่ | ไม่ | ใช่ (7 ตาราง AAFP) |
| journal_page | ไม่ | ไม่ | ใช่ |
| patient_group + filter | ไม่ | ไม่ | ใช่ (inclusive) |

> **หมายเหตุ:** รัน cell Eval (Section 6) ก่อน cell ด้านล่าง
"""

CELL_41 = """# ── ตารางเปรียบเทียบรายเคส (A vs B vs C) ───────────────────────
from IPython.display import display

STRATEGY_LABELS = {
    "A_gemini": "A",
    "B_gemini": "B",
    "C_gemini": "C",
}

def _fmt_pages(pages) -> str:
    if not pages:
        return "—"
    return ", ".join(str(int(p)) for p in pages)

def _fmt_page_recall(val) -> str:
    if val is None:
        return "n/a"
    return "✅" if val == 1 else "❌"

def _fmt_bool(val) -> str:
    return "✅" if val == 1 else "❌"

def _fmt_top1(m) -> str:
    src = m.get("top1_source")
    if not src:
        return "—"
    grp = m.get("top1_group", "general")
    if src == "AAFP" and m.get("top1_journal_page") is not None:
        return f"{src} j.{m['top1_journal_page']} pdf.{m['top1_page']} ({grp})"
    return f"{src} p.{m['top1_page']} ({grp})"

def _fmt_top5(hits: list) -> str:
    if not hits:
        return "(ไม่มีผลลัพธ์)"
    lines = []
    for h in hits:
        if isinstance(h, dict) and "raw" in h:
            lines.append(f"#{h.get('rank', '?')} {h['raw']}")
        elif isinstance(h, dict):
            j = h.get("journal_page", "-")
            lines.append(
                f"#{h.get('rank', '?')} {h['chunk_id']} | {h['source']} pdf.{h['page']} j.{j} "
                f"| {h.get('patient_group', 'general')} | score={h.get('score', 0):.4f}"
            )
        else:
            lines.append(str(h))
    return "\\n".join(lines)

compare_rows = []
for _, ev in eval_df.iterrows():
    case_id = ev["id"]
    row = {
        "id": case_id,
        "case": ev.get("case", ""),
        "query": ev["input"],
        "expected_source": ev["expected_source"],
        "expected_pages": ev.get("expected_pages", []),
        "query_group": ev["patient_group_query"],
    }
    for cfg_name, label in STRATEGY_LABELS.items():
        m = next(r for r in all_results[cfg_name] if r["case_id"] == case_id)
        row[f"{label}_mrr"] = m["mrr"]
        row[f"{label}_source"] = m["recall_source"]
        row[f"{label}_page"] = m["recall_page_journal"]
        row[f"{label}_page_pdf"] = m["recall_page_pdf"]
        row[f"{label}_group"] = m["patient_group_correct"]
        row[f"{label}_top1"] = _fmt_top1(m)
        row[f"{label}_top5"] = m.get("retrieved_top5", [])
        if not row[f"{label}_top5"] and m.get("retrieved_top3"):
            row[f"{label}_top5"] = [{"rank": i + 1, "raw": s} for i, s in enumerate(m["retrieved_top3"])]
    compare_rows.append(row)

compare_df = pd.DataFrame(compare_rows)

print(f"📊 Per-case comparison: {len(compare_df)} cases")
print(f"   Source hit counts → A:{compare_df['A_source'].sum()} | B:{compare_df['B_source'].sum()} | C:{compare_df['C_source'].sum()}")
pj = lambda col: compare_df[col].dropna().astype(int).sum()
print(f"   Page(journal)     → A:{pj('A_page')} | B:{pj('B_page')} | C:{pj('C_page')}")
print(f"   Page(pdf)         → A:{pj('A_page_pdf')} | B:{pj('B_page_pdf')} | C:{pj('C_page_pdf')}")
print(f"   Group hit counts  → A:{compare_df['A_group'].sum()} | B:{compare_df['B_group'].sum()} | C:{compare_df['C_group'].sum()}")

summary_cols = [
    "id", "case", "expected_source", "expected_pages", "query_group",
    "A_mrr", "A_source", "A_page", "A_page_pdf", "A_group", "A_top1",
    "B_mrr", "B_source", "B_page", "B_page_pdf", "B_group", "B_top1",
    "C_mrr", "C_source", "C_page", "C_page_pdf", "C_group", "C_top1",
]
display_df = compare_df[summary_cols].copy()
display_df["expected_pages"] = display_df["expected_pages"].apply(_fmt_pages)
for col in ["A_page", "B_page", "C_page", "A_page_pdf", "B_page_pdf", "C_page_pdf"]:
    display_df[col] = display_df[col].apply(_fmt_page_recall)
for col in ["A_source", "B_source", "C_source", "A_group", "B_group", "C_group"]:
    display_df[col] = display_df[col].apply(_fmt_bool)

display_df.rename(columns={
    "id": "Case",
    "case": "Level",
    "expected_source": "Exp Source",
    "expected_pages": "Exp Pages",
    "query_group": "Query Group",
    "A_mrr": "A MRR", "A_source": "A Src", "A_page": "A Page(j)", "A_page_pdf": "A Page(pdf)",
    "A_group": "A Grp", "A_top1": "A Top-1",
    "B_mrr": "B MRR", "B_source": "B Src", "B_page": "B Page(j)", "B_page_pdf": "B Page(pdf)",
    "B_group": "B Grp", "B_top1": "B Top-1",
    "C_mrr": "C MRR", "C_source": "C Src", "C_page": "C Page(j)", "C_page_pdf": "C Page(pdf)",
    "C_group": "C Grp", "C_top1": "C Top-1",
}, inplace=True)

print("\\n📋 Metrics รายเคส (✅=ผ่าน, ❌=ไม่ผ่าน, n/a=ไม่มีเลขหน้าใน Ref)")
display(display_df)
"""

CELL_36_MD = """---
## Section 6: Retrieval Accuracy Test

วัด Recall@5, MRR, patient_group accuracy

**Run 2 metrics:**
- `Page Recall@5 (journal)` — AAFP ใช้ `journal_page` (P.628–636 ตาม test case) ← **ตัวหลัก**
- `Page Recall@5 (pdf)` — เทียบ `page` แบบ Run 1 (อ้างอิง)
"""


def set_cell(nb, idx, content, cell_type="code"):
    nb["cells"][idx]["source"] = [content]
    if cell_type:
        nb["cells"][idx]["cell_type"] = cell_type
    nb["cells"][idx]["outputs"] = []
    nb["cells"][idx]["execution_count"] = None


def patch_cell3(nb):
    prefix_end = "from dotenv import load_dotenv\n\n"
    src = "".join(nb["cells"][3]["source"])
    if "find_fast_root" in src:
        return
    if prefix_end in src:
        head = src[: src.index(prefix_end) + len(prefix_end)]
        nb["cells"][3]["source"] = [head + CELL_3_PATHS]
    nb["cells"][3]["outputs"] = []
    nb["cells"][3]["execution_count"] = None


def main():
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    set_cell(nb, 0, CELL_0, "markdown")
    patch_cell3(nb)
    set_cell(nb, 11, CELL_11, "markdown")
    set_cell(nb, 12, CELL_12, "code")
    set_cell(nb, 19, CELL_19, "markdown")
    set_cell(nb, 20, CELL_20, "code")
    set_cell(nb, 28, CELL_28, "code")
    set_cell(nb, 40, CELL_40, "markdown")
    set_cell(nb, 41, CELL_41, "code")
    set_cell(nb, 36, CELL_36_MD, "markdown")
    set_cell(nb, 37, CELL_37, "code")
    set_cell(nb, 39, CELL_39, "code")
    set_cell(nb, 49, CELL_49, "code")
    set_cell(nb, 52, CELL_52, "code")
    NB_PATH.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Patched {NB_PATH}")


if __name__ == "__main__":
    main()
