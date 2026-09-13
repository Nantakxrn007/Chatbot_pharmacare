# -*- coding: utf-8 -*-
"""
แก้ "เลขหน้าอ้างอิง" ให้ตรงหน้า PDF จริง (หน้าที่เห็นตอนเปิดใน PDF reader)
================================================================================
ปัญหา (feedback: "เลขอ้างอิงที่โชว์เช่น p.39 ต้องตรงกับหน้า 39 จริงของ PDF"):
  md_chunker เขียน metadata `page` ของทุก chunk ในหนึ่ง section ด้วย "เลขหน้าตอน flush"
  (= หน้าสุดท้ายของ section นั้น) ไม่ใช่หน้าที่เนื้อของ chunk นั้นเริ่มจริง
  -> section ที่คร่อมหลายหน้าจะได้เลขหน้าเกินจริง (AAFP +1, URI มากสุด +7)
  -> frontend เปิด `<pdf>#page=<เลขที่อ้าง>` เป็นหน้า PDF จริง จึงเด้งผิดหน้า

วิธีแก้ (query-time, ไม่แตะ chunk / ไม่ re-embed / ไม่ ingest ใหม่):
  ไฟล์ .md ต้นทาง (rag/data/AAFP.md, URI.md) มี `<!-- PAGE N -->` กำกับทุกหน้า และยืนยันแล้วว่า
  marker ตรงกับหน้า PDF จริงทุกหน้า (เทียบข้อความที่ extract จาก PDF ตรงๆ) -> ใช้ .md เป็น
  source of truth หาว่า "เนื้อของ chunk นี้เริ่มที่ marker หน้าไหน" แล้ว override เลขหน้าตอนตอบ

  การจับตำแหน่ง: normalize ช่องว่างทิ้งทั้งสองฝั่ง แล้วค้นข้อความต้นของ chunk ใน .md แบบ
  "เดินหน้าไปข้างหน้าเท่านั้น" (monotonic cursor) เพราะ chunk เรียงตามลำดับเอกสาร
  -> กัน false match ไปโดนสารบัญ/หัวข้อซ้ำที่หน้าอื่น (เคสที่เจอจริงตอน verify)

หมายเหตุ: Dose (มาจาก CSV ที่มีคอลัมน์ Page ของตัวเอง) ตรงหน้า PDF จริงอยู่แล้ว --
ตรวจครบ 102 chunk ไม่มีเหลื่อม จึงไม่แตะ (map นี้ครอบเฉพาะเล่มที่มาจาก .md)
"""
from __future__ import annotations

import json
import re

from .config import CHUNKS_FILE, DATA_DIR, MD_FILES, PDF_FILENAMES

# ความยาว probe ที่ลองไล่จากยาวไปสั้น (ยาวก่อน = ชี้เฉพาะเจาะจงกว่า)
_PROBE_LENS = (200, 140, 100, 70, 50, 35)
# ระยะที่ยอมให้ถอยหลังจาก cursor (chunk ตาราง/หัวข้อแม่ถูก emit สลับลำดับกันได้เล็กน้อย)
_BACKTRACK = 4000

_PAGE_MARKER_RE = re.compile(r"<!-- PAGE (\d+) -->")
_PAGE_NUM_TAG_RE = re.compile(r"<page_number>.*?</page_number>", re.IGNORECASE | re.DOTALL)
_PAGE_NUM_TAG_OPEN_RE = re.compile(r"</?page_number[^>]*>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

_REAL_PAGE: dict[str, int] | None = None
_SPAN: dict[str, tuple[int, int]] = {}


def _norm(text: str) -> str:
    """ตัดช่องว่างและ tag เลขหน้าวารสารทิ้ง เพื่อเทียบ .md กับเนื้อ chunk ได้ตรง"""
    text = text.replace("\u200b", "")
    text = _PAGE_NUM_TAG_RE.sub("", text)
    text = _PAGE_NUM_TAG_OPEN_RE.sub("", text)
    return _WS_RE.sub("", text).lower()


def _load_md(path: str) -> tuple[str, list[tuple[int, int]]]:
    """(ข้อความ .md ที่ normalize แล้ว, [(offset ที่หน้านั้นเริ่ม, เลขหน้า)])"""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    parts: list[str] = []
    marks: list[tuple[int, int]] = []
    cursor = 0
    for piece in _PAGE_MARKER_RE.split(raw):
        if piece.isdigit() and marks[-1:] != [(cursor, int(piece))]:
            # re.split คืน group ที่จับได้สลับกับเนื้อ -> ตัวเลขล้วนคือเลขหน้าของ marker
            marks.append((cursor, int(piece)))
            continue
        chunk = _norm(piece)
        if chunk:
            parts.append(chunk)
            cursor += len(chunk)
    return "".join(parts), marks


def _page_at(offset: int, marks: list[tuple[int, int]]) -> int:
    page = 1
    for start, num in marks:
        if start <= offset:
            page = num
        else:
            break
    return page


def _chunk_body(content: str) -> str:
    """เนื้อ chunk ล้วน (ตัดหัว [Source: ...] / [Context: ...] ที่ chunker ใส่เพิ่ม ไม่มีใน .md)"""
    return "\n".join(
        ln for ln in (content or "").split("\n")
        if not ln.startswith(("[Source:", "[Context:"))
    ).strip()


def _build() -> dict[str, int]:
    """{chunk_id: หน้า PDF จริงที่เนื้อของ chunk เริ่ม} -- เฉพาะเล่มที่มีไฟล์ .md ต้นทาง
    เก็บ _SPAN = (หน้าเริ่ม, หน้าจบ) ของแต่ละ chunk ไว้ด้วย (chunk คร่อมหน้าได้)
    """
    rows_by_source: dict[str, list[dict]] = {}
    with open(CHUNKS_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("chunk_id"):
                rows_by_source.setdefault(row.get("source"), []).append(row)

    out: dict[str, int] = {}
    for md_path, source in MD_FILES:
        rows = rows_by_source.get(source)
        if not rows:
            continue
        try:
            text, marks = _load_md(md_path)
        except OSError as e:  # noqa: BLE001 -- ไม่มีไฟล์ .md -> เล่มนั้นใช้เลขหน้า metadata เดิม
            print(f"[PAGEMAP] อ่าน {md_path} ไม่ได้ ({e}) -> ข้าม {source}")
            continue
        cursor = 0
        found: list[tuple[str, int, int]] = []
        for row in rows:
            body = _norm(_chunk_body(row.get("content", "")))
            pos = -1
            for length in _PROBE_LENS:
                if len(body) < length:
                    continue
                probe = body[:length]
                pos = text.find(probe, cursor)
                if pos < 0:
                    pos = text.find(probe, max(0, cursor - _BACKTRACK))
                if pos >= 0:
                    break
            if pos < 0:   # ต้น chunk เพี้ยน (ตารางถูกจัดรูปใหม่) -> ลองยิงจากกลางเนื้อ
                for offset in (20, 40, 80, 150):
                    if len(body) > offset + 40:
                        pos = text.find(body[offset: offset + 40], max(0, cursor - _BACKTRACK))
                        if pos >= 0:
                            break
            if pos < 0:   # หาไม่เจอ -> คงเลขหน้า metadata เดิมไว้ (ไม่เดา)
                continue
            cursor = pos
            out[row["chunk_id"]] = _page_at(pos, marks)
            found.append((row["chunk_id"], pos, len(body)))
        # หน้าจบของแต่ละ chunk = หน้าที่ตำแหน่งท้ายเนื้อตกอยู่ (chunk เดียวคร่อมหลายหน้าได้ เช่น
        # URI_0053 = ท้ายหน้า 40 + "แผนภูมิที่ 3 acute sinusitis" ทั้งหน้า 41)
        for cid, pos, length in found:
            _SPAN[cid] = (_page_at(pos, marks), _page_at(pos + max(length - 1, 0), marks))
    return out


def _map() -> dict[str, int]:
    global _REAL_PAGE
    if _REAL_PAGE is None:
        try:
            _REAL_PAGE = _build()
            fixed = sum(1 for _ in _REAL_PAGE)
            print(f"[PAGEMAP] แมปหน้า PDF จริงได้ {fixed} chunk")
        except Exception as e:  # noqa: BLE001 -- ล้มเหลว = ใช้เลขหน้า metadata เดิม (ไม่ทำให้ตอบพัง)
            print(f"[PAGEMAP] สร้าง map ไม่ได้ ({e}) -> ใช้เลขหน้า metadata เดิม")
            _REAL_PAGE = {}
    return _REAL_PAGE


def real_page(chunk_id: str, meta_page):
    """หน้า PDF จริงของ chunk (คืน meta_page เดิมถ้าไม่มีข้อมูล)"""
    if not chunk_id:
        return meta_page
    return _map().get(chunk_id, meta_page)


def covered_pages(chunk_id: str, meta_page) -> list[int]:
    """ทุกหน้า PDF ที่เนื้อของ chunk นี้กินพื้นที่อยู่ (อย่างน้อย 1 หน้า)
    ใช้ตัดสินว่า "หน้านั้นเป็นเอกสารอ้างอิงล้วนจริงไหม" -- ถ้าดูแค่หน้าเริ่มของ chunk
    หน้าที่ถูกคร่อมด้วย chunk คลินิก (แต่ chunk ถัดไปที่เริ่มหน้านั้นเป็นบรรณานุกรม)
    จะถูกเหมาเป็นหน้าอ้างอิงผิด ๆ แล้วตัด citation ที่ถูกต้องทิ้ง
    """
    _map()
    span = _SPAN.get(chunk_id)
    if not span:
        try:
            return [int(meta_page)]
        except (TypeError, ValueError):
            return []
    start, end = span
    return list(range(min(start, end), max(start, end) + 1))


# ─── จำนวนหน้าจริงของแต่ละ PDF (อ่านจากไฟล์ ไม่ hardcode) ─────────────────────
# ใช้เป็น "ขอบเขตเลขหน้าที่อ้างได้" ทั้งในคำสั่ง prompt และตอน sanitize citation
# ต้องอ่านจากไฟล์เพราะเอกสารเปลี่ยนรุ่นได้ (ตาราง Dose ฉบับใหม่ = 45 หน้า ขณะที่ prompt
# เคย hardcode ไว้ "1-53" ซึ่งเป็นจำนวน *ตัวยา* ไม่ใช่จำนวนหน้า -> โมเดลอ้างเกินเล่มได้)
_PAGE_COUNT: dict[str, int] | None = None


def page_counts() -> dict[str, int]:
    """{source: จำนวนหน้าของ PDF เล่มนั้น} -- lazy, cached, พลาดแล้วไม่พังคำตอบ"""
    global _PAGE_COUNT
    if _PAGE_COUNT is not None:
        return _PAGE_COUNT
    out: dict[str, int] = {}
    try:
        import pypdf
        for src, fname in PDF_FILENAMES.items():
            path = DATA_DIR / fname
            try:
                out[src] = len(pypdf.PdfReader(str(path)).pages)
            except Exception as e:  # noqa: BLE001 -- เล่มเดียวอ่านไม่ได้ ไม่ทำให้เล่มอื่นพัง
                print(f"[PAGEMAP] นับหน้า {fname} ไม่ได้: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"[PAGEMAP] อ่านจำนวนหน้า PDF ไม่ได้ ({e}) -> ไม่จำกัดขอบเขตเลขหน้า")
    _PAGE_COUNT = out
    return out


def max_page(source: str) -> int | None:
    """หน้าสูงสุดที่อ้างได้ของเล่มนี้ (None = ไม่รู้ -> ไม่ตัด)"""
    return page_counts().get(source)


_HEADER_PAGE_RE = re.compile(r"(\|\s*Page:\s*)(\d+)")
_HEADER_PDF_PAGE_RE = re.compile(r"(#page=)(\d+)")


def fix_content_page(content: str, page) -> str:
    """แก้เลขหน้าในหัว [Source: ... | Page: N] / [Context: ... #page=N] ที่ฝังอยู่ในเนื้อ chunk
    -> โมเดลเห็นเลขหน้าที่ถูกต้องตัวเดียวกับที่ header ของ context บอก (กันอ้างเลขจากหัวเก่า)
    """
    if not content or page in (None, "", 0):
        return content
    head, sep, rest = content.partition("\n\n")
    if not sep or "[Source:" not in head:
        return content
    head = _HEADER_PAGE_RE.sub(lambda m: m.group(1) + str(page), head, count=1)
    head = _HEADER_PDF_PAGE_RE.sub(lambda m: m.group(1) + str(page), head, count=1)
    return head + sep + rest
