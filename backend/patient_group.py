"""
Patient group inference for guideline chunks and pharmacy queries.

AAFP and URI guidelines rarely have clean pediatric/adult section splits.
URI is pediatric-only; AAFP mixes both in prose and dose tables.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Document defaults ─────────────────────────────────────────────────────────

DOCUMENT_DEFAULTS = {
    "URI": "pediatric",   # ทั้งเล่มเป็นแนวทางในเด็ก
    "AAFP": "general",    # family medicine article — ผสมทั้งสองกลุ่ม
}

# ── Content patterns (chunk tagging) ───────────────────────────────────────────

BOTH_PATTERNS = [
    r"\badults?\s+and\s+children\b",
    r"\bchildren\s+and\s+adults\b",
    r"\bchildren\s+and\s+adolescents\b",
    r"\bin\s+children\s+and\s+adults\b",
    r"\badults?\s*\|\s*children\b",
    r"<td>\s*adults?\s*</td>\s*<td>\s*children\b",
    r"\|adults?\|[^\n]*\|children\b",
]

PEDIATRIC_PATTERNS = [
    r"\bchildren\b",
    r"\bchild\b",
    r"\binfants?\b",
    r"\bpediatric\b",
    r"\badolescents?\b",
    r"\bเด็ก\b",
    r"\bกุมาร\b",
    r"\bทารก\b",
    r"ระบบหายใจในเด็ก",
    r"\bmonths?\s+and\s+older\b",
    r"\byounger\s+than\s+\d+\s+years\b",
    r"\bunder\s+\d+\s+years\b",
    r"\btwo\s+years\s+and\s+older\b",
    r"\bsix\s+to\s+twenty[- ]three\s+months\b",
    r"\b\d+\s+to\s+\d+\s+months\b",
    r"\bamerican academy of pediatrics\b",
    r"\bmcisaa?c\b",
    r"\btympanic\s+membrane\b",
    r"\bwatchful waiting\b",
    r"\bper\s+kg\b",
    r"\bmg\s+per\s+kg\b",
]

ADULT_PATTERNS = [
    r"\badults?\b",
    r"\bผู้ใหญ่\b",
    r"\b65\s+years\s+and\s+older\b",
    r"\bolder\s+than\s+45\s+years\b",
    r"\b15\s+to\s+45\s+years\b",
    r"\bcentor\s+(?:criteria|score)\s+is\s+only\s+applicable\s+to\s+adults\b",
    r"\bfor\s+adults\b",
    r"\bin\s+adults\b",
    r"\badult\s+patients\b",
    r"\b500\s+mg\b",
    r"\b875\s+mg\b",
    r"\b1000\s+mg\b",
]

# ── Query patterns (test cases / user input) ───────────────────────────────────

QUERY_CHILD_PATTERNS = [
    r"เด็ก",
    r"ทารก",
    r"กุมาร",
    r"แรกเกิด",
    r"ลูก(?:ชาย|สาว|แฝด)?",
    r"\d+\s*ขวบ",
    r"\d+\s*เดือน",
    r"pediatric",
    r"\bchildren\b",
    r"\bchild\b",
]

QUERY_ADULT_PATTERNS = [
    r"ผู้ใหญ่",
    r"\badult\b",
    r"ชายอายุ\s*\d+\s*ปี",
    r"หญิงอายุ\s*\d+\s*ปี",
    r"ผู้ป่วย(?:ผู้ใหญ่)?อายุ\s*\d+\s*ปี",
    r"อายุ\s*\d+\s*ปี",
]


def _count_pattern_hits(text: str, patterns: list[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


def infer_patient_group_from_chunk(
    content: str,
    source: str = "",
    heading: str = "",
) -> str:
    """
    Tag chunk from document source + heading + body.

    Returns: pediatric | adult | both | general
    """
    source_key = (source or "").replace(".md", "").strip().upper()
    if source_key == "URI":
        return "pediatric"

    text = f"{heading}\n{content}"
    lower = text.lower()

    if _count_pattern_hits(lower, BOTH_PATTERNS):
        return "both"

    ped = _count_pattern_hits(lower, PEDIATRIC_PATTERNS)
    adult = _count_pattern_hits(lower, ADULT_PATTERNS)

    if ped > 0 and adult > 0:
        return "both"
    if ped > adult:
        return "pediatric"
    if adult > ped:
        return "adult"

    return DOCUMENT_DEFAULTS.get(source_key, "general")


def infer_patient_group_from_query(text: str) -> str:
    """Infer patient group from pharmacy case / user query."""
    if not isinstance(text, str) or not text.strip():
        return "general"

    lower = text.lower()

    if any(re.search(p, lower) for p in QUERY_CHILD_PATTERNS):
        # ถ้ามีทั้งลูกและผู้ใหญ่ในประโยคเดียว ให้ pediatric ชนะ (เคสเด็กชัดกว่า)
        if re.search(r"\d+\s*ขวบ", lower) or re.search(r"\d+\s*เดือน", lower):
            return "pediatric"
        if re.search(r"ลูก|เด็ก|ทารก|กุมาร", lower):
            return "pediatric"

    # อายุเป็นปี — ใช้ threshold 18 ปี
    for pat in (
        r"(?:ชาย|หญิง|ผู้(?:ป่วย)?)อายุ\s*(\d+)\s*ปี",
        r"อายุ\s*(\d+)\s*ปี",
        r"(\d+)\s*ปี",
    ):
        m = re.search(pat, lower)
        if m:
            age = int(m.group(1))
            return "pediatric" if age < 18 else "adult"

    if any(re.search(p, lower) for p in QUERY_ADULT_PATTERNS):
        return "adult"
    if any(re.search(p, lower) for p in QUERY_CHILD_PATTERNS):
        return "pediatric"

    return "general"


def groups_compatible(query_group: str, chunk_group: str) -> bool:
    """Whether retrieved chunk is acceptable for the query's patient group."""
    if not query_group or query_group == "general":
        return True
    if not chunk_group or chunk_group in ("general", "both"):
        return True
    return query_group == chunk_group


def filter_groups_for_query(query_group: str) -> Optional[list[str]]:
    """
    Inclusive Qdrant filter groups.

    PDF ไม่ได้แยกเด็ก/ผู้ใหญ่ชัด — ยังค้นหา general + both เสมอ
    """
    if not query_group or query_group == "general":
        return None

    allowed = ["general", "both"]
    if query_group == "pediatric":
        allowed.append("pediatric")
    elif query_group == "adult":
        allowed.append("adult")
    else:
        return None

    return allowed
