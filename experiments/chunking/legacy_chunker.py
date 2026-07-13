"""
Strategy A baseline สำหรับ experiment เท่านั้น
- Recursive text split (ก่อน Strategy C)
- Table parser แบบเก่า (บั๊กกลืนบรรทัดถัดไป)
ไม่ใช้ใน production
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.md_chunker import (
    ChunkConfig,
    build_journal_page_map,
    estimate_tokens,
    extract_heading_text,
    extract_page_num,
    heading_level,
    is_heading,
    is_table_end_html,
    is_table_row_md,
    is_table_start_html,
    pre_clean_text,
    resolve_journal_page,
    split_html_table_by_rows,
)


def split_into_blocks_legacy(md_text: str, cfg: ChunkConfig) -> list[dict]:
    """Parser แบบเก่า — table ไม่เช็ค </table> บรรทัดแรก"""
    lines = md_text.splitlines()
    blocks = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if cfg.skip_line_patterns:
            if any(re.search(pat, line, re.IGNORECASE) for pat in cfg.skip_line_patterns):
                i += 1
                continue

        if re.search(r"<!--\s*PAGE\s+\d+", line):
            blocks.append({"type": "page_marker", "content": line, "page": extract_page_num(line)})
            i += 1
            continue

        if is_heading(line, cfg):
            lvl = heading_level(line)
            if lvl <= cfg.max_heading_level:
                blocks.append({"type": "heading", "content": line, "level": lvl})
                i += 1
                continue

        if is_table_start_html(line):
            table_lines = [line]
            while i + 1 < len(lines):
                i += 1
                table_lines.append(lines[i])
                if is_table_end_html(lines[i]):
                    break
            blocks.append({"type": "table_html", "content": "\n".join(table_lines)})
            i += 1
            continue

        if is_table_row_md(line):
            table_lines = [line]
            while i + 1 < len(lines) and is_table_row_md(lines[i + 1]):
                i += 1
                table_lines.append(lines[i])
            blocks.append({"type": "table_md", "content": "\n".join(table_lines)})
            i += 1
            continue

        if line.strip() == "":
            blocks.append({"type": "blank", "content": ""})
            i += 1
            continue

        para_lines = [line]
        while (
            i + 1 < len(lines)
            and lines[i + 1].strip() != ""
            and not is_heading(lines[i + 1], cfg)
            and not is_table_start_html(lines[i + 1])
            and not is_table_row_md(lines[i + 1])
            and not re.search(r"<!--\s*PAGE", lines[i + 1])
        ):
            i += 1
            para_lines.append(lines[i])
        blocks.append({"type": "text", "content": "\n".join(para_lines)})
        i += 1

    return blocks


def build_chunks_strategy_a(
    blocks: list[dict],
    source_name: str,
    cfg: ChunkConfig,
    journal_map: dict[int, int] | None = None,
) -> list[dict]:
    """Strategy A: recursive split + text buffer ต่อเนื่อง (ไม่มี section buffer)"""
    chunks = []
    chunk_id = 0
    current_page = 1
    headings = {1: "", 2: "", 3: "", 4: ""}

    def make_chunk(content, ctype, page, h1, h2, h3):
        nonlocal chunk_id
        heading_path = " > ".join(filter(None, [h1, h2, h3]))
        if cfg.include_prefix:
            prefix = (
                f"[Source: {source_name} | Page: {page} | Section: {heading_path}]\n\n"
                if heading_path
                else f"[Source: {source_name} | Page: {page}]\n\n"
            )
        else:
            prefix = ""
        full = prefix + content.strip()
        c = {
            "chunk_id": f"{source_name}_{chunk_id:04d}",
            "source": source_name,
            "page": page,
            "heading": heading_path,
            "type": ctype,
            "content": full,
            "tokens_approx": estimate_tokens(full, cfg),
        }
        journal_page = resolve_journal_page(page, journal_map or {})
        if journal_page is not None:
            c["journal_page"] = journal_page
        chunk_id += 1
        return c

    def split_long_text(text, page, h1, h2, h3):
        separators = [r"\n\n+", r"\n", r"(?<=\.)\s+"]

        def do_split(txt, sep_idx):
            if estimate_tokens(txt, cfg) <= cfg.max_tokens or sep_idx >= len(separators):
                if estimate_tokens(txt, cfg) > cfg.max_tokens:
                    out = []
                    while txt:
                        out.append(txt[: cfg.max_chars])
                        txt = txt[cfg.max_chars :]
                    return out
                return [txt]

            parts = re.split(separators[sep_idx], txt)
            result = []
            buffer = ""
            for part in parts:
                if not part.strip():
                    continue
                candidate = (buffer + " " + part).strip() if buffer else part
                if estimate_tokens(candidate, cfg) > cfg.max_tokens and buffer:
                    result.append(buffer)
                    overlap = buffer[-cfg.overlap_chars :] if len(buffer) > cfg.overlap_chars else buffer
                    buffer = (overlap + " " + part).strip()
                else:
                    buffer = candidate
            if buffer.strip():
                result.append(buffer.strip())

            final = []
            for r in result:
                if estimate_tokens(r, cfg) > cfg.max_tokens:
                    final.extend(do_split(r, sep_idx + 1))
                else:
                    final.append(r)
            return final

        return [make_chunk(t, "text", page, h1, h2, h3) for t in do_split(text, 0)]

    text_buffer = ""

    def flush_text():
        nonlocal text_buffer
        if not text_buffer.strip():
            text_buffer = ""
            return
        h1, h2, h3 = headings[1], headings[2], headings[3]
        if estimate_tokens(text_buffer, cfg) <= cfg.max_tokens:
            chunks.append(make_chunk(text_buffer, "text", current_page, h1, h2, h3))
        else:
            chunks.extend(split_long_text(text_buffer, current_page, h1, h2, h3))
        text_buffer = ""

    for block in blocks:
        btype = block["type"]

        if btype == "page_marker":
            current_page = block.get("page") or current_page
        elif btype == "blank":
            if text_buffer:
                text_buffer += "\n\n"
        elif btype == "heading":
            flush_text()
            lvl = block["level"]
            htxt = extract_heading_text(block["content"])
            headings[lvl] = htxt
            for l in range(lvl + 1, 5):
                headings[l] = ""
        elif btype in ("table_html", "table_md"):
            flush_text()
            h1, h2, h3 = headings[1], headings[2], headings[3]
            if btype == "table_html" and cfg.table_chunk_mode == "row":
                sub_tables = split_html_table_by_rows(block["content"], cfg.table_rows_per_chunk)
                for sub in sub_tables:
                    chunks.append(make_chunk(sub, "table_html", current_page, h1, h2, h3))
            else:
                chunks.append(make_chunk(block["content"], btype, current_page, h1, h2, h3))
        elif btype == "text":
            text_buffer += block["content"] + "\n\n"
            if estimate_tokens(text_buffer, cfg) > cfg.max_tokens:
                flush_text()

    flush_text()
    return chunks


def chunk_strategy_a(md_path: str, source_name: str | None = None, config: ChunkConfig | None = None) -> list[dict]:
    cfg = config or ChunkConfig(overlap_tokens=100)
    md_path = Path(md_path)
    source_name = source_name or md_path.stem
    raw_text = md_path.read_text(encoding="utf-8")
    journal_map = build_journal_page_map(raw_text)
    text = pre_clean_text(raw_text, cfg)
    blocks = split_into_blocks_legacy(text, cfg)
    return build_chunks_strategy_a(blocks, source_name, cfg, journal_map=journal_map)
