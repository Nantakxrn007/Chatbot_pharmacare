"""
Generic Markdown Chunker
รับ .md ไหนก็ได้ → output .jsonl พร้อม embed
"""

import re
import json
from pathlib import Path
from dataclasses import dataclass, field

from backend.patient_group import infer_patient_group_from_chunk

# ─── Config ──────────────────────────────────────────────────────────────────

@dataclass
class ChunkConfig:
    # ── ขนาด chunk ──────────────────────────────────────────────────────────
    max_tokens        : int  = 500   # tokens สูงสุดต่อ chunk
    overlap_tokens    : int  = 80    # overlap ระหว่าง chunk (Strategy C)
    chars_per_token   : int  = 4     # 1 token ≈ 4 chars

    # ── heading ──────────────────────────────────────────────────────────────
    max_heading_level : int  = 4     # รับ # ถึง #### (1-4)
    max_heading_chars : int  = 150   # heading ยาวกว่านี้ = text (OCR แปลง paragraph เป็น ##)

    # ── filter tags ──────────────────────────────────────────────────────────
    skip_figure       : bool = True  # ตัด <figure>...</figure>
    skip_page_number  : bool = True  # ตัด <page_number>N</page_number>
    skip_tags         : list = field(default_factory=list)  # custom tags เพิ่มเติม

    # ── filter บรรทัด (footer / watermark / copyright) ───────────────────────
    skip_line_patterns: list = field(default_factory=lambda: [
        r'Downloaded from',
        r'For the private.*use of',
        r'All other rights reserved',
        r'CME This clinical content',
        r'Author disclosure:',
        r'Patient information:',
    ])

    # ── table chunking ──────────────────────────────────────────────────────
    # "full"     = เก็บตารางทั้งก้อน (default) — ข้อมูลครบ แต่ chunk ใหญ่
    # "row"      = ตัดทีละ N แถว พร้อม header แถวแรกติดทุก chunk
    table_chunk_mode  : str  = "full"
    table_rows_per_chunk: int = 5    # ใช้เมื่อ table_chunk_mode = "row"

    # ── prefix ──────────────────────────────────────────────────────────────
    include_prefix    : bool = True  # ติด [Source | Page | Section] ทุก chunk

    @property
    def max_chars(self):
        return self.max_tokens * self.chars_per_token

    @property
    def overlap_chars(self):
        return self.overlap_tokens * self.chars_per_token


# ─── Helpers ─────────────────────────────────────────────────────────────────

def estimate_tokens(text: str, cfg: ChunkConfig) -> int:
    return len(text) // cfg.chars_per_token


def is_heading(line: str, cfg: ChunkConfig) -> bool:
    """
    heading จริงต้องสั้น — OCR มักแปลง paragraph ยาวเป็น ## ด้วย
    กรอง superscript ออกก่อนวัดความยาว
    """
    if not re.match(r'^#{1,4}\s+', line):
        return False
    text = re.sub(r'^#{1,4}\s+', '', line).strip()
    # ลบ superscript unicode และ footnote marker ออกก่อนวัด
    text_clean = re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ\u00b0-\u00b9\u2070-\u209f]', '', text)
    text_clean = re.sub(r'\[\d+\]$', '', text_clean).strip()
    return len(text_clean) <= cfg.max_heading_chars


def heading_level(line: str) -> int:
    m = re.match(r'^(#{1,4})\s+', line)
    return len(m.group(1)) if m else 0


def extract_heading_text(line: str) -> str:
    return re.sub(r'^#{1,4}\s+', '', line).strip()


def is_table_start_html(line: str) -> bool:
    return bool(re.search(r'<table', line, re.IGNORECASE))


def is_table_end_html(line: str) -> bool:
    return bool(re.search(r'</table>', line, re.IGNORECASE))


def is_table_row_md(line: str) -> bool:
    return bool(re.match(r'^\s*\|', line))


def extract_page_num(line: str):
    m = re.search(r'PAGE\s+(\d+)', line)
    return int(m.group(1)) if m else None


def build_journal_page_map(md_text: str) -> dict[int, int]:
    """
    แมป PDF page (<!-- PAGE N -->) → เลขหน้าวารสาร (629, 632, ...)
    อ่านจาก raw .md ก่อน pre_clean ลบ <page_number> tag
    """
    journal_by_pdf: dict[int, int] = {}
    current_pdf = 1

    for line in md_text.splitlines():
        pm = re.search(r'<!--\s*PAGE\s+(\d+)\s*-->', line)
        if pm:
            current_pdf = int(pm.group(1))

        ptag = re.search(r'<page_number>\s*(\d+)\s*</page_number>', line, re.IGNORECASE)
        if ptag:
            journal_by_pdf[current_pdf] = int(ptag.group(1))
            continue

        jm = re.search(r'American Family Physician\s+(\d{3})\b', line)
        if jm:
            journal_by_pdf[current_pdf] = int(jm.group(1))

    cite = re.search(r'Am Fam Physician\.\s*\d{4};\d+\(\d+\):(\d+)', md_text)
    if cite:
        journal_by_pdf.setdefault(1, int(cite.group(1)))

    return journal_by_pdf


def resolve_journal_page(pdf_page: int, journal_map: dict[int, int]) -> int | None:
    """เลขหน้าวารสารล่าสุดที่รู้จักสำหรับ PDF page นี้"""
    if not journal_map:
        return None
    if pdf_page in journal_map:
        return journal_map[pdf_page]
    for p in range(pdf_page, 0, -1):
        if p in journal_map:
            return journal_map[p]
    return None


# ─── Pre-clean ────────────────────────────────────────────────────────────────

def pre_clean_text(text: str, cfg: ChunkConfig) -> str:
    """ลบ tag ขยะทั้งหมดก่อน parse (กันกรณี tag ติดกันในบรรทัดเดียว)"""
    if cfg.skip_figure:
        text = re.sub(r'<figure.*?</figure>', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'</?figure[^>]*>', '', text, flags=re.IGNORECASE)
    if cfg.skip_page_number:
        text = re.sub(r'<page_number>.*?</page_number>', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'</?page_number[^>]*>', '', text, flags=re.IGNORECASE)
    for tag in cfg.skip_tags:
        tag_name = re.sub(r'[<>/]', '', tag).split()[0]
        text = re.sub(rf'<{tag_name}.*?</{tag_name}>', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(rf'</?{tag_name}[^>]*>', '', text, flags=re.IGNORECASE)
    return text


# ─── Block Parser ────────────────────────────────────────────────────────────

def split_into_blocks(md_text: str, cfg: ChunkConfig) -> list[dict]:
    lines  = md_text.splitlines()
    blocks = []
    i      = 0

    while i < len(lines):
        line = lines[i]

        # ── skip_line_patterns (footer/watermark) ─────────────────────────
        if cfg.skip_line_patterns:
            if any(re.search(pat, line, re.IGNORECASE) for pat in cfg.skip_line_patterns):
                i += 1
                continue

        # ── PAGE marker ───────────────────────────────────────────────────
        if re.search(r'<!--\s*PAGE\s+\d+', line):
            blocks.append({"type": "page_marker", "content": line, "page": extract_page_num(line)})
            i += 1
            continue

        # ── Heading ───────────────────────────────────────────────────────
        if is_heading(line, cfg):
            lvl = heading_level(line)
            if lvl <= cfg.max_heading_level:
                blocks.append({"type": "heading", "content": line, "level": lvl})
                i += 1
                continue

        # ── HTML table ────────────────────────────────────────────────────
        if is_table_start_html(line):
            table_start = re.search(r'<table', line, re.IGNORECASE).start()
            if table_start > 0:
                prefix = line[:table_start].strip()
                if prefix:
                    blocks.append({"type": "text", "content": prefix})
                line = line[table_start:]

            table_lines = [line]
            if not is_table_end_html(line):
                while i + 1 < len(lines):
                    i += 1
                    table_lines.append(lines[i])
                    if is_table_end_html(lines[i]):
                        break

            table_content = "\n".join(table_lines)
            blocks.append({"type": "table_html", "content": table_content})

            last_line = table_lines[-1]
            end_m = re.search(r'</table>', last_line, re.IGNORECASE)
            if end_m:
                suffix = last_line[end_m.end():].strip()
                if suffix:
                    blocks.append({"type": "text", "content": suffix})

            i += 1
            continue

        # ── Markdown table ────────────────────────────────────────────────
        if is_table_row_md(line):
            table_lines = [line]
            while i + 1 < len(lines) and is_table_row_md(lines[i + 1]):
                i += 1
                table_lines.append(lines[i])
            blocks.append({"type": "table_md", "content": "\n".join(table_lines)})
            i += 1
            continue

        # ── บรรทัดว่าง ────────────────────────────────────────────────────
        if line.strip() == "":
            blocks.append({"type": "blank", "content": ""})
            i += 1
            continue

        # ── paragraph ─────────────────────────────────────────────────────
        para_lines = [line]
        while (
            i + 1 < len(lines)
            and lines[i + 1].strip() != ""
            and not is_heading(lines[i + 1], cfg)
            and not is_table_start_html(lines[i + 1])
            and not is_table_row_md(lines[i + 1])
            and not re.search(r'<!--\s*PAGE', lines[i + 1])
        ):
            i += 1
            para_lines.append(lines[i])
        blocks.append({"type": "text", "content": "\n".join(para_lines)})
        i += 1

    return blocks


# ─── Table Splitter ──────────────────────────────────────────────────────────

def split_html_table_by_rows(table_html: str, rows_per_chunk: int) -> list[str]:
    """
    ตัดตาราง HTML ทีละ N แถว พร้อมติด header แถวแรกทุก chunk
    คืน list ของ table HTML string
    """
    # ดึง header row แรก (<tr> แรก)
    header_match = re.search(r'(<table[^>]*>)\s*(<tr>.*?</tr>)', table_html, re.IGNORECASE | re.DOTALL)
    if not header_match:
        return [table_html]  # parse ไม่ได้ → คืนทั้งก้อน

    table_open  = header_match.group(1)   # <table ...>
    header_row  = header_match.group(2)   # <tr>header</tr>

    # ดึง data rows ที่เหลือ (ข้าม header)
    after_header = table_html[header_match.end():]
    data_rows    = re.findall(r'<tr>.*?</tr>', after_header, re.IGNORECASE | re.DOTALL)

    if not data_rows:
        return [table_html]

    chunks = []
    for i in range(0, len(data_rows), rows_per_chunk):
        batch      = data_rows[i:i + rows_per_chunk]
        parts      = [table_open, header_row] + batch + ["</table>"]
        chunk_html = "\n".join(parts)
        chunks.append(chunk_html)

    return chunks if chunks else [table_html]


# ─── Chunk Builder ───────────────────────────────────────────────────────────

def build_chunks(blocks: list[dict], source_name: str, cfg: ChunkConfig,
                 journal_map: dict[int, int] | None = None) -> list[dict]:
    """
    Strategy C: section buffer + sliding window + table isolation + patient_group
    """
    chunks         = []
    chunk_id       = 0
    current_page   = 1
    headings       = {1: "", 2: "", 3: "", 4: ""}
    section_buffer = []

    def heading_path():
        return " > ".join(filter(None, [headings[1], headings[2], headings[3]]))

    def make_chunk(content, ctype, page, hpath):
        nonlocal chunk_id
        if cfg.include_prefix:
            src_prefix = (
                f"[Source: {source_name} | Page: {page} | Section: {hpath}]\n\n"
                if hpath
                else f"[Source: {source_name} | Page: {page}]\n\n"
            )
            ctx = f"[Context: {hpath}]\n" if hpath else ""
            full = src_prefix + ctx + content.strip()
        else:
            full = content.strip()

        patient_group = infer_patient_group_from_chunk(content, source_name, hpath)
        c = {
            "chunk_id"     : f"{source_name}_{chunk_id:04d}",
            "source"       : source_name,
            "page"         : page,
            "heading"      : hpath,
            "type"         : ctype,
            "content"      : full,
            "tokens_approx": estimate_tokens(full, cfg),
            "patient_group": patient_group,
        }
        journal_page = resolve_journal_page(page, journal_map or {})
        if journal_page is not None:
            c["journal_page"] = journal_page
        chunk_id += 1
        return c

    def flush_section_buffer(page, hpath):
        text_parts    = [b["content"] for b in section_buffer if b["type"] == "text"]
        text_combined = "\n\n".join(text_parts).strip()
        if not text_combined:
            return []

        if estimate_tokens(text_combined, cfg) <= cfg.max_tokens:
            return [make_chunk(text_combined, "text", page, hpath)]

        result_chunks = []
        paragraphs    = re.split(r'\n{2,}', text_combined)
        buffer        = ""
        for para in paragraphs:
            if not para.strip():
                continue
            candidate = (buffer + "\n\n" + para).strip() if buffer else para
            if estimate_tokens(candidate, cfg) > cfg.max_tokens and buffer:
                result_chunks.append(make_chunk(buffer, "text", page, hpath))
                overlap = buffer[-cfg.overlap_chars:] if len(buffer) > cfg.overlap_chars else buffer
                buffer  = (overlap + "\n\n" + para).strip()
            else:
                buffer = candidate
        if buffer.strip():
            result_chunks.append(make_chunk(buffer, "text", page, hpath))
        return result_chunks

    for block in blocks:
        btype = block["type"]

        if btype == "page_marker":
            current_page = block.get("page") or current_page

        elif btype == "blank":
            pass

        elif btype == "heading":
            if section_buffer:
                chunks.extend(flush_section_buffer(current_page, heading_path()))
                section_buffer = []
            lvl  = block["level"]
            htxt = extract_heading_text(block["content"])
            headings[lvl] = htxt
            for l in range(lvl + 1, 5):
                headings[l] = ""

        elif btype in ("table_html", "table_md"):
            if section_buffer:
                chunks.extend(flush_section_buffer(current_page, heading_path()))
                section_buffer = []

            hpath         = heading_path()
            table_heading = f"{hpath} > Table" if hpath else "Table"

            if btype == "table_html" and cfg.table_chunk_mode == "row":
                sub_tables = split_html_table_by_rows(block["content"], cfg.table_rows_per_chunk)
                for sub in sub_tables:
                    chunks.append(make_chunk(sub, "table_html", current_page, table_heading))
            else:
                chunks.append(make_chunk(block["content"], btype, current_page, table_heading))

        elif btype == "text":
            section_buffer.append(block)

    if section_buffer:
        chunks.extend(flush_section_buffer(current_page, heading_path()))

    return chunks


# ─── Public API ──────────────────────────────────────────────────────────────

def chunk_md_file(md_path: str, source_name: str = None, config: ChunkConfig = None) -> list[dict]:
    """
    แปลง .md → list of chunk dict

    Args:
        md_path     : path ไฟล์ .md
        source_name : ชื่อ source (default = ชื่อไฟล์)
        config      : ChunkConfig (default = ค่า default ทั้งหมด)
    """
    cfg         = config or ChunkConfig()
    md_path     = Path(md_path)
    source_name = source_name or md_path.stem
    raw_text    = md_path.read_text(encoding="utf-8")
    journal_map = build_journal_page_map(raw_text)
    text        = pre_clean_text(raw_text, cfg)
    blocks      = split_into_blocks(text, cfg)
    return build_chunks(blocks, source_name, cfg, journal_map=journal_map)


def save_chunks_jsonl(chunks: list[dict], output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"[SAVE] {len(chunks)} chunks -> {output_path}")


def print_summary(chunks: list[dict]):
    types  = {}
    tokens = [c["tokens_approx"] for c in chunks]
    for c in chunks:
        types[c["type"]] = types.get(c["type"], 0) + 1
    print(f"\n{'='*50}")
    print(f"[SUMMARY] total  : {len(chunks)} chunks")
    print(f"[SUMMARY] tokens : avg={sum(tokens)//len(tokens) if tokens else 0}  min={min(tokens) if tokens else 0}  max={max(tokens) if tokens else 0}")
    print(f"[SUMMARY] types  :")
    for t, n in sorted(types.items()):
        print(f"           {t:<15} {n} chunks")
    print(f"{'='*50}\n")