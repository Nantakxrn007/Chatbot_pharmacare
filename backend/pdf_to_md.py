# pip install typhoon-ocr pypdf python-dotenv
import os
import sys
import io
import time
import traceback
from datetime import datetime
from typhoon_ocr import ocr_document
from dotenv import load_dotenv

load_dotenv()


# ─── ตั้งค่า ─────────────────────────────────────────────────────────────────

API_KEY = os.getenv("TYPHOON_API_KEY")
DELAY   = 3.5  # วินาที ระหว่างหน้า (rate limit 20 req/min)
RETRIES = 5    # retry สูงสุดต่อหน้า

# ─── Logger ──────────────────────────────────────────────────────────────────

LOG_FILE = "ocr_log.txt"

def log(level, msg):
    """print + เขียนลงไฟล์ log พร้อม timestamp"""
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def log_info(msg):    log("INFO ", msg)
def log_ok(msg):      log("OK   ", msg)
def log_warn(msg):    log("WARN ", msg)
def log_error(msg):   log("ERROR", msg)
def log_sep():
    line = "-" * 55
    print(line); 
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ─── Functions ───────────────────────────────────────────────────────────────

def get_page_count(pdf_path):
    """นับจำนวนหน้า PDF"""
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    return len(reader.pages)


def ocr_one_page(pdf_path, page_num):
    """
    OCR หน้าเดียว พร้อม retry + exponential backoff
    
    Error handling:
    ┌─────────────────────────┬──────────────────────────────────────┐
    │ Error                   │ Action                               │
    ├─────────────────────────┼──────────────────────────────────────┤
    │ 429 Rate limit          │ backoff: 10 → 20 → 40 → 80 → 160s  │
    │ API Key ผิด / 401       │ หยุดทันที ไม่ retry (ไม่มีประโยชน์) │
    │ Timeout / Network       │ รอ 10s แล้ว retry                   │
    │ Error อื่นๆ             │ รอ 10s แล้ว retry                   │
    │ ล้มเหลวครบ {RETRIES} ครั้ง │ return None (บันทึกใน fail_pages) │
    └─────────────────────────┴──────────────────────────────────────┘
    """
    os.environ["TYPHOON_OCR_API_KEY"] = API_KEY

    for attempt in range(1, RETRIES + 1):
        try:
            log_info(f"  หน้า {page_num} | attempt {attempt}/{RETRIES} | เริ่ม OCR...")
            t0 = time.time()

            result = ocr_document(
                pdf_or_image_path=pdf_path,
                page_num=page_num,
                model="typhoon-ocr",
                figure_language="Thai",
                task_type="v1.5",
            )

            elapsed = time.time() - t0
            chars   = len(result) if result else 0
            log_ok(f"  หน้า {page_num} | สำเร็จ | {chars} chars | {elapsed:.1f}s")
            return result

        except Exception as e:
            elapsed   = time.time() - t0
            err_str   = str(e).lower()
            err_full  = traceback.format_exc().strip().splitlines()[-1]  # บรรทัดสุดท้าย

            # ─── จำแนกประเภท error ─────────────────────────────────────────

            # 401 Unauthorized — key ผิด หยุดทันที
            if "401" in str(e) or "unauthorized" in err_str or "invalid api key" in err_str:
                log_error(f"  หน้า {page_num} | API Key ผิดหรือหมดอายุ — หยุดทันที")
                log_error(f"  Detail: {err_full}")
                log_error(f"  กรุณาตรวจสอบ TYPHOON_API_KEY ใน .env")
                raise SystemExit(1)

            # 429 Rate limit
            elif "429" in str(e) or "rate limit" in err_str:
                wait = 10 * (2 ** (attempt - 1))  # 10 → 20 → 40 → 80 → 160
                log_warn(f"  หน้า {page_num} | Rate limit (429) | รอ {wait}s | attempt {attempt}/{RETRIES}")

            # Timeout / Connection error
            elif any(k in err_str for k in ["timeout", "connection", "network", "socket"]):
                wait = 10
                log_warn(f"  หน้า {page_num} | Network error | รอ {wait}s | attempt {attempt}/{RETRIES}")
                log_warn(f"  Detail: {err_full}")

            # Error อื่นๆ
            else:
                wait = 10
                log_warn(f"  หน้า {page_num} | Error | รอ {wait}s | attempt {attempt}/{RETRIES}")
                log_warn(f"  Detail: {err_full}")

            # ─── ครบ retry แล้ว ────────────────────────────────────────────
            if attempt == RETRIES:
                log_error(f"  หน้า {page_num} | ล้มเหลวครบ {RETRIES} ครั้ง | ข้ามหน้านี้ไป")
                log_error(f"  Full traceback:\n{traceback.format_exc()}")
                return None

            time.sleep(wait)

    return None


def pdf_to_md(pdf_path, output_path, start_page=1, end_page=None):
    """
    แปลง PDF ทุกหน้าเป็น Markdown

    Args:
        pdf_path    : path ไฟล์ PDF  เช่น "pharmacy.pdf"
        output_path : path ไฟล์ .md  เช่น "pharmacy.md"
        start_page  : หน้าเริ่มต้น (default 1)
        end_page    : หน้าสุดท้าย  (default = ทำทุกหน้า)

    Returns:
        fail_pages  : list หน้าที่ล้มเหลว  ([] = สำเร็จทั้งหมด)
    """

    # เคลียร์ log เก่า
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== OCR Log เริ่ม {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")

    checkpoint_path = output_path.replace(".md", "_checkpoint.txt")

    # ─── ตรวจสอบ API Key ───────────────────────────────────────────────────
    if not API_KEY:
        log_error("ไม่พบ TYPHOON_API_KEY ใน .env — หยุดทำงาน")
        raise SystemExit(1)
    log_info(f"API Key: ...{API_KEY[-6:]} (แสดงแค่ 6 ตัวท้าย)")

    # ─── ตรวจสอบไฟล์ PDF ──────────────────────────────────────────────────
    if not os.path.exists(pdf_path):
        log_error(f"ไม่พบไฟล์ PDF: {pdf_path}")
        raise SystemExit(1)
    file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
    log_info(f"ไฟล์ PDF: {pdf_path} ({file_size_mb:.1f} MB)")

    total    = get_page_count(pdf_path)
    end_page = end_page or total
    pages    = list(range(start_page, end_page + 1))

    log_sep()
    log_info(f"[PDF]    {pdf_path}")
    log_info(f"[Output] {output_path}")
    log_info(f"[Pages]  {start_page} - {end_page}  ({len(pages)} หน้า / ทั้งหมด {total} หน้า)")
    log_info(f"[Delay]  {DELAY}s ต่อหน้า  (rate limit safe)")
    log_info(f"[Time]   ~{len(pages) * DELAY / 60:.1f} นาที")
    log_sep()

    # ─── โหลด checkpoint ──────────────────────────────────────────────────
    done_pages = set()
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, encoding="utf-8") as f:
            done_pages = {int(l.strip()) for l in f if l.strip()}
        log_info(f"[Resume] พบ checkpoint — ข้ามหน้าที่ทำไปแล้ว {len(done_pages)} หน้า")

    results    = {}
    fail_pages = []
    time_start = time.time()

    # ─── Main Loop ────────────────────────────────────────────────────────
    for i, page_num in enumerate(pages, 1):

        if page_num in done_pages:
            log_info(f"[SKIP {i}/{len(pages)}] หน้า {page_num} (checkpoint)")
            continue

        log_sep()
        log_info(f"[PAGE {i}/{len(pages)}] หน้า {page_num} เริ่ม OCR")
        t0 = time.time()

        md      = ocr_one_page(pdf_path, page_num)
        elapsed = time.time() - t0

        if md:
            results[page_num] = md
            done_pages.add(page_num)
            # บันทึก checkpoint ทุกหน้า
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                f.write("\n".join(str(p) for p in sorted(done_pages)))

            done_count      = len(results)
            remaining_count = len(pages) - i
            avg_time        = (time.time() - time_start) / done_count
            eta_min         = (remaining_count * avg_time) / 60
            log_ok(f"[PAGE {i}/{len(pages)}] หน้า {page_num} | {len(md)} chars | เสร็จแล้ว {done_count} หน้า | ETA ~{eta_min:.1f} นาที")
        else:
            fail_pages.append(page_num)
            log_error(f"[PAGE {i}/{len(pages)}] หน้า {page_num} FAIL — ข้ามไป")

        # รอ rate limit (ยกเว้นหน้าสุดท้าย)
        remaining = len([p for p in pages if p not in done_pages])
        if remaining > 0:
            sleep = max(0, DELAY - elapsed)
            if sleep > 0:
                log_info(f"  รอ {sleep:.1f}s (rate limit delay)...")
                time.sleep(sleep)

    # ─── เขียน output ─────────────────────────────────────────────────────
    log_sep()
    log_info(f"เขียนไฟล์ {output_path} ...")
    with open(output_path, "w", encoding="utf-8") as f:
        for page_num in sorted(results.keys()):
            f.write(f"\n\n<!-- PAGE {page_num} -->\n\n")
            f.write(results[page_num])
            f.write("\n")

    total_time = (time.time() - time_start) / 60

    # ─── สรุปผล ───────────────────────────────────────────────────────────
    log_sep()
    log_ok(f"[DONE] สำเร็จ {len(results)}/{len(pages)} หน้า | ใช้เวลา {total_time:.1f} นาที")
    log_info(f"[LOG]  ดู log ทั้งหมดที่: {LOG_FILE}")
    if fail_pages:
        log_error(f"[FAIL] หน้าที่ล้มเหลว: {fail_pages}")
        log_warn(f"       run ใหม่ได้เลย จะ resume ต่อเองครับ")
    else:
        log_ok(f"[DONE] สำเร็จทั้งหมด — ลบ checkpoint แล้ว")
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)

    return fail_pages
