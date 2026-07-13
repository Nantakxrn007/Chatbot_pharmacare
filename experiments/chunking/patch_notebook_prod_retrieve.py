"""Patch experiment notebook retrieve to match production (per-source + LLM rerank)."""
import json
from pathlib import Path

NB_PATH = Path(__file__).parent / "experiment_chunking.ipynb"

CELL_36 = """---
## Section 6: Retrieval Accuracy Test

วัด Recall@5, MRR, patient_group accuracy

**Run 3 — retrieve เหมือน production (`rag_engine.retrieve_chunks`):**
1. `patient_group` inclusive filter
2. ดึงแยกเล่ม AAFP / URI
3. **LLM rerank** (fallback BM25) — ตั้ง `RERANK_MODE` ได้
4. source coverage → top_k

**Metrics:**
- `Page Recall@5 (journal)` — AAFP ใช้ `journal_page` ← ตัวหลัก
- `Page Recall@5 (pdf)` — เทียบ `page` แบบ Run 1 (อ้างอิง)

> ⚠️ LLM rerank = ~1 API call / case / strategy → 25 cases × 3 ≈ **75 calls** (ช้า + ใช้ quota)
> ถ้าอยากเร็ว: ใส่ `os.environ[\"RERANK_MODE\"]=\"bm25\"` ก่อนรัน cell นี้
"""

CELL_37 = r'''from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue
from backend.rag_engine import (
    retrieve_chunks,
    RERANK_MODE,
    PER_SOURCE_TOP_K,
)

GUIDELINE_SOURCES = {"AAFP", "URI"}

# ให้ตรง production — เปลี่ยนได้ก่อนรัน eval
# os.environ["RERANK_MODE"] = "bm25"   # เร็ว ไม่เรียก LLM
# os.environ["RERANK_MODE"] = "llm"    # default เหมือน production
# os.environ["RERANK_MODE"] = "vector" # ไม่ rerank

print(f"✅ Production retrieve ready | RERANK_MODE={RERANK_MODE} | PER_SOURCE_TOP_K={PER_SOURCE_TOP_K}")


def _normalize_source(source: str) -> str:
    if not source:
        return ""
    return str(source).replace(".md", "").strip()


def _page_for_match(r: dict, exp_source: str) -> int | None:
    """AAFP → journal_page (เลขวารสาร), URI/อื่น → pdf page"""
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
    """
    Legacy: vector + filter อย่างเดียว (เก็บไว้เทียบ ablation)
    """
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
            "rerank_method": "vector_only",
        }
        for r in results
        if _normalize_source(r.payload.get("source")) in GUIDELINE_SOURCES
    ]


def retrieve_production(
    client: QdrantClient,
    collection: str,
    query: str,
    query_vec: list[float],
    top_k: int = 5,
    patient_group_filter: str = None,
    rerank_mode: str = None,
) -> list[dict]:
    """
    เหมือน production `rag_engine.search_chunks`:
    filter → per-source → LLM/BM25 rerank → source coverage
    """
    chunks = retrieve_chunks(
        client,
        collection,
        query,
        query_vector=query_vec,
        top_k=top_k,
        patient_group=patient_group_filter,
        apply_group_filter=True,
        rerank_mode=rerank_mode,
    )
    out = []
    for c in chunks:
        src = _normalize_source(c.get("source", ""))
        if src not in GUIDELINE_SOURCES:
            continue
        score = c.get("rerank_score")
        if score is None:
            score = c.get("vector_score", 1.0 - float(c.get("distance", 0.0)))
        out.append({
            "chunk_id"     : c.get("chunk_id", ""),
            "source"       : src,
            "page"         : c.get("page", 0),
            "journal_page" : c.get("journal_page"),
            "heading"      : c.get("heading", ""),
            "patient_group": c.get("patient_group", "general"),
            "score"        : float(score),
            "vector_score" : c.get("vector_score"),
            "rerank_method": c.get("rerank_method", "prod"),
            "content"      : (c.get("content") or "")[:200],
        })
    return out


def compute_metrics(retrieved: list[dict], expected: dict, k: int = 5) -> dict:
    """Recall@k, MRR — Page Recall แยก journal (AAFP) vs pdf"""
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
        "recall_page"          : recall_page_journal,
        "recall_page_journal"  : recall_page_journal,
        "recall_page_pdf"      : recall_page_pdf,
        "patient_group_correct": recall_group,
        "mrr"                  : mrr,
        "top1_source"          : top.get("source"),
        "top1_page"            : top.get("page"),
        "top1_journal_page"    : top.get("journal_page"),
        "top1_group"           : top.get("patient_group"),
        "top1_rerank_method"   : top.get("rerank_method"),
    }

print("✅ Retrieval metric functions ready (production retrieve + journal/pdf page)")
'''

CELL_39 = r'''# รัน Retrieval Evaluation — production retrieve (per-source + LLM/BM25 rerank)
# ใช้ retrieve เดียวกันทุก strategy เพื่อเทียบ chunk A/B/C แบบ fair

USE_PRODUCTION_RETRIEVE = True  # False = กลับไป vector+filter แบบ Run 2

CONFIGS = [
    {"name": "A_gemini", "collection": "exp_A_gemini"},
    {"name": "B_gemini", "collection": "exp_B_gemini"},
    {"name": "C_gemini", "collection": "exp_C_gemini"},
]

all_results = {}
mode_label = RERANK_MODE if USE_PRODUCTION_RETRIEVE else "vector_only"
print(f"⚙️  Retrieve mode: {'production/' + mode_label if USE_PRODUCTION_RETRIEVE else 'legacy vector+filter'}")

for cfg in CONFIGS:
    print(f"\n🔍 Evaluating: {cfg['name']}...")
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
        # production ใช้ filter กับทุก strategy (fair) — A/B chunk ไม่มี tag ก็ได้ general
        use_filter = row["patient_group_query"]

        if USE_PRODUCTION_RETRIEVE:
            retrieved = retrieve_production(
                qclient,
                cfg["collection"],
                query,
                q_vec,
                top_k=5,
                patient_group_filter=use_filter,
                rerank_mode=os.getenv("RERANK_MODE"),
            )
        else:
            retrieved = retrieve_vector(
                qclient,
                cfg["collection"],
                q_vec,
                top_k=5,
                patient_group_filter=use_filter if "C" in cfg["name"] else None,
            )

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
                "rerank_method": r.get("rerank_method"),
            }
            for rank, r in enumerate(retrieved[:5], start=1)
        ]
        metrics["retrieved_top3"] = [
            f"{h['chunk_id']} | {h['source']} pdf.{h['page']} j.{h.get('journal_page','-')} | {h['patient_group']} | {h.get('rerank_method','')}"
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
'''


def set_cell(nb, idx, content, cell_type="code"):
    nb["cells"][idx]["source"] = [content]
    nb["cells"][idx]["cell_type"] = cell_type
    nb["cells"][idx]["outputs"] = []
    nb["cells"][idx]["execution_count"] = None


def main():
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    set_cell(nb, 36, CELL_36, "markdown")
    set_cell(nb, 37, CELL_37, "code")
    set_cell(nb, 39, CELL_39, "code")

    # bump header note on cell 0 if present
    src0 = "".join(nb["cells"][0].get("source", []))
    if "Run 3" not in src0:
        note = (
            "\n\n> **Run 3:** Eval retrieve = production "
            "(`filter → per-source → LLM/BM25 rerank`) — ดู Section 6\n"
        )
        nb["cells"][0]["source"] = [src0.rstrip() + note]

    # summary cell 49 — mention Run 3
    src49 = "".join(nb["cells"][49].get("source", []))
    src49 = src49.replace(
        "🏆 RESULTS SUMMARY — Run 2 (Gemini only)",
        "🏆 RESULTS SUMMARY — Run 3 (production retrieve)",
    )
    if "Run 2 baseline" not in src49 and "Run1" in src49.replace(" ", ""):
        pass
    src49 = src49.replace(
        "📌 Run 1 baseline (C, pre-parser-fix, 72 chunks):",
        "📌 Baselines:\n"
        "   Run 1 C (pre-fix): Source=0.84 | Page(pdf)=0.12 | MRR=0.553\n"
        "   Run 2 C (chunk fix, vector+filter): Source=0.80 | Page(journal)=0.56 | MRR=0.630\n"
        "📌 Run 1 baseline (C, pre-parser-fix, 72 chunks):",
    )
    nb["cells"][49]["source"] = [src49]
    nb["cells"][49]["outputs"] = []
    nb["cells"][49]["execution_count"] = None

    NB_PATH.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Patched {NB_PATH}")


if __name__ == "__main__":
    main()
