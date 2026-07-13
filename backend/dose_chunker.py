"""
Dose supportive CSV → structured chunks
======================================
1 แถวยา → สูงสุด 2 chunks (adult / pediatric)
page = คอลัมน์ Page ใน CSV = เลขหน้า Dose supportive.pdf (เปิดอ้างอิง #page=N ได้)

ไม่ใช้ Strategy C ของ markdown — เป็นตารางยาต้องแม่น
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.md_chunker import ChunkConfig, estimate_tokens
from backend.config import DOSE_PDF_NAME as _CFG_DOSE_PDF

_DOSE_TOKEN_CFG = ChunkConfig()  # ใช้ chars_per_token=4 เหมือน MD

DOSE_SOURCE_NAME = "Dose"
DOSE_PDF_NAME = _CFG_DOSE_PDF  # ไฟล์ PDF ที่ frontend เปิดด้วย #page=N

COL_PAGE = "Page"
COL_DRUG = "ชื่อสามัญ/ชื่อการค้า"
COL_INDICATION = "ข้อบ่งใช้"
COL_ADULT = "Dose ผู้ใหญ่"
COL_PEDIATRIC = "Dose เด็ก"
COL_RENAL_HEPATIC = "Dose ปรับตามตับ/ไต"
COL_WARNINGS = "ข้อห้ามใช้/ควรระวัง"

REQUIRED_COLS = {
    COL_PAGE,
    COL_DRUG,
    COL_INDICATION,
    COL_ADULT,
    COL_PEDIATRIC,
    COL_RENAL_HEPATIC,
    COL_WARNINGS,
}


def _cell_text(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    text = str(val).strip()
    if text.lower() in {"nan", "none", "x", "-", "—"}:
        return ""
    return text


def _short(text: str, max_len: int = 80) -> str:
    text = _cell_text(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _parse_page(val) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def load_dose_csv(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"ไม่พบ Dose CSV: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig")
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV schema ไม่ตรง — ขาด columns: {sorted(missing)}\nพบ: {list(df.columns)}"
        )
    return df


def dose_df_to_chunks(df: pd.DataFrame) -> list[dict]:
    """
    แปลง Dose supportive.csv → chunks
    - แยก adult / pediatric
    - ติด renal/hepatic + warnings ทุก chunk
    - page จากคอลัมน์ Page (= หน้า PDF)
    """
    chunks: list[dict] = []
    chunk_seq = 0

    for _, row in df.iterrows():
        page = _parse_page(row.get(COL_PAGE))
        drug_name = _cell_text(row.get(COL_DRUG))
        indication = _cell_text(row.get(COL_INDICATION))
        renal_hepatic = _cell_text(row.get(COL_RENAL_HEPATIC))
        warnings = _cell_text(row.get(COL_WARNINGS))

        if not drug_name:
            continue

        dose_variants = [
            ("adult", "ผู้ใหญ่", COL_ADULT),
            ("pediatric", "เด็ก", COL_PEDIATRIC),
        ]

        for patient_group, group_label, dose_col in dose_variants:
            dose_text = _cell_text(row.get(dose_col))
            if not dose_text:
                continue

            heading = f"{drug_name} Dosing — {_short(indication)} — {group_label}"
            content_parts = [
                f"[Source: {DOSE_SOURCE_NAME} | Page: {page} | Drug: {drug_name} | Group: {patient_group}]",
                f"[Context: {DOSE_SOURCE_NAME} | {drug_name} | {patient_group} | PDF: {DOSE_PDF_NAME}#page={page}]",
                "",
                f"Drug: {drug_name}",
                f"Indication: {indication}",
                f"Patient Group: {patient_group}",
                f"Dose ({group_label}): {dose_text}",
            ]
            if renal_hepatic:
                content_parts.append(f"Renal/Hepatic Adjustment: {renal_hepatic}")
            if warnings:
                content_parts.append(f"Warnings/Contraindications: {warnings}")
            content_parts.append(f"Ref: {DOSE_SOURCE_NAME}, หน้า {page} ({DOSE_PDF_NAME})")

            content = "\n".join(content_parts)
            chunks.append({
                "chunk_id": f"dose_{chunk_seq:03d}",
                "source": DOSE_SOURCE_NAME,
                "page": page,
                "heading": heading,
                "type": "dose_table",
                "content": content,
                "tokens_approx": estimate_tokens(content, _DOSE_TOKEN_CFG),
                "patient_group": patient_group,
                "drug_name": drug_name,
                "pdf_file": DOSE_PDF_NAME,
            })
            chunk_seq += 1

    return chunks


def chunk_dose_csv(csv_path: str | Path) -> list[dict]:
    """API หลัก: path → chunks"""
    df = load_dose_csv(csv_path)
    return dose_df_to_chunks(df)


def print_dose_summary(chunks: list[dict]) -> None:
    n_adult = sum(1 for c in chunks if c.get("patient_group") == "adult")
    n_ped = sum(1 for c in chunks if c.get("patient_group") == "pediatric")
    pages = sorted({c.get("page", 0) for c in chunks if c.get("page")})
    print(f"  Dose chunks: {len(chunks)} (adult={n_adult}, pediatric={n_ped})")
    print(f"  PDF pages covered: {len(pages)} unique | range {min(pages) if pages else '-'}–{max(pages) if pages else '-'}")
    print(f"  PDF file for Ref: {DOSE_PDF_NAME}")


if __name__ == "__main__":
    from backend.config import DOSE_CSV
    chunks = chunk_dose_csv(DOSE_CSV)
    print_dose_summary(chunks)
    if chunks:
        print("\nSample:")
        c = chunks[0]
        print(f"  [{c['chunk_id']}] page={c['page']} | {c['heading']}")
        print(c["content"][:400], "...")
