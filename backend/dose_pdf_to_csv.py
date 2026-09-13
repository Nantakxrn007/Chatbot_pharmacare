"""
Dose supportive.pdf → Dose supportive.csv
========================================
ดึงตารางทีละหน้าด้วย pdfplumber แล้วเย็บแถวข้ามหน้า
ข้อความไทยประกอบจากลำดับตัวอักษรใน PDF (ไม่เรียงตาม x) เพื่อไม่ให้สระลอย
Page = หน้าแรกที่ยานั้นเริ่ม (เปิด PDF #page=N ได้)

    python backend/dose_pdf_to_csv.py
    python rag/pipeline.py --dose-pdf
"""

from __future__ import annotations

import csv
import logging
import re
import sys
import warnings
from pathlib import Path

logging.getLogger("pdfminer").setLevel(logging.ERROR)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pdfplumber

from backend.config import DOSE_CSV, DOSE_PDF
from backend.dose_chunker import (
    COL_ADULT,
    COL_DRUG,
    COL_INDICATION,
    COL_PAGE,
    COL_PEDIATRIC,
    COL_RENAL_HEPATIC,
    COL_WARNINGS,
)

CSV_COLUMNS = [
    COL_PAGE,
    COL_DRUG,
    COL_INDICATION,
    COL_ADULT,
    COL_PEDIATRIC,
    COL_RENAL_HEPATIC,
    COL_WARNINGS,
]

# แถวหลุดจากตาราง (ไม่ใช่ชื่อยา)
_NOT_DRUG = re.compile(
    r"^(?:"
    r"[\d\(\)\[\]\}]"
    r"|use:"
    r"|indications?"
    r"|fever:"
    r"|pain:"
    r"|temporary"
    r"|reduction of"
    r"|minor aches"
    r"|and headache"
    r"|labeled"
    r"|oral:"
    r")",
    re.IGNORECASE,
)

# สระ/วรรณยุกต์ที่ต้องอยู่กับพยัญชนะ — extract_tables() มักเรียงตาม x แล้วสระลอย
_SARA_AM = "\u0e33"       # ำ
_NIKHAHIT = "\u0e4d"      # ํ
_SARA_AA = "\u0e32"       # า
_TONES = "่้๊๋"


def _in_bbox(char: dict, bbox: tuple, y_tol: float = 2.0, x_tol: float = 1.0) -> bool:
    x0, top, x1, bot = bbox
    cx = (char["x0"] + char["x1"]) / 2
    cy = (char["top"] + char["bottom"]) / 2
    return (x0 - x_tol) <= cx <= (x1 + x_tol) and (top - y_tol) <= cy <= (bot + y_tol)


def _cell_from_chars(chars: list[dict], bbox: tuple | None) -> str:
    """ประกอบข้อความในเซลล์ตามลำดับใน PDF (ไม่เรียงตาม x) เพื่อไม่ให้สระไทยเพี้ยน"""
    if bbox is None:
        return ""
    selected = [c for c in chars if c.get("text") and _in_bbox(c, bbox)]
    if not selected:
        return ""
    parts: list[str] = []
    last_top = selected[0]["top"]
    for c in selected:
        if abs(c["top"] - last_top) > 5:
            parts.append(" ")
            last_top = c["top"]
        parts.append(c["text"])
    return "".join(parts)


def _normalize_thai(text: str) -> str:
    """แก้สระลอยจาก PDF: ํ+า → ำ และย้ายวรรณยุกต์ให้อยู่ก่อน ำ"""
    text = text.replace("\u00a0", " ")
    text = text.replace(_NIKHAHIT + _SARA_AA, _SARA_AM)
    text = text.replace(_SARA_AA + _NIKHAHIT, _SARA_AM)
    text = re.sub(
        rf"{_NIKHAHIT}([{_TONES}]){_SARA_AA}",
        lambda m: m.group(1) + _SARA_AM,
        text,
    )
    text = re.sub(
        rf"{_NIKHAHIT}{_SARA_AA}([{_TONES}])",
        lambda m: m.group(1) + _SARA_AM,
        text,
    )
    text = re.sub(rf"({_SARA_AM})([{_TONES}])", r"\2\1", text)
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _normalize_thai(str(text))


def _is_header(cells: list[str]) -> bool:
    first = cells[0].replace(" ", "")
    return first.startswith("ชื่อ") or "สามัญ" in first[:30]


def _map_row(raw: list[str | None]) -> list[str] | None:
    """6 คอลัมน์มาตรฐาน หรือ 5 คอลัมน์ (Dose ผู้ใหญ่/เด็ก รวมช่องเดียว)"""
    cells = [_clean(c) for c in (raw or [])]
    if not any(cells):
        return None
    if len(cells) >= 6:
        return cells[:6]
    if len(cells) == 5:
        drug, indication, adult_ped, renal, warnings = cells
        return [drug, indication, adult_ped, adult_ped, renal, warnings]
    return None


def _is_new_drug(cells: list[str]) -> bool:
    drug = cells[0]
    if not drug or _is_header(cells):
        return False
    if _NOT_DRUG.match(drug):
        return False
    if not any(cells[1:]):
        return False
    return True


def _append(dst: list[str], src: list[str]) -> None:
    for i, extra in enumerate(src):
        if not extra:
            continue
        dst[i] = f"{dst[i]} {extra}".strip() if dst[i] else extra


# ตารางย่อยใน PDF เป็นรูปแทรกในคอลัมน์ Dose เด็ก ไม่ใช่ข้อความ
# displayed size ประมาณ 67x33 pt — ใช้จับรูปแล้วแปะ markdown กลับเข้าแถวยานั้น
_NESTED_IMG_W = (40.0, 100.0)
_NESTED_IMG_H = (20.0, 80.0)

_ACETAMINOPHEN_ORAL_CHART = """
[Nested table: Acetaminophen Dosing (Oral)]
Weight (kg) | Weight (lbs) | Age | Dosage (mg)
2.7 to 5.3 | 6 to 11 | 0 to 3 mo | 40
5.4 to 8.1 | 12 to 17 | 4 to 11 mo | 80
8.2 to 10.8 | 18 to 23 | 1 to 2 y | 120
10.9 to 16.3 | 24 to 35 | 2 to 3 y | 160
16.4 to 21.7 | 36 to 47 | 4 to 5 y | 240
21.8 to 27.2 | 48 to 59 | 6 to 8 y | 320 to 325
27.3 to 32.6 | 60 to 71 | 9 to 10 y | 325 to 400
32.7 to 43.2 | 72 to 95 | 11 y | 480 to 500
Note: Manufacturer's recommendations are based on weight in pounds (OTC labeling); weight in kg listed here is derived from pounds and rounded; kg weight listed also is adjusted to allow for continuous weight ranges in kg. OTC labeling instructs consumer to consult with physician for dosing instructions in infants and children under 2 years of age.
""".strip()

_IBUPROFEN_CHART = """
[Nested table: Ibuprofen Dosing]
Weight (kg) | Weight (lbs) | Age | Dosage (mg)
5.4 to 8.1 | 12 to 17 | 6 to 11 months | 50
8.2 to 10.8 | 18 to 23 | 12 to 23 months | 75 to 80
10.9 to 16.3 | 24 to 35 | 2 to 3 years | 100
16.4 to 21.7 | 36 to 47 | 4 to 5 years | 150
21.8 to 27.2 | 48 to 59 | 6 to 8 years | 200
27.3 to 32.6 | 60 to 71 | 9 to 10 years | 200 to 250
32.7 to 43.2 | 72 to 95 | 11 years | 300
Note: Manufacturer's recommendations are based on weight in pounds (OTC labeling); weight in kg listed here is derived from pounds and rounded; kg weight listed also is adjusted to allow for continuous weight ranges in kg.
""".strip()


def _is_nested_chart_image(im: dict) -> bool:
    w = float(im.get("width") or 0)
    h = float(im.get("height") or 0)
    return _NESTED_IMG_W[0] < w < _NESTED_IMG_W[1] and _NESTED_IMG_H[0] < h < _NESTED_IMG_H[1]


def _chart_markdown_for_drug(drug_name: str, pdf_page: int | None = None) -> str:
    key = (drug_name or "").strip().lower()
    if key.startswith("paracetamol") or "acetaminophen" in key:
        md = _ACETAMINOPHEN_ORAL_CHART
        title = "Acetaminophen Dosing (Oral)"
    elif key.startswith("ibuprofen"):
        md = _IBUPROFEN_CHART
        title = "Ibuprofen Dosing"
    else:
        return ""
    if pdf_page:
        md = md.replace(
            f"[Nested table: {title}]",
            f"[Nested table: {title} | PDF page {pdf_page}]",
        )
    return md


def extract_dose_rows(pdf_path: str | Path) -> list[dict]:
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"ไม่พบ Dose PDF: {pdf_path}")

    rows: list[dict] = []
    open_row: dict | None = None
    pages_with_charts: set[int] = set()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pdfplumber.open(str(pdf_path)) as doc:
            for page_num, page in enumerate(doc.pages, 1):
                for table in page.find_tables() or []:
                    if not table.rows or len(table.rows[0].cells) < 5:
                        continue
                    for row in table.rows:
                        raw = [_cell_from_chars(page.chars, cell) for cell in row.cells]
                        cells = _map_row(raw)
                        if cells is None:
                            continue
                        if _is_header(cells):
                            continue
                        if _is_new_drug(cells):
                            open_row = {"page": page_num, "cells": cells}
                            rows.append(open_row)
                            continue
                        if open_row is not None:
                            _append(open_row["cells"], cells)
                if any(_is_nested_chart_image(im) for im in (page.images or [])):
                    pages_with_charts.add(page_num)

    for i, row in enumerate(rows):
        start = row["page"]
        nxt = rows[i + 1]["page"] if i + 1 < len(rows) else start + 1
        covered = {start} if nxt <= start else set(range(start, nxt))
        chart_pages = sorted(pages_with_charts & covered)
        if not chart_pages:
            continue
        md = _chart_markdown_for_drug(row["cells"][0], pdf_page=chart_pages[0])
        if not md:
            continue
        ped = row["cells"][3]
        if "[Nested table:" in ped:
            continue
        row["cells"][3] = f"{ped}\n\n{md}".strip() if ped else md

    out = []
    for row in rows:
        drug, indication, adult, ped, renal, warnings_text = row["cells"]
        if not drug:
            continue
        out.append({
            COL_PAGE: row["page"],
            COL_DRUG: drug,
            COL_INDICATION: indication,
            COL_ADULT: adult,
            COL_PEDIATRIC: ped,
            COL_RENAL_HEPATIC: renal,
            COL_WARNINGS: warnings_text,
        })
    return out


def save_dose_csv(rows: list[dict], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file():
        bak = output_path.with_name(output_path.stem + ".bak.csv")
        bak.write_bytes(output_path.read_bytes())
        print(f"[DOSE-PDF] backup -> {bak}")
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def pdf_to_dose_csv(
    pdf_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> list[dict]:
    pdf_path = Path(pdf_path) if pdf_path else DOSE_PDF
    output_path = Path(output_path) if output_path else DOSE_CSV
    print(f"[DOSE-PDF] {pdf_path}")
    rows = extract_dose_rows(pdf_path)
    if not rows:
        raise RuntimeError(f"ดึงแถวยาจาก PDF ไม่ได้: {pdf_path}")
    save_dose_csv(rows, output_path)
    pages = [r[COL_PAGE] for r in rows]
    print(f"[DOSE-PDF] {len(rows)} drugs -> {output_path}")
    print(f"[DOSE-PDF] start pages {min(pages)}–{max(pages)}")
    for row in rows:
        print(f"  p{row[COL_PAGE]:>3}  {row[COL_DRUG][:70]}")
    return rows


if __name__ == "__main__":
    pdf_to_dose_csv()
