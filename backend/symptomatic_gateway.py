# -*- coding: utf-8 -*-
"""
Symptomatic Gateway (Phase 2 optimize 1) -- query-time only, no vector/chunk edits
==================================================================================
ปัญหาเดิม (จาก feedback หน้างานจริง):
  - cosine ดึง Dose ได้แค่ 1-3 ตัว และมักเป็น "ยาสูตรผสม" (chunk สูตรผสมพูดถึงหลายอาการ -> similarity สูง)
    -> AI แนะนำยาเดิมๆ ซ้ำ (CPM+Phenylephrine / Decolgen) แม้อาการไม่เข้า (ไม่มีน้ำมูก, น้ำมูกข้นเหนียว, ไซนัส)
  - ขาด "ตัวเลือก" (choice) ตามสถานการณ์ และเสี่ยงเชิงโฆษณา (ตอบชื่อการค้าอย่างเดียว)

แนวทาง (dynamic, ไม่ hard-code รายเคส):
  1) Formulary: สร้างคลังยาจาก Dose chunks (chunks.jsonl -- อ่านอย่างเดียว) จัดกลุ่มยาจาก "ข้อบ่งใช้" ในตารางเอง
  2) Case features: สกัดอาการจากข้อความเคส (รองรับคำปฏิเสธภาษาไทย เช่น "ไม่มีน้ำมูก", "ไม่ไอ")
  3) Gateway: ตัดสินรายกลุ่มยา fit / conditional / avoid ตามหลักเภสัชวิทยา + ข้อความข้อบ่งใช้ใน Dose table
     (เช่น สูตรผสม decongestant+antihistamine ต้องมีทั้งน้ำมูกไหลและคัดจมูก, ยาแก้แพ้รุ่นที่ 1 ไม่เหมาะน้ำมูกข้นเหนียว)
  4) Catalog: แนบ "คลังยาตามอาการ" (ทุกตัวเลือกในกลุ่มที่เหมาะ + ขนาดยาตัดตอนตรงจากตาราง + เลขหน้า) เข้า Context
     และตัด Dose chunk ที่ gateway ตัดสินว่า "ไม่เหมาะกับเคสนี้" ออกจาก Context
"""

from __future__ import annotations

import json
import re

from backend.config import CHUNKS_FILE
from backend.patient_group import infer_patient_group_from_query

# ─── Formulary (Dose table -> in-memory catalog) ─────────────────────────────

_FIELD_RE = re.compile(
    r"^(Drug|Indication|Dose \(ผู้ใหญ่\)|Dose \(เด็ก\)|Renal/Hepatic Adjustment|Warnings/Contraindications):\s*(.*)$"
)
_EMPTY_VALUES = {"", "❌", "-", "–", "—"}
# antihistamine รุ่นที่ 1 (sedating, ลดสารคัดหลั่งแรง) -- ความรู้เภสัชวิทยาระดับกลุ่มยา ไม่ใช่รายเคส
_FIRST_GEN_AH = ("chlorpheniramine", "brompheniramine", "diphenhydramine", "cyproheptadine", "hydroxyzine")

_FORMULARY: list[dict] | None = None


def _nz(text: str) -> str:
    """ตัดช่องว่างทั้งหมด + lower (ข้อความไทยจาก PDF มีช่องว่างแทรกกลางคำ)"""
    return re.sub(r"\s+", "", text or "").lower()


def _display_name(name: str) -> str:
    """ชื่อที่ใช้แสดง: ชื่อ + วงเล็บตัวยาแรก (ตัดส่วนอธิบายยาวท้ายชื่อ) / ตำรับไทยใช้ 2 คำแรก (ตัดตรายี่ห้อ)"""
    if re.match(r"^[฀-๿]", name):
        return " ".join(name.split()[:2])
    if "(" in name and ")" in name:
        return name[: name.index(")") + 1].strip()
    return name.strip()


def _name_keys(name: str) -> list[str]:
    """คำที่ใช้จับชื่อยานี้ในคำตอบ (ยาวก่อน) เช่น 'Decolgen prin', 'TIFFY DEY', 'Chlorpheniramine + Phenylephrine'"""
    head = re.split(r"\s*\(", name)[0]
    keys: set[str] = set()
    for part in re.split(r"\s*/\s*", head):
        part = part.strip()
        if not part:
            continue
        keys.add(part)
        if "+" in part:
            items = [x.strip() for x in part.split("+") if x.strip()]
            keys.add(" + ".join(reversed(items)))
    m = re.search(r"\(([^)]*)\)", name)
    if m and not re.search(r"\d|มีตัวยา|มีสาร|ใน\s", m.group(1)):
        keys.add(m.group(1).strip())          # ชื่อพ้อง เช่น N-Acetylcysteine
    if re.match(r"^[฀-๿]", name):
        toks = head.split()
        keys.add(toks[1] if len(toks) > 1 else toks[0])
    for k in list(keys):
        k2 = re.sub(r"\s+(?:HCl|\d+\s*ml)$", "", k, flags=re.IGNORECASE).strip()
        if k2:
            keys.add(k2)
    return sorted((k for k in keys if len(k) >= 4), key=len, reverse=True)


def _min_age(drug: dict) -> float | None:
    """อายุขั้นต่ำที่ตาราง Dose ระบุ (เช่น 'เด็กอายุ 6 ปีขึ้นไป', 'children >12 yr', 'ห้ามใช้เด็กอายุต่ำกว่า 1 ปี')"""
    dose_txt = _nz(drug.get("ped") or drug.get("adult"))
    warn_txt = _nz(drug.get("warn"))
    found = [float(x) for x in re.findall(r"อายุ(?:ตั้งแต่)?(\d+)ปีขึ้นไป", dose_txt)]
    found += [float(x) for x in re.findall(r"children(?:over|>)(\d+)(?:yr|years?)", dose_txt)]
    if found:
        return min(found)
    neg = re.findall(r"(?:ไม่ควรใช้|ห้ามใช้|ไม่แนะนำให้ใช้)(?:ใน|กับ)?เด็กอายุต่ำกว่า(\d+)", warn_txt + dose_txt)
    return float(max(neg, key=float)) if neg else None


def _classify(d: dict) -> dict:
    ind = _nz(d["indication"])
    low = d["name"].lower()
    not_rhino = "ไม่ค่อยใช้ลดน้ำมูก" in ind
    has_ah = ("antihistamine" in ind or "ลดน้ำมูก" in ind) and not not_rhino
    has_dc = "decongestant" in ind or "แก้คัดจมูก" in ind
    has_ap = any(k in ind for k in ("antipyretic", "analgesic", "ลดไข้", "แก้ปวด"))
    throat = any(k in ind for k in ("เจ็บคอ", "ระคายเคืองคอ", "เสียงแหบ"))
    cls: set[str] = set()
    if has_ap and has_ah and has_dc:
        cls.add("combo_flu")
    elif has_ah and has_dc:
        cls.add("combo_cold")
    elif has_ap:
        cls.add("fever_pain")
    elif has_ah:
        cls.add("antihistamine")
    elif has_dc:
        cls.add("decongestant")
    if "corticoster" in ind:
        cls.add("incs")
    if "ไอแห้ง" in ind or "ไม่มีเสมหะ" in ind:
        cls.add("cough_dry")
    if re.search(r"(?<!ไม่)มีเสมหะ|ขับเสมหะ|ละลายเสมหะ", ind):
        cls.add("cough_wet")
    if "ยาพ่น" in ind and throat:
        cls.add("throat_spray")
    if "ยาอม" in ind and throat:
        cls.add("throat_lozenge")
    if "กลั้วคอ" in ind:
        cls.add("gargle")
    # รูปแบบยาเฉพาะที่คอ (ใช้เรียกหมวดให้ถูก: ยาพ่นคอ / ยาอม / ยากลั้วคอ)
    form = None
    if "throat_spray" in cls:
        form = "spray"
    elif "throat_lozenge" in cls or ind.startswith("ยาอม"):
        form = "lozenge"
    elif "gargle" in cls:
        form = "gargle"
    return {
        "classes": cls,
        "first_gen": "+" not in d["name"] and any(k in low for k in _FIRST_GEN_AH),
        "herbal": bool(re.match(r"^[฀-๿]", d["name"])),
        "nsaid": "fever_pain" in cls and "paracetamol" not in low,
        "product": bool(re.search(r"\([^)]*(?:มีตัวยา|\d)", d["name"])) or bool(_PRODUCT_RE.match(d["name"])),
        "rare_uri": any(k in low for k in _RARE_URI),
        "form": form,
    }


# ผลิตภัณฑ์ (ชื่อการค้า/สูตรผสม) -- จัดเป็น "ผลิตภัณฑ์ทางเลือก" ต่อจากยาหลักชื่อสามัญ (กันเชิงโฆษณา + ยาหลักขึ้นก่อน)
_PRODUCT_RE = re.compile(r"^(?:Solmax|Muclear|Strepsils|Terco|Decolgen|Difflam|Kamill?osan|Propoliz|Betadine)", re.IGNORECASE)
# มีในตาราง Dose แต่ไม่ใช่ตัวเลือกทั่วไปสำหรับอาการ URI (แสดงเฉพาะเมื่อผู้ใช้ขอดูทั้งหมด) -- ความรู้ระดับกลุ่มยา
_RARE_URI = ("aspirin", "piroxicam", "celecoxib", "etoricoxib", "diphenhydramine", "cyproheptadine")
FORM_LABELS = {"spray": "ยาพ่นคอ", "lozenge": "ยาอม", "gargle": "ยากลั้วคอ"}


def load_formulary() -> list[dict]:
    """คลังยาจาก Dose chunks (lazy, cached) -- 1 รายการต่อยา รวมแถวผู้ใหญ่+เด็ก"""
    global _FORMULARY
    if _FORMULARY is not None:
        return _FORMULARY
    drugs: dict[str, dict] = {}
    order: list[str] = []
    try:
        with open(CHUNKS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("source") != "Dose":
                    continue
                fields: dict[str, str] = {}
                for ln in (row.get("content") or "").splitlines():
                    m = _FIELD_RE.match(ln.strip())
                    if m:
                        fields[m.group(1)] = m.group(2).strip()
                name = fields.get("Drug")
                if not name:
                    continue
                d = drugs.get(name)
                if d is None:
                    d = {"name": name, "page": str(row.get("page", "")), "indication": fields.get("Indication", ""),
                         "adult": "", "ped": "", "renal": "", "warn": "", "chunk_ids": []}
                    drugs[name] = d
                    order.append(name)
                d["chunk_ids"].append(row.get("chunk_id"))
                for key, fk in (("adult", "Dose (ผู้ใหญ่)"), ("ped", "Dose (เด็ก)"),
                                ("renal", "Renal/Hepatic Adjustment"), ("warn", "Warnings/Contraindications")):
                    val = fields.get(fk, "")
                    if val and val not in _EMPTY_VALUES and not d[key]:
                        d[key] = val
    except Exception as e:  # noqa: BLE001
        print(f"[SYMPT] formulary load failed (gateway disabled): {e}")
    out: list[dict] = []
    for n in order:
        d = drugs[n]
        d.update(_classify(d))
        d["display"] = _display_name(n)
        d["keys"] = _name_keys(n)
        d["min_age"] = _min_age(d)
        out.append(d)
    _FORMULARY = out
    return out


def drug_by_name(name: str) -> dict | None:
    for d in load_formulary():
        if d["name"] == name:
            return d
    return None


# ─── Case features (Thai-negation aware) ─────────────────────────────────────

_NEG_TAIL_RE = re.compile(
    r"(ไม่มี(?:อาการ|ภาวะ)?|ไม่พบ(?:ว่ามี)?|ไม่ได้|ไม่|ปฏิเสธ|\bno\b|without|denies)\s*$", re.IGNORECASE)

_FEATURE_PATTERNS: dict[str, str] = {
    "fever": r"ไข้(?!หวัด)|fever|\d{2}(?:\.\d)?\s*(?:°|องศา)",
    "pain": r"ปวด|เจ็บ|headache|myalgia|paracetamol|พารา",
    "runny": r"น้ำมูก|rhinorrh|post\s*-?nasal|จาม",
    "congestion": r"คัดจมูก|แน่นจมูก|จมูกตัน|nasal\s*congestion|stuffy",
    "cough": r"ไอ(?!โอดีน|น้ำ|ศกรีม)|cough",
    "sore_throat": r"เจ็บคอ|คอแดง|ระคาย(?:เคือง)?คอ|คออักเสบ|pharyngitis|ทอนซิล|tonsil|กลืน(?:เจ็บ|ลำบาก)|เยื่อบุคอ",
    "hoarse": r"เสียงแหบ|laryngitis|กล่องเสียงอักเสบ|สายเสียง",
    "ear": r"ปวดหู|หูอื้อ|น้ำหนวก|หูชั้นกลาง|otitis|\bAOM\b",
    # "จาม" เฉยๆ พบได้ในหวัด -> ไม่นับเป็นภูมิแพ้ (ต้องมีคัน/จามบ่อย/ภูมิแพ้/เป็นๆหายๆ/สิ่งกระตุ้น)
    "allergic": r"คันจมูก|คันตา|จามบ่อย|จามติดต่อ|จามเป็นชุด|ภูมิแพ้|allerg|เป็นๆ\s*หายๆ|ฝุ่น|เกสร",
    "sinus": r"ไซนัส|sinus|ABRS|ปวดโหนก|ปวด(?:บริเวณ)?ใบหน้า|ปวดหน้า(?!ท้อง)|ปวด(?:แน่น)?หน้าผาก|แน่นหน้าผาก|facial\s*pain",
    "chronic": r"เรื้อรัง|กลับเป็นซ้ำ|เป็นซ้ำ|recurrent|chronic|เป็นๆ\s*หายๆ|ทุกปี",
    "incs_req": r"steroid|สเตียรอยด์|corticoster|mometasone|fluticasone|budesonide|triamcinolone|beclomet|ciclesonide",
    "renal": r"โรคไต|ไตวาย|ไตเสื่อม|ไตบกพร่อง|การทำงานของไต|ไต(?!รมาส)|\bCKD\b|renal|kidney|eGFR|CrCl",
    "hepatic": r"โรคตับ|ตับแข็ง|ตับอักเสบ|ตับบกพร่อง|การทำงานของตับ|liver|hepatic|cirrhosis",
    "pregnant": r"ตั้งครรภ์|ตั้งท้อง|pregnan|ให้นมบุตร|ไตรมาส",
    "gi": r"แผลในกระเพาะ|โรคกระเพาะ|peptic|ulcer|เลือดออกในทางเดินอาหาร",
    # "ความดันตก/ความดันต่ำ" = อาการของ anaphylaxis ไม่ใช่โรคประจำตัวความดันโลหิตสูง (เคยทำให้โมเดลเขียนว่าผู้ป่วยเป็นความดันสูง)
    "cardio": r"ความดัน(?!\s*(?:ตก|ต่ำ))|hypertension|หัวใจ(?!เต้น)|cardiac|heart",
    "asthma": r"หอบหืด|asthma",
}
_THICK_RE = re.compile(
    r"น้ำมูก[^\n,;]{0,16}?(?:ข้น|เหนียว|เขียว|เหลือง|หนอง)|(?:ข้น|เหนียว)[^\n,;]{0,4}น้ำมูก|purulent|mucopurulent",
    re.IGNORECASE,
)
_CLEAR_RE = re.compile(r"น้ำมูก[^\n,;]{0,8}?(?:ใส|เหลว|ไหล)|rhinorrh", re.IGNORECASE)
_DRY_COUGH_RE = re.compile(r"ไอแห้ง|ไอ(?:แบบ)?ไม่มีเสมหะ|dry\s*cough|ไอระคายคอ", re.IGNORECASE)
_WET_PATTERN = r"เสมหะ(?!ไหลลงคอ)|productive|chesty"
_PARACETAMOL_FAIL_RE = re.compile(r"(?:paracetamol|พารา)[^\n]{0,40}(?:ไม่ดีขึ้น|ไม่หาย|ไม่ลด|ไม่ได้ผล|แทน)", re.IGNORECASE)

COMORBIDITY_LABELS = {
    "renal": "โรคไต/การทำงานของไตบกพร่อง", "hepatic": "โรคตับ", "pregnant": "ตั้งครรภ์/ให้นมบุตร",
    "gi": "แผลในกระเพาะอาหาร", "cardio": "ความดันโลหิตสูง/โรคหัวใจ", "asthma": "หอบหืด",
}


def _tri(text: str, pattern: str) -> bool | None:
    """True = มีอาการ, False = ระบุว่าไม่มี, None = ไม่ได้กล่าวถึง"""
    pos = neg = False
    for m in re.finditer(pattern, text, re.IGNORECASE):
        pre = text[max(0, m.start() - 14): m.start()]
        if _NEG_TAIL_RE.search(pre):
            neg = True
        else:
            pos = True
    if pos:
        return True
    return False if neg else None


def parse_age_years(text: str) -> float | None:
    for pat in (r"อายุ\s*(\d+(?:\.\d+)?)\s*(?:ปี|ขวบ)?", r"(\d+(?:\.\d+)?)\s*ขวบ",
                r"(\d+(?:\.\d+)?)\s*(?:ปี|yo\b|y/o|years?\s*old)(?!\s*(?:ก่อน|ที่แล้ว|แล้ว))"):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    m = re.search(r"(\d+)\s*เดือน(?!\s*(?:ก่อน|ที่แล้ว))", text)
    if m:
        return int(m.group(1)) / 12.0
    return None


def parse_weight(text: str) -> float | None:
    m = re.search(r"(?:น้ำหนัก|หนัก|BW)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:kg|กก\.?|กิโล)", text, re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1) or m.group(2))


def extract_case_features(text: str) -> dict:
    text = text or ""
    f: dict = {k: _tri(text, p) for k, p in _FEATURE_PATTERNS.items()}
    # "ไม่มีไข้สูง" = ไม่ใช่ไข้สูง (อาจมีไข้ต่ำ) -> ถือว่าไม่ทราบ ไม่ใช่ "ไม่มีไข้"
    if f["fever"] is False and re.search(r"ไม่(?:มี)?ไข้สูง", text):
        f["fever"] = None
    f["runny_char"] = "thick" if _THICK_RE.search(text) else ("clear" if _CLEAR_RE.search(text) else None)
    if f["runny_char"] and f["runny"] is not False:
        f["runny"] = True
    if _DRY_COUGH_RE.search(text):
        f["cough_type"] = "dry"
    elif _tri(text, _WET_PATTERN):
        f["cough_type"] = "wet"
    else:
        f["cough_type"] = None
    if f["cough_type"] and f["cough"] is not False:
        f["cough"] = True
    f["paracetamol_failed"] = bool(_PARACETAMOL_FAIL_RE.search(text))
    f["age"] = parse_age_years(text)
    f["weight"] = parse_weight(text)
    if f["age"] is not None:
        f["group"] = "pediatric" if f["age"] < 18 else "adult"
    else:
        g = infer_patient_group_from_query(text)
        f["group"] = g if g in ("adult", "pediatric") else None
    f["comorbid"] = [k for k in COMORBIDITY_LABELS if f.get(k)]
    return f


def describe_features(f: dict) -> str:
    def yn(v, yes="มี", no="ไม่มี"):
        return yes if v else (no if v is False else None)
    bits = []
    for label, val in (
        ("ไข้", yn(f["fever"])), ("ปวด/เจ็บ", yn(f["pain"])), ("เจ็บคอ", yn(f["sore_throat"])),
        ("เสียงแหบ", yn(f["hoarse"])), ("คัดจมูก", yn(f["congestion"])),
        ("อาการภูมิแพ้ (คัน/จาม/เป็นๆหายๆ)", yn(f["allergic"])), ("อาการเข้าได้กับไซนัส", yn(f["sinus"])),
    ):
        if val:
            bits.append(f"{label}: {val}")
    if f["runny"] is not None:
        ch = {"thick": "ข้นเหนียว/มีสี", "clear": "ใส/ไหล"}.get(f["runny_char"], "ยังไม่ทราบลักษณะ")
        bits.append(f"น้ำมูก: {'มี (' + ch + ')' if f['runny'] else 'ไม่มี'}")
    if f["cough"] is not None:
        ct = {"dry": "ไอแห้ง/ไม่มีเสมหะ", "wet": "มีเสมหะ"}.get(f["cough_type"], "ยังไม่ทราบว่ามีเสมหะหรือไม่")
        bits.append(f"ไอ: {'มี (' + ct + ')' if f['cough'] else 'ไม่มี'}")
    if f["comorbid"]:
        bits.append("โรคร่วม/ภาวะพิเศษ: " + ", ".join(COMORBIDITY_LABELS[k] for k in f["comorbid"]))
    return " | ".join(bits) if bits else "-"


# ─── Gateway: fit / conditional / avoid per drug class ───────────────────────

CLASS_LABELS: dict[str, str] = {
    "fever_pain": "ยาแก้ปวด/ลดไข้ (Analgesic/Antipyretic: Paracetamol และกลุ่ม NSAIDs)",
    "antihistamine": "ยาลดน้ำมูก/ยาแก้แพ้ ชนิดเดี่ยว (Antihistamine)",
    "decongestant": "ยาแก้คัดจมูก ชนิดเดี่ยว (Topical nasal decongestant)",
    "combo_cold": "ยาแก้คัดจมูก+ลดน้ำมูก สูตรผสม (Decongestant + Antihistamine)",
    "combo_flu": "ยาสูตรผสม ลดไข้+ลดน้ำมูก+แก้คัดจมูก (Paracetamol + Antihistamine + Decongestant)",
    "incs": "ยาพ่นจมูกสเตียรอยด์ (Intranasal corticosteroid)",
    "cough_dry": "ยาบรรเทาอาการไอแห้ง/ไอไม่มีเสมหะ (Antitussive)",
    "cough_wet": "ยาละลาย/ขับเสมหะ สำหรับไอมีเสมหะ (Mucolytic/Expectorant)",
    "throat_spray": "ยาพ่นบรรเทาอาการเจ็บคอ (Throat spray)",
    "throat_lozenge": "ยาอมบรรเทาอาการเจ็บคอ (Lozenge)",
    "gargle": "ยากลั้วคอ (Gargle)",
}
CLASS_ORDER = ["fever_pain", "antihistamine", "decongestant", "combo_cold", "combo_flu", "incs",
               "cough_dry", "cough_wet", "throat_spray", "throat_lozenge", "gargle"]
# คำนำทางสำหรับตัดตอน "ช่วงขนาดยาที่ตรงข้อบ่งใช้ URI" จากแถวยาที่ยาว (เช่น Ibuprofen ขึ้นต้นด้วยขนาดโรคข้อ)
_CLASS_DOSE_HINTS: dict[str, list[str]] = {
    "antihistamine": ["common cold", "Upper respiratory", "hay fever", "Allergic rhinitis", "rhinitis", "หวัด"],
    "decongestant": ["Nasal congestion"],
    "combo_cold": ["Upper respiratory"],
    "incs": ["rhinosinusitis", "Rhinosinusitus", "Allergic rhinitis", "rhinitis"],
    "cough_dry": ["Cough", "cough"],
    "cough_wet": ["mucolytic", "Mucolytic"],
}
_U4 = "เด็กอายุต่ำกว่า 4 ปี ไม่แนะนำยาแก้ไอ/ยาแก้แพ้/ยาลดน้ำมูก/ยาแก้คัดจมูกสำหรับอาการหวัด (AAP; URI เด็ก 2562)"


def plan_classes(f: dict) -> dict[str, tuple[str, str]]:
    """คืน {class: (status, reason)} -- status: fit | conditional | avoid"""
    plan: dict[str, tuple[str, str]] = {}
    # ต้องมีอาการ URI อย่างน้อย 1 อย่าง (ปวดอย่างเดียว เช่น ปวดท้อง/ปวดหลัง = นอกขอบเขต ไม่ใช่งานของ gateway นี้)
    if not has_uri_symptom(f):
        return plan
    age = f.get("age")
    u4 = age is not None and age < 4
    thick = f.get("runny_char") == "thick"
    runny = f.get("runny") is True
    cong = f.get("congestion") is True
    allergic = f.get("allergic") is True
    fever = f.get("fever") is True

    if fever or f.get("pain") or f.get("sore_throat") or f.get("sinus"):
        why = "มีไข้/ปวด/เจ็บคอ"
        if f.get("paracetamol_failed"):
            why += " -- ใช้ Paracetamol แล้วไม่ดีขึ้น: พิจารณายาแก้ปวดกลุ่ม NSAIDs เป็นทางเลือก (ตรวจข้อห้าม/อายุ/น้ำหนัก)"
        plan["fever_pain"] = ("fit", why)
    elif f.get("fever") is None:
        # ไม่ได้บอกเรื่องไข้ (ไม่ใช่ปฏิเสธไข้) -> เสนอยาแก้ปวด/ลดไข้เป็นยาใช้เมื่อมีอาการ (prn) ที่พบบ่อยในหวัด
        plan["fever_pain"] = ("fit", "ให้ระบุไว้ในคำตอบเป็นยาใช้เมื่อมีอาการ (as needed) สำหรับไข้ต่ำ/ปวดศีรษะ/ปวดเมื่อยซึ่งพบบ่อย"
                                     "ในหวัด -- Paracetamol เป็นตัวหลัก")
    else:
        plan["fever_pain"] = ("conditional", "เสนอเป็นยาใช้เมื่อมีอาการ (as needed) สำหรับไข้ต่ำ/ปวดศีรษะ/ปวดเมื่อยที่พบบ่อยในหวัด "
                                             "-- Paracetamol เป็นตัวหลัก")

    if runny or allergic:
        if u4 and not allergic:
            plan["antihistamine"] = ("avoid", _U4)
        elif thick and not allergic:
            plan["antihistamine"] = ("avoid", "น้ำมูกข้นเหนียว -- ยาแก้แพ้ (โดยเฉพาะรุ่นที่ 1 เช่น Chlorpheniramine, "
                                     "Brompheniramine) ลดสารคัดหลั่งทำให้น้ำมูกเหนียวข้นขึ้น -> แนะนำล้างจมูกด้วยน้ำเกลือแทน")
        elif f.get("runny_char") == "clear" or allergic:
            plan["antihistamine"] = ("fit", "น้ำมูกใส/ไหล" if not allergic else "อาการภูมิแพ้จมูก (คัน/จาม/น้ำมูกใส)")
        else:
            plan["antihistamine"] = ("conditional", "มีน้ำมูกแต่ยังไม่ทราบลักษณะ -- ถ้าใสเหลวใช้ได้ (รุ่นที่ 1 เหมาะน้ำมูกใสเหลว), "
                                                    "ถ้าข้นเหนียวไม่ควรใช้")

    need = [x for x, ok in (("น้ำมูกไหล", runny and not thick), ("คัดจมูก", cong)) if not ok]
    if runny or cong:
        if u4:
            plan["combo_cold"] = ("avoid", _U4)
        elif thick:
            plan["combo_cold"] = ("avoid", "มียาแก้แพ้รุ่นที่ 1 ในสูตร ทำให้น้ำมูกข้นเหนียวขึ้น -- ไม่เหมาะกับน้ำมูกข้นเหนียว/ไซนัสอักเสบ")
        elif need:
            plan["combo_cold"] = ("avoid", "สูตรผสมใช้เมื่อมี 'ทั้งน้ำมูกไหลและคัดจมูก' ร่วมกัน แต่เคสนี้ไม่มี/ไม่ได้ระบุ: "
                                  + ", ".join(need) + " -> ใช้ยาชนิดเดี่ยวตามอาการที่มีจริงแทน")
        else:
            plan["combo_cold"] = ("fit", "มีทั้งน้ำมูกไหล (ใส) และคัดจมูก")
    if runny or cong or fever:
        need2 = ([] if fever else ["ไข้"]) + need
        if u4:
            plan["combo_flu"] = ("avoid", _U4)
        elif thick or f.get("sinus"):
            plan["combo_flu"] = ("avoid", "เป็นยาสำหรับหวัดที่มีไข้+น้ำมูกไหล+คัดจมูกพร้อมกัน ไม่ใช่ยาสำหรับไซนัสอักเสบ/น้ำมูกข้นเหนียว "
                                          "(มี Chlorpheniramine ทำให้น้ำมูกเหนียวขึ้น)")
        elif need2:
            plan["combo_flu"] = ("avoid", "สูตรผสม 3 ตัวยา (Paracetamol ลดไข้ + Chlorpheniramine ลดน้ำมูก + Phenylephrine แก้คัดจมูก) "
                                          "ใช้เมื่อมีครบ 3 อาการ แต่เคสนี้ไม่มี/ไม่ได้ระบุ: " + ", ".join(need2))
        else:
            plan["combo_flu"] = ("fit", "มีครบ ไข้ + น้ำมูกไหล + คัดจมูก")
    if cong:
        plan["decongestant"] = ("avoid", _U4) if u4 else (
            "fit", "มีอาการคัดจมูก -- ชนิดพ่น/หยดจมูกใช้ไม่เกิน 3-5 วัน (กัน rebound congestion)")

    if allergic or f.get("chronic") or f.get("incs_req"):
        plan["incs"] = ("fit", "มีภูมิแพ้จมูก/อาการเรื้อรังหรือกลับเป็นซ้ำ/ผู้ใช้ขอยาพ่นสเตียรอยด์")
    elif f.get("sinus") or cong or thick:
        plan["incs"] = ("avoid", "ไม่จำเป็นต้องแนะนำเป็นประจำในการติดเชื้อเฉียบพลัน (โดยเฉพาะผู้ใหญ่) -- ใช้เมื่อมีภูมิแพ้จมูกร่วม "
                                 "หรือเป็นเรื้อรัง/กลับเป็นซ้ำ; ในเด็กตาม URI เด็ก 2562 ใช้เฉพาะเรื้อรัง/กลับเป็นซ้ำ/ภูมิแพ้ร่วม")

    if f.get("cough"):
        ct = f.get("cough_type")
        if u4:
            plan["cough_dry"] = ("avoid", _U4)
            plan["cough_wet"] = ("avoid", _U4)
        elif ct == "dry":
            plan["cough_dry"] = ("fit", "ไอแห้ง/ไม่มีเสมหะ")
        elif ct == "wet":
            plan["cough_wet"] = ("fit", "ไอมีเสมหะ")
            plan["cough_dry"] = ("avoid", "ยากดการไอไม่เหมาะกับไอมีเสมหะ (ขัดขวางการขับเสมหะ)")
        else:
            why = "ยังไม่ทราบลักษณะการไอ -- ต้องถามก่อนว่า 'ไอแห้ง' หรือ 'ไอมีเสมหะ' แล้วเลือกกลุ่มให้ตรง"
            plan["cough_dry"] = ("conditional", why)
            plan["cough_wet"] = ("conditional", why)

    if f.get("sore_throat") or f.get("hoarse"):
        for c in ("throat_spray", "throat_lozenge", "gargle"):
            plan[c] = ("fit", "เจ็บคอ/ระคายคอ/เสียงแหบ -- ตรวจอายุขั้นต่ำของแต่ละผลิตภัณฑ์")
    return plan


def practical_options(f: dict) -> list[str]:
    """ทางเลือกที่ไม่ใช้ยา/พฤติกรรมปฏิบัติจริงหน้าร้าน (Expert practice)"""
    out: list[str] = []
    if f.get("runny_char") == "thick" or f.get("sinus") or f.get("congestion"):
        out.append("ล้างจมูกด้วยน้ำเกลือ (Normal saline nasal irrigation) -- ช่วยระบายน้ำมูกข้นเหนียว/ไซนัส "
                   "เหมาะกว่ายาแก้แพ้ในเคสน้ำมูกข้น")
    if f.get("sore_throat"):
        out.append("กลั้วคอด้วยน้ำเกลืออุ่น -- ทางเลือกง่าย ปลอดภัย ใช้ได้เป็นพื้นฐาน (ไม่จำเป็นต้องใช้ยากลั้วคอเสมอ)")
    if f.get("hoarse"):
        out.append("พักการใช้เสียง จิบน้ำอุ่นบ่อยๆ")
    if f.get("group") == "pediatric" and f.get("runny"):
        out.append("เด็กเล็ก: หยดน้ำเกลือและดูดน้ำมูกเมื่อน้ำมูกมาก")
    return out


def _dose_excerpt(text: str, hints: list[str], limit: int) -> str:
    """ตัดตอนขนาดยาตรงจากตาราง (verbatim) เริ่มที่ช่วงที่ตรงข้อบ่งใช้ และจบที่ขอบประโยค (ไม่ตัดกลางตัวเลข)"""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    start = 0
    for h in hints:
        i = text.find(h)
        if i > 0:
            start = i
            break
    seg = text[start:]
    if len(seg) <= limit:
        return seg
    cut = seg[:limit]
    b = max(cut.rfind(". "), cut.rfind("; "), cut.rfind(" •"))
    if b > limit * 0.45:
        return cut[: b + 1].strip() + " …"
    b = cut.rfind(" ")
    return (cut[:b] if b > 0 else cut).strip() + " …"


def _drug_line(d: dict, cls: str, f: dict, *, full: bool) -> str:
    group = f.get("group")
    hints = list(_CLASS_DOSE_HINTS.get(cls, []))
    if cls == "fever_pain":
        hints = ["Fever", "fever", "Pain", "pain"] if f.get("fever") else ["Pain", "pain", "Fever"]
    tag = ""
    if d.get("first_gen"):
        tag = " [รุ่นที่ 1: ง่วง, ลดสารคัดหลั่งแรง -- เหมาะน้ำมูกใสเหลว]"
    elif d.get("herbal"):
        tag = " [ตำรับยาน้ำ/สมุนไพร -- ทางเลือกเสริม ไม่ใช่ตัวหลัก]"
    elif d.get("nsaid") and cls == "fever_pain":
        tag = " [NSAID]"
    if d.get("form"):
        tag += f" [รูปแบบ: {FORM_LABELS[d['form']]}]"
    ingr = brand_ingredient(d)
    if ingr:
        tag += f" [ตัวยา/สารสำคัญ: {ingr}]"
    parts = [f"  - {d['display']}{tag} (Page: {d['page']})"]
    ind = re.sub(r"\s+", " ", d.get("indication") or "").strip()
    if ind:
        parts.append("ข้อบ่งใช้: " + ind[:110])
    lim = 380 if full else 300
    if group == "pediatric":
        parts.append("ขนาดเด็ก: " + (_dose_excerpt(d["ped"], hints, lim) or "ไม่มีขนาดเด็กในตาราง"))
    elif group == "adult":
        parts.append("ขนาดผู้ใหญ่: " + (_dose_excerpt(d["adult"] or d["ped"], hints, lim) or "-"))
    else:
        parts.append("ขนาดผู้ใหญ่: " + (_dose_excerpt(d["adult"], hints, 220) or "-"))
        parts.append("ขนาดเด็ก: " + (_dose_excerpt(d["ped"], hints, 220) or "-"))
    if f.get("renal") or f.get("hepatic"):
        parts.append("ปรับขนาดตามไต/ตับ: " + (_dose_excerpt(d.get("renal"), [], 260) or "ไม่มีข้อมูลในตาราง"))
    if f.get("comorbid") or full:
        w = _dose_excerpt(d.get("warn"), [], 260 if f.get("comorbid") else 160)
        if w:
            parts.append("ข้อห้าม/ข้อควรระวัง: " + w)
    return " | ".join(parts)


def _eligible(d: dict, f: dict) -> tuple[bool, str]:
    """ตรวจอายุ/ขนาดเด็กจากตารางเอง (data-driven)"""
    age = f.get("age")
    if f.get("group") == "pediatric":
        if "aspirin" in d["name"].lower():
            return False, "ห้ามใช้ Aspirin ในเด็กและวัยรุ่น (เสี่ยง Reye's syndrome)"
        if not d.get("ped"):
            return False, "ตารางไม่มีขนาดยาเด็ก"
        if age is not None and d.get("min_age") is not None and age < d["min_age"]:
            return False, f"ตารางระบุใช้ได้ตั้งแต่อายุ {d['min_age']:g} ปี"
    return True, ""


def _tier(d: dict, cls: str) -> str:
    """ลำดับความสำคัญในกลุ่ม: main (ยาหลัก) -> alt (ทางเลือกในกลุ่ม) -> product (ผลิตภัณฑ์/สูตรผสม) -> adjunct -> rare"""
    if d.get("rare_uri"):
        return "rare"
    if d.get("herbal"):
        return "adjunct"
    if cls in ("throat_spray", "throat_lozenge", "gargle"):
        return "main"          # หมวดยาเฉพาะที่คอในตารางเป็นผลิตภัณฑ์ทั้งหมด -> ไม่มีชื่อสามัญให้ขึ้นก่อน
    if cls == "fever_pain" and d.get("nsaid"):
        return "alt"
    if d.get("product"):
        return "product"
    return "main"


_TIER_ORDER = {"main": 0, "alt": 1, "product": 2, "adjunct": 3, "rare": 4}
_TIER_LABELS = {
    "main": "ยาหลัก",
    "alt": "ทางเลือกในกลุ่ม (เช่น NSAIDs -- ตรวจข้อห้าม)",
    "product": "ผลิตภัณฑ์ที่มีตัวยาเดียวกัน/สูตรผสม/รูปแบบอื่น (ทางเลือกเสริม -- ระบุตัวยาสำคัญกำกับเสมอ)",
    "adjunct": "ตำรับยาน้ำ/สมุนไพร (ทางเลือกเสริม ไม่ใช่ตัวหลัก)",
}


def symptomatic_depth(f: dict, *, full: bool = False) -> tuple[str, str]:
    """ระดับรายละเอียดของหัวข้อยาตามอาการ -> ('global'|'detail', เหตุผล)
    global = เคสทั่วไปไม่ซับซ้อน (แนะนำแบบยืดหยุ่น: ทางเลือกพื้นฐาน/กลุ่มยา + ตัวอย่างยา + ขนาดสั้น)
    detail = เคสที่ต้องเจาะลึก (โรคร่วม/ภาวะพิเศษ, ยาเดิมไม่ได้ผล, ขอยาพ่นสเตียรอยด์, ผู้ใช้ขอดูตัวเลือกยา)"""
    why: list[str] = []
    if f.get("comorbid"):
        why.append("มีโรคร่วม/ภาวะพิเศษ (" + ", ".join(COMORBIDITY_LABELS[k] for k in f["comorbid"]) + ")")
    if f.get("paracetamol_failed"):
        why.append("ใช้ Paracetamol แล้วไม่ดีขึ้น")
    if f.get("incs_req"):
        why.append("ผู้ใช้ถามถึงยาพ่นจมูกสเตียรอยด์")
    if full:
        why.append("ผู้ใช้ขอดูตัวเลือกยา")
    if why:
        return "detail", "; ".join(why)
    return "global", "เคสอาการทั่วไป ไม่มีโรคร่วม/ภาวะพิเศษ"


_DEPTH_GUIDE = {
    "global": (
        "รูปแบบหัวข้อ 3b = GLOBAL (เคสทั่วไป): เขียนกระชับเป็น 'หมวดตามอาการ' เรียงลำดับความสำคัญ -- แต่ละหมวดขึ้นต้นด้วยประโยคแนะนำ"
        "แบบยืดหยุ่น (ทางเลือกพื้นฐานที่ไม่ใช้ยาก่อนถ้ามี เช่น 'กลั้วคอด้วยน้ำเกลืออุ่น หรือใช้ยาพ่นบรรเทาอาการเจ็บคอ เช่น ...') แล้วตามด้วย"
        "ตัวเลือกทั้งหมดที่เหมาะในหมวดนั้น แบบสั้น: ชื่อยา + ขนาดต่อครั้ง/ความถี่ + [Ref: Dose, หน้า N] (รวมเป็นบรรทัดเดียวคั่นด้วย ',' ได้ "
        "เมื่อเป็นกลุ่มย่อยเดียวกัน) -- ไม่ต้องอธิบายยาวรายตัว ไม่ต้องใส่ข้อห้าม/การปรับขนาดที่ไม่เกี่ยวกับเคส"),
    "detail": (
        "รูปแบบหัวข้อ 3b = DETAIL (เคสที่ต้องเจาะลึก): แต่ละตัวเลือกบอกว่าเหมาะกับใคร + ขนาด (ช่วง + ขนาดสูงสุด) + ข้อควรระวัง/การปรับขนาด"
        "ที่เกี่ยวกับเคสนี้ + [Ref: Dose, หน้า N]"),
}


def build_catalog(f: dict, plan: dict[str, tuple[str, str]], *, full: bool = False) -> tuple[str, list[dict]]:
    """สร้างบล็อก 'คลังยาตามอาการ' สำหรับ Context + รายการยาที่แนบ (ใช้ทำ source/เลขหน้า)"""
    if not plan:
        return "", []
    formulary = load_formulary()
    if not formulary:
        return "", []
    group_label = {"adult": "ผู้ใหญ่", "pediatric": "เด็ก"}.get(f.get("group"), "ยังไม่ทราบกลุ่มอายุ (แสดงทั้งผู้ใหญ่และเด็ก)")
    depth, depth_why = symptomatic_depth(f, full=full)
    lines = [
        "[คลังยาตามอาการ -- DOSE CATALOG] Source: Dose | Type: DOSE_TABLE (ระบบคัดจากตาราง Dose ตามอาการของเคสนี้)",
        f"อาการที่ระบบตรวจพบ: {describe_features(f)}",
        f"กลุ่มผู้ป่วยสำหรับขนาดยา: {group_label}",
        "วิธีใช้: ใช้เฉพาะกลุ่มที่ 'เหมาะกับเคสนี้/ขึ้นกับข้อมูลที่ยังไม่ทราบ' -- เสนอ **ตัวเลือกให้ครบทุกตัวที่อยู่ในกลุ่มนั้นและเหมาะกับผู้ป่วยรายนี้** "
        "(ไม่จำกัด 2-4 ตัว แต่ต้องถูกต้อง) เรียงตามลำดับ: ยาหลัก -> ทางเลือกในกลุ่ม -> ผลิตภัณฑ์/ทางเลือกเสริม; เรียกหมวดตาม [รูปแบบ] ของยา "
        "(ยาพ่นคอ/ยาอม/ยากลั้วคอ ห้ามปนหมวด) และผลิตภัณฑ์ต้องมีตัวยา/สารสำคัญกำกับ + ขนาดยา + [Ref: Dose, หน้า N] (N = Page ท้ายชื่อยา) "
        "-- ขนาดยาด้านล่างเป็นข้อความตัดตอนตรงจากตาราง (ตัวเลขตรงต้นฉบับ)",
        f"{_DEPTH_GUIDE[depth]} (เหตุผล: {depth_why})",
    ]
    used: list[dict] = []
    avoided: list[str] = []
    for cls in CLASS_ORDER:
        if cls not in plan:
            continue
        status, reason = plan[cls]
        members = [d for d in formulary if cls in d["classes"]]
        if not members:
            continue
        if status == "avoid":
            names = ", ".join(dict.fromkeys(d["display"] for d in members))
            avoided.append(f"  - {CLASS_LABELS[cls]}: {names} -- เหตุผล: {reason}")
            continue
        head = "เหมาะกับเคสนี้" if status == "fit" else "ขึ้นกับข้อมูลที่ยังไม่ทราบ"
        lines.append(f"\n■ {CLASS_LABELS[cls]} -- {head}: {reason}")
        not_ok: list[str] = []
        rare: list[str] = []
        cur_tier = None
        # ยาแก้แพ้รุ่นที่ 1 (ง่วง/ลดสารคัดหลั่งแรง) ไว้ท้ายกลุ่มยาหลัก -> รุ่นที่ 2 ขึ้นก่อนตาม feedback อาจารย์
        for d in sorted(members, key=lambda x: (_TIER_ORDER[_tier(x, cls)], 1 if x.get("first_gen") else 0)):
            ok, why = _eligible(d, f)
            if not ok:
                not_ok.append(f"{d['display']} ({why})")
                continue
            tier = _tier(d, cls)
            if tier == "rare" and not full:
                rare.append(d["display"])
                continue
            if tier != cur_tier and tier in _TIER_LABELS:
                lines.append(f"  [{_TIER_LABELS[tier]}]")
                cur_tier = tier
            lines.append(_drug_line(d, cls, f, full=full))
            if d not in used:
                used.append(d)
        if rare:
            lines.append("  (มีในตารางแต่ไม่ใช่ตัวเลือกทั่วไปสำหรับอาการ URI -- ไม่ต้องแนะนำ เว้นแต่ผู้ใช้ขอดูทั้งหมด: "
                         + ", ".join(rare) + ")")
        if not_ok:
            lines.append("  (ไม่เหมาะกับอายุ/กลุ่มผู้ป่วยนี้: " + "; ".join(not_ok) + ")")
    if avoided:
        lines.append("\n■ ไม่เหมาะกับเคสนี้ (ห้ามแนะนำเป็นการรักษา -- ถ้าจะกล่าวถึง ให้บอกว่าไม่แนะนำเพราะอะไร):")
        lines += avoided
    prac = practical_options(f)
    if prac:
        lines.append("\n■ ทางเลือกที่ไม่ใช้ยา / แนวปฏิบัติจริงหน้าร้าน (Expert practice -- ต้องใส่ในคำตอบเมื่อเกี่ยวข้อง, "
                     "ไม่ใช่ยาในตาราง Dose จึงห้ามอ้าง [Ref: Dose]):")
        lines += [f"  - {p}" for p in prac]
    return "\n".join(lines), used


def gate_dose_chunks(chunks: list[dict], plan: dict[str, tuple[str, str]], f: dict) -> list[dict]:
    """ตัด Dose chunk ที่ gateway ตัดสินว่าไม่เหมาะกับเคสนี้ (ทุกกลุ่มของยานั้นเป็น avoid) หรือไม่เข้าเกณฑ์อายุ"""
    if not plan:
        return chunks
    out: list[dict] = []
    for c in chunks:
        if c.get("source") != "Dose":
            out.append(c)
            continue
        name = c.get("drug_name")
        if not name:
            m = re.search(r"^Drug:\s*(.+)$", c.get("content") or "", re.MULTILINE)
            name = m.group(1).strip() if m else None
        d = drug_by_name(name) if name else None
        if d is None:
            out.append(c)
            continue
        statuses = [plan[k][0] for k in d["classes"] if k in plan]
        if statuses and all(s == "avoid" for s in statuses):
            continue
        if not _eligible(d, f)[0]:
            continue
        out.append(c)
    return out


# ─── History-taking completeness (หลักซักประวัติร้านยา) ──────────────────────
# Who - Age - (Weight: เด็ก) - What - Severity - When - Any treated - Allergy - Comorbidity
# ให้ LLM เห็น pattern ชัด: ระบบบอกว่า "อะไรทราบแล้ว / อะไรยังขาด" แทนการให้ LLM เดาเอง

_PATIENT_RE = re.compile(
    r"ผู้ป่วย|คนไข้|ผู้หญิง|ผู้ชาย|หญิง|ชาย|เด็ก|ลูก|ทารก|อายุ|\d+\s*(?:ปี|ขวบ|เดือน)|คุณแม่|คุณพ่อ|ป้า|ลุง|ยาย|น้อง",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"\d+\s*(?:วัน|สัปดาห์|อาทิตย์|ชั่วโมง|ชม\.?)|(?:หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|หลาย)\s*วัน|"
    r"เมื่อวาน|เมื่อเช้า|วันนี้|ในวันเดียว|เป็นๆ\s*หายๆ|เรื้อรัง|ทุกปี|วันที่\s*\d",
    re.IGNORECASE,
)
_TEMP_RE = re.compile(r"\d{2}(?:\.\d)?\s*(?:°|องศา)|ไข้\s*\d{2}", re.IGNORECASE)
_ALLERGY_RE = re.compile(
    r"(?<!ภูมิ)แพ้(?!อากาศ|ฝุ่น|เกสร)|allergy|NKDA|เคยรับ[^\n]{0,40}(?:ผื่น|ลมพิษ)", re.IGNORECASE)
_COMORBID_RE = re.compile(
    r"โรคประจำตัว|ความดัน|เบาหวาน|ไต(?!รมาส)|หัวใจ|หอบหืด|asthma|ตับ|ตั้งครรภ์|ให้นมบุตร|ไทรอยด์|G6PD|กระเพาะ|"
    r"สุขภาพแข็งแรง|ไม่มีโรค", re.IGNORECASE)
_MEDS_RE = re.compile(
    r"(?<!ควร)(?:กิน|ทาน|ได้รับ|ซื้อ)ยา|(?<!ควร)ใช้ยา(?!ปฏิชีวนะ|ต้าน|ตัว)|เคย(?:ได้|ใช้|กิน|รับ)|ยังไม่ได้(?:กิน|ใช้|รับ|ทาน)|"
    r"ไม่ได้(?:กิน|ทาน|ใช้)|paracetamol|พารา",
    re.IGNORECASE)


# ผู้ใช้ระบุ "การวินิจฉัย/ภาวะ" มาแล้ว (เช่น "มี acute bacterial rhinosinusitis", "Streptococcal pharyngitis ชัดเจน")
# -> อาการ/ระยะเวลาที่ไม่ได้บอก ไม่ใช่เหตุให้เป็นประเภท 4 (ตอบตามการวินิจฉัยที่ให้มา)
_DIAGNOSIS_GIVEN_RE = re.compile(
    r"วินิจฉัย(?:ว่า|แล้ว|เป็น)|ได้รับการวินิจฉัย|ยืนยัน(?:ว่า|ผล)|acute\s+bacterial\s+rhinosinusitis|\bABRS\b|"
    r"streptococcal|\bGABHS\b|strep\s*throat|(?:มี|เป็น)\s*(?:acute|streptococcal)|อาการ[^\n]{0,40}ชัดเจน",
    re.IGNORECASE,
)


_URI_KEYS = ("fever", "runny", "congestion", "cough", "sore_throat", "hoarse", "sinus", "allergic", "ear")


def has_uri_symptom(f: dict) -> bool:
    return any(f.get(k) for k in _URI_KEYS)


def is_case_description(text: str, f: dict | None = None) -> bool:
    """ข้อความเป็น 'คำบรรยายเคส URI ของผู้ป่วย' (ไม่ใช่คำถามความรู้ทั่วไป/คำถามต่อยอดสั้นๆ/เคสนอกขอบเขต)"""
    f = f or extract_case_features(text)
    n_uri = sum(1 for k in _URI_KEYS if f.get(k))
    if n_uri == 0:
        return False
    return bool(_PATIENT_RE.search(text or "")) or (n_uri + (1 if f.get("pain") else 0)) >= 2


def assess_history(text: str, f: dict) -> str:
    """บันทึกความครบถ้วนของการซักประวัติ (inject เข้า user message) -- '' ถ้าไม่ใช่คำบรรยายเคส"""
    return history_gaps(text, f)[0]


def history_gaps(text: str, f: dict) -> tuple[str, int]:
    """(บันทึกความครบถ้วนของการซักประวัติ, จำนวนข้อมูลขั้นต่ำที่ขาด) -- ('', 0) ถ้าไม่ใช่คำบรรยายเคส"""
    if not is_case_description(text, f):
        return "", 0
    known: list[str] = []
    minimal_missing: list[str] = []
    helpful_missing: list[str] = []
    ped = f.get("group") == "pediatric"

    if _PATIENT_RE.search(text):
        known.append("ผู้ป่วยคือใคร")
    if f.get("age") is not None:
        known.append(f"อายุ ({f['age']:g} ปี)")
    elif ped and f.get("weight") is not None:
        # เด็กที่ทราบน้ำหนักแล้ว = คำนวณขนาดยาได้ (เกณฑ์เดิม: อาการหลักชัด + น้ำหนักตัว "หรือ" อายุ -> ประเภท 2)
        helpful_missing.append("อายุที่แน่นอน (ทราบน้ำหนักแล้ว คำนวณขนาดยาได้) -- เพื่อตรวจข้อห้ามใช้ตามอายุและเลือกระยะเวลา"
                               "รักษาตามช่วงอายุ")
    else:
        minimal_missing.append("อายุ -- เพื่อเลือก Guideline ให้ตรงกลุ่มอายุ ตรวจข้อห้ามใช้ตามอายุ และให้คะแนน Centor ได้ถูก")
    if ped and (f.get("age") is None or f["age"] <= 12):
        if f.get("weight") is not None:
            known.append(f"น้ำหนัก ({f['weight']:g} kg)")
        else:
            minimal_missing.append("น้ำหนักตัว (เด็ก -- จำเป็นเสมอ) -- เพื่อคำนวณขนาดยาตามน้ำหนัก (mg/kg) อย่างปลอดภัย")
    known.append("อาการหลัก")
    if f.get("fever") is not None or _TEMP_RE.search(text) or re.search(r"ไข้(?!หวัด)|fever", text, re.IGNORECASE):
        known.append("ไข้")
    else:
        minimal_missing.append("มีไข้หรือไม่ วัดได้เท่าไร -- เพื่อประเมินความรุนแรงและเกณฑ์วินิจฉัย")
    if _DURATION_RE.search(text):
        known.append("ระยะเวลา")
    else:
        minimal_missing.append("อาการเป็นมากี่วัน (เคยเป็นแบบนี้มาก่อนไหม) -- เพื่อแยก viral vs bacterial "
                               "(เช่น ไซนัส ≥10 วัน) และประเมินระยะของโรค")
    confirm_before_dispense: list[str] = []
    if _ALLERGY_RE.search(text):
        known.append("ประวัติแพ้ยา")
    else:
        # ไม่ทราบประวัติแพ้ยา "อย่างเดียว" ไม่ทำให้ประเมิน/วินิจฉัยไม่ได้ -> ไม่นับเป็นข้อมูลขั้นต่ำที่ขาด
        # แต่ต้องยืนยันก่อนจ่ายยา (โดยเฉพาะยาปฏิชีวนะ) -- ถามนำก่อนส่วนการจ่ายยา
        confirm_before_dispense.append("ประวัติแพ้ยา (แพ้ตัวไหน อาการแพ้แบบใด) -- เพื่อเลือกยาที่ปลอดภัย "
                                       "โดยเฉพาะก่อนจ่ายยาปฏิชีวนะ (ถ้าจะแนะนำยาปฏิชีวนะ ให้เขียนแบบมีเงื่อนไข ถ้าไม่แพ้/ถ้าแพ้)")

    if f.get("runny") and not f.get("runny_char"):
        helpful_missing.append("ลักษณะน้ำมูก (ใส/เหลว หรือ ข้นเหนียว/มีสี) -- เพื่อเลือกยาลดน้ำมูกให้ถูก "
                               "(ยาแก้แพ้รุ่นที่ 1 ทำให้น้ำมูกข้นเหนียวขึ้น)")
    if f.get("cough") and not f.get("cough_type"):
        helpful_missing.append("ลักษณะการไอ (ไอแห้ง/ไม่มีเสมหะ หรือ ไอมีเสมหะ) -- เพื่อเลือกระหว่างยาบรรเทาอาการไอแห้ง "
                               "กับยาละลาย/ขับเสมหะ")
    if f.get("fever") and not _TEMP_RE.search(text) and (f.get("sore_throat") or f.get("sinus") or ped):
        helpful_missing.append("อุณหภูมิที่วัดได้ -- ใช้ประเมินเกณฑ์ Centor (≥38°C)/ความรุนแรง")
    if _MEDS_RE.search(text):
        known.append("ยาที่ใช้มาก่อน")
    else:
        helpful_missing.append("ยาที่ใช้มาก่อนมาร้าน (ชื่อยา/ได้ผลไหม) -- เพื่อไม่ให้ใช้ยาซ้ำซ้อน/เกินขนาด และประเมินการตอบสนอง")
    if _COMORBID_RE.search(text):
        known.append("โรคประจำตัว/ภาวะพิเศษ")
    else:
        helpful_missing.append("โรคประจำตัว/ภาวะพิเศษ (ความดัน โรคหัวใจ ไต ตับ ตั้งครรภ์) -- เพื่อตรวจข้อห้ามของยา "
                               "(เช่น Phenylephrine ในความดันสูง, NSAIDs ในโรคไต/แผลในกระเพาะ)")

    dx_given = bool(_DIAGNOSIS_GIVEN_RE.search(text))
    if dx_given:
        # วินิจฉัยมาแล้ว: ข้อมูลอาการที่ขาดเป็นแค่ "ข้อมูลเสริม" (ยกเว้นน้ำหนักเด็กที่ต้องใช้คำนวณยา)
        keep = [x for x in minimal_missing if x.startswith("น้ำหนักตัว")]
        helpful_missing = [x for x in minimal_missing if x not in keep] + helpful_missing
        minimal_missing = keep
    n_min = len(minimal_missing)
    lines = ["**ตรวจความครบถ้วนของการซักประวัติ (ระบบคัดกรองจากข้อความผู้ใช้ตามหลัก Who-Age-(Weight)-What-Severity-"
             "When-Treated-Allergy-Comorbidity -- ระบบอาจตรวจพลาด ให้ยึดข้อความผู้ใช้เป็นหลัก):**",
             "- ทราบแล้ว: " + (", ".join(known) or "-")]
    if dx_given:
        lines.append("- ผู้ใช้ระบุการวินิจฉัย/ภาวะมาแล้ว -> ตอบตามการวินิจฉัยนั้น (ประเภท 2 หรือ 5) ไม่นับอาการที่ไม่ได้บอกเป็นข้อมูลไม่ครบ")
    if minimal_missing:
        lines.append(f"- ข้อมูลขั้นต่ำเพื่อประเมิน/วินิจฉัยที่ยังขาด ({n_min} รายการ):")
        lines += [f"    - {x}" for x in minimal_missing]
    else:
        lines.append("- ข้อมูลขั้นต่ำเพื่อประเมิน/วินิจฉัย: ครบ")
    if confirm_before_dispense:
        lines.append("- ต้องยืนยันก่อนจ่ายยา:")
        lines += [f"    - {x}" for x in confirm_before_dispense]
    if helpful_missing:
        lines.append("- ข้อมูลที่ช่วยเลือกยา/ความปลอดภัยที่ยังขาด:")
        lines += [f"    - {x}" for x in helpful_missing]
    if n_min <= 1:
        verdict = ("ข้อมูลขั้นต่ำครบ/ขาดไม่เกิน 1 รายการ -> ระบุเป็น **ประเภท 2** เท่านั้น (ไม่ต้องใส่ประเภท 4) และตอบเต็ม 5 ขั้น")
    else:
        verdict = ("ขาดข้อมูลขั้นต่ำตั้งแต่ 2 รายการ -> ตัดสินประเภท 2 vs ประเภท 4 ตามเกณฑ์เดิมใน SYSTEM (ประเภท 4 นำด้วยการซักประวัติ "
                   "ยกเว้นอาการชัดและมีน้ำหนัก/อายุพอคำนวณขนาดยา)")
    lines.append(
        "แนวทางใช้: " + verdict + " -- ถ้าตอบเต็มแต่ยังมีข้อที่ขาด ให้ใส่ **\"ข้อมูลที่ควรซักเพิ่มเติม\" เป็นหัวข้อย่อยตัวหนา "
        "(ไม่ใส่เลข) ใต้หัวข้อ 1 สรุปอาการ** -- คงเลขหัวข้อ 1-5 เดิม, สั้น ≤4 ข้อที่สำคัญที่สุด มีเหตุผลกำกับ -- และถ้าข้อที่ขาด"
        "ทำให้เลือกยาต่างกัน ให้เสนอยาแบบมีเงื่อนไข (\"ถ้า... แนะนำ...\")"
    )
    if ped and f.get("weight") is None:
        lines.append("เด็กที่ยังไม่ทราบน้ำหนัก: ห้ามแต่งน้ำหนัก ให้แสดงขนาดเป็น mg/kg และขอน้ำหนักเพื่อคำนวณ mg/mL ต่อครั้ง")
    return "\n".join(lines), n_min


# ─── Modified Centor (McIsaac) -- คำนวณแบบ deterministic จากข้อความเคส ───────────
# (พบโมเดลรวมคะแนนผิด เช่น 1+1+0+1+0 = "2" และ "สมมติฐาน" คะแนนข้อที่ผู้ใช้ไม่ได้บอก)
_LYMPH_PATTERN = r"ต่อมน้ำเหลือง|lymphaden|lymph\s*node"
_LYMPH_NORMAL_RE = re.compile(r"ต่อมน้ำเหลือง\S{0,12}\s*(?:ปกติ|ไม่โต)", re.IGNORECASE)
# เรียง alternative ให้วลียาวมาก่อน: "ตรวจไม่พบ tonsillar exudate" ต้องนับเป็นการปฏิเสธครั้งเดียว
# (ถ้า "exudate" จับซ้ำเป็นอีก match จะกลายเป็น "พบ" ทั้งที่ผู้ใช้บอกว่าตรวจไม่พบ)
_TONSIL_PATTERN = r"tonsill?ar\s*(?:exudates?|swelling|enlargement)|exudates?|tonsils?|ทอนซิล|ฝ้าขาว|จุดขาว|หนองที่คอ"
_TONSIL_NORMAL_RE = re.compile(r"(?:ทอนซิล|tonsil)\S{0,8}\s*(?:ปกติ|ไม่โต|ไม่บวม|normal)", re.IGNORECASE)
_EXAM_RE = re.compile(r"ตรวจ(?:คอ|ร่างกาย|ช่องปาก)?\s*พบ|ตรวจดู", re.IGNORECASE)


def _finding(text: str, pattern: str, normal_re: "re.Pattern") -> bool | None:
    if normal_re.search(text):
        return False
    return _tri(text, pattern)


def _temps(text: str) -> list[float]:
    vals = [float(x) for x in re.findall(r"(\d{2}(?:\.\d+)?)\s*(?:°|องศา|℃)", text)]
    vals += [float(x) for x in re.findall(r"ไข้\s*(?:สูง\s*)?(\d{2}(?:\.\d+)?)(?!\s*(?:วัน|ปี|ชั่วโมง|ชม))", text)]
    return [v for v in vals if 34 <= v <= 43]


def centor_assessment(text: str, f: dict) -> dict | None:
    """คะแนน Modified Centor รายข้อจากข้อมูลที่ผู้ใช้ให้มาจริง (None = ยังไม่ทราบ -- ห้ามเดา) -- เฉพาะเคสเจ็บคอ"""
    if not f.get("sore_throat"):
        return None
    text = text or ""
    age = f.get("age")
    if age is not None and age < 3:
        return {"applicable": False, "items": [], "known": 0, "max": 0, "unknown": []}
    items: list[tuple[str, int | None, str]] = []
    c = f.get("cough")
    items.append(("ไม่มีอาการไอ (Absence of cough)", 1 if c is False else (0 if c else None),
                  "ผู้ใช้ระบุว่าไม่ไอ" if c is False else ("มีอาการไอ" if c else "ยังไม่ทราบว่ามีไอหรือไม่")))
    if age is None:
        items.append(("อายุ", None, "ยังไม่ทราบอายุ"))
    elif age <= 14:
        items.append(("อายุ 3-14 ปี", 1, f"{age:g} ปี"))
    elif age <= 45:
        items.append(("อายุ 15-45 ปี", 0, f"{age:g} ปี"))
    else:
        items.append(("อายุมากกว่า 45 ปี", -1, f"{age:g} ปี"))
    temps = _temps(text)
    if temps:
        mx = max(temps)
        items.append(("ไข้ ≥38°C", 1 if mx >= 38 else 0, f"วัดได้ {mx:g}°C"))
    elif re.search(r"(?<!ไม่มี)(?<!ไม่)ไข้สูง", text):
        items.append(("ไข้ ≥38°C", 1, "ผู้ใช้ระบุว่าไข้สูง (ไม่ได้ระบุตัวเลข)"))
    elif re.search(r"ไข้ต่ำ", text):
        items.append(("ไข้ ≥38°C", 0, "ไข้ต่ำๆ (มักต่ำกว่า 38°C -- ถ้าวัดได้ ≥38°C ให้นับ +1)"))
    elif f.get("fever") is False or re.search(r"ไม่(?:มี)?ไข้สูง", text):
        items.append(("ไข้ ≥38°C", 0, "ไม่มีไข้/ไม่มีไข้สูง"))
    else:
        items.append(("ไข้ ≥38°C", None, "มีไข้แต่ไม่ทราบอุณหภูมิ" if f.get("fever") else "ยังไม่ทราบเรื่องไข้"))
    ly = _finding(text, _LYMPH_PATTERN, _LYMPH_NORMAL_RE)
    items.append(("ต่อมน้ำเหลืองคอด้านหน้าโต/กดเจ็บ", None if ly is None else (1 if ly else 0),
                  "ยังไม่ทราบ (ไม่ได้ระบุ)" if ly is None else ("ผู้ใช้ระบุว่ามี" if ly else "ผู้ใช้ระบุว่าไม่มี")))
    to = _finding(text, _TONSIL_PATTERN, _TONSIL_NORMAL_RE)
    if to is None and _EXAM_RE.search(text):
        items.append(("ทอนซิลบวม/มีหนอง (exudate/swelling)", 0, "ผลตรวจคอที่ให้มาไม่พบทอนซิลบวม/มีหนอง"))
    else:
        items.append(("ทอนซิลบวม/มีหนอง (exudate/swelling)", None if to is None else (1 if to else 0),
                      "ยังไม่ทราบ (ไม่ได้ระบุ)" if to is None else ("ตรวจพบ" if to else "ตรวจไม่พบ")))
    known = sum(s for _, s, _ in items if s is not None)
    unknown = [lab for lab, s, _ in items if s is None]
    return {"applicable": True, "items": items, "known": known, "max": known + len(unknown), "unknown": unknown}


def centor_note(ca: dict | None) -> str:
    if not ca:
        return ""
    if not ca["applicable"]:
        return ("**Modified Centor:** เด็กอายุต่ำกว่า 3 ปี ไม่ใช้เกณฑ์ Centor (GABHS พบน้อยในกลุ่มนี้) -- ประเมินตาม URI เด็ก 2562")
    lines = ["**Modified Centor (McIsaac) -- ระบบคำนวณจากข้อมูลที่ผู้ใช้ให้มาจริง (เกณฑ์ AAFP หน้า 4 TABLE 2) -- ให้แสดงตามนี้ "
             "ห้ามให้คะแนนข้อที่ 'ยังไม่ทราบ' เอง และห้ามรวมเลขใหม่เป็นค่าอื่น; เกณฑ์และการแปลผลอ้าง [Ref: AAFP, หน้า 4]:**"]
    for lab, s, note in ca["items"]:
        lines.append(f"- {lab}: " + ("ยังไม่ทราบ" if s is None else f"{s:+d} คะแนน") + f" ({note})")
    k = ca["known"]
    if ca["unknown"]:
        lines.append(f"- คะแนนรวมจากข้อที่ทราบ = {k} (ถ้าข้อที่ยังไม่ทราบเป็นบวกทั้งหมด อาจได้ถึง {ca['max']})")
    else:
        lines.append(f"- **คะแนนรวม = {k}**")
    if k >= 3:
        lines.append("- แปลผลตามแนวปฏิบัติจริงไทย (RDU): คะแนน ≥3 -> พิจารณาจ่ายยาปฏิชีวนะ first-line (ชื่อยา+ขนาด+ระยะเวลาจาก Context) "
                     "หลังยืนยันประวัติแพ้ยา -- แสดงคำแนะนำ Guideline ก่อน แล้วต่อด้วยบล็อก 'ในทางปฏิบัติจริง'")
    elif ca["unknown"] and ca["max"] >= 3:
        lines.append("- แปลผล: จากข้อที่ทราบยังต่ำกว่า 3 แต่ข้อที่ยังไม่ทราบอาจทำให้ถึง 3 -> ต้องยืนยันข้อนั้นก่อนตัดสินใจเรื่องยาปฏิชีวนะ")
    else:
        lines.append("- แปลผลตามแนวปฏิบัติจริงไทย (RDU): คะแนน <3 -> หลีกเลี่ยงการจ่ายยาปฏิชีวนะ")
    return "\n".join(lines)


# ─── History-taking gate (Phase2 opt2) -- ข้อมูลไม่ครบตาม pattern -> ซักประวัติก่อน ยังไม่สรุปการรักษา ─────
# pattern ของผู้ใช้: Who - Age - (Weight: เด็ก ≤12 ปี) - What - Severity - When - Any treated - Allergy - Comorbidity
ASK_HEADING = "ข้อมูลที่ต้องซักเพิ่มเติมก่อนสรุปการรักษา"
_FEMALE_RE = re.compile(r"หญิง|ผู้หญิง|female|คุณแม่|ป้า|ยาย|สตรี", re.IGNORECASE)
_ATB_ASK_RE = re.compile(r"ยาปฏิชีวนะ|ยาต้านจุลชีพ|antibiotic|\bATB\b|ยาฆ่าเชื้อ|ยาแก้อักเสบ", re.IGNORECASE)
_ANSWER_NOW_RE = re.compile(
    r"ตอบเลย|ไม่ต้องถาม|ไม่ต้องซัก|ข้อมูลมีเท่านี้|มีข้อมูลเท่านี้|ข้อมูลเท่าที่มี|สรุปเลย|ประเมินจากข้อมูลที่มี", re.IGNORECASE)


def history_checklist(text: str, f: dict) -> dict | None:
    """ตรวจข้อมูลตาม pattern ซักประวัติ -> {'missing': [{key,label,q,why}], 'dx_given', 'ped'} (None ถ้าไม่ใช่คำบรรยายเคส)"""
    if not is_case_description(text, f):
        return None
    text = text or ""
    ped = f.get("group") == "pediatric"
    age = f.get("age")
    child12 = ped and (age is None or age <= 12)
    throat = bool(f.get("sore_throat")) and not (age is not None and age < 3)
    sinus, ear = bool(f.get("sinus")), bool(f.get("ear"))
    missing: list[dict] = []

    def add(key: str, label: str, q: str, why: str) -> None:
        missing.append({"key": key, "label": label, "q": q, "why": why})

    if not _PATIENT_RE.search(text):
        add("who", "ผู้ป่วยคือใคร", "ผู้ป่วยคือใคร (ผู้มาซื้อยาเอง หรือซื้อให้ผู้อื่น เช่น บุตร/ผู้สูงอายุ)",
            "เพื่อประเมินจากข้อมูลของผู้ใช้ยาจริง และเลือกคำแนะนำให้ตรงตัวผู้ป่วย")
    if age is None:
        add("age", "อายุ", "อายุของผู้ป่วย",
            "เพื่อเลือก Guideline ให้ตรงกลุ่มอายุ (เด็กยึด URI เด็ก 2562 / ผู้ใหญ่ยึด AAFP) และตรวจข้อห้ามใช้ยาตามอายุ"
            + (" รวมถึงให้คะแนน Modified Centor ข้ออายุ" if f.get("sore_throat") else ""))
    if child12 and f.get("weight") is None:
        add("weight", "น้ำหนักตัว", "น้ำหนักตัวปัจจุบัน (กก.)",
            "ในเด็กจำเป็นต้องใช้คำนวณขนาดยาตามน้ำหนัก (mg/kg) ทั้งยาลดไข้และยาอื่นๆ ให้ถูกต้องและปลอดภัย")
    if not (f.get("fever") is not None or _TEMP_RE.search(text) or re.search(r"ไข้(?!หวัด)|fever", text, re.IGNORECASE)):
        add("fever", "ไข้", "มีไข้หรือไม่ ถ้ามีวัดได้กี่องศา",
            "เพื่อประเมินความรุนแรงของโรค" + (" และเป็นเกณฑ์ Modified Centor (ไข้ ≥38°C)" if throat else "")
            + (" และใช้ประกอบเกณฑ์ไซนัสอักเสบจากแบคทีเรีย" if sinus else ""))
    if f.get("runny") and not f.get("runny_char"):
        add("runny_char", "ลักษณะน้ำมูก", "ลักษณะน้ำมูก: ใส/เหลว หรือ ข้นเหนียว มีสีเหลือง-เขียว",
            "เพื่อเลือกการรักษาให้ตรง: น้ำมูกใสใช้ยาลดน้ำมูก (antihistamine) ได้ แต่น้ำมูกข้นเหนียวไม่ควรใช้ antihistamine "
            "รุ่นที่ 1 เพราะทำให้เหนียวข้นขึ้น ควรล้างจมูกด้วยน้ำเกลือแทน")
    if f.get("cough") and not f.get("cough_type"):
        add("cough_type", "ลักษณะการไอ", "ลักษณะการไอ: ไอแห้ง/ไม่มีเสมหะ หรือ ไอมีเสมหะ",
            "เพื่อเลือกกลุ่มยาให้ตรง: ไอแห้งใช้ยาบรรเทาอาการไอ (antitussive) ส่วนไอมีเสมหะใช้ยาละลาย/ขับเสมหะ (mucolytic) "
            "-- ใช้แทนกันไม่ได้")
    if throat:
        ca = centor_assessment(text, f) or {"items": []}
        unknown = {lab for lab, s, _ in ca["items"] if s is None}
        if f.get("cough") is None:
            add("cough_presence", "มีไอหรือไม่", "มีอาการไอร่วมด้วยหรือไม่",
                "เป็นเกณฑ์ Modified Centor (ไม่ไอ = +1) ช่วยแยกคออักเสบจากแบคทีเรีย GABHS กับไวรัส")
        if "ทอนซิลบวม/มีหนอง (exudate/swelling)" in unknown:
            add("tonsil", "ลักษณะต่อมทอนซิล", "ตรวจดูต่อมทอนซิล: บวมโต หรือมีหนอง/ฝ้าขาวหรือไม่",
                "เป็นเกณฑ์ Modified Centor (+1) ใช้ตัดสินใจเรื่องยาปฏิชีวนะ")
        if "ต่อมน้ำเหลืองคอด้านหน้าโต/กดเจ็บ" in unknown:
            add("lymph", "ต่อมน้ำเหลืองที่คอ", "คลำต่อมน้ำเหลืองบริเวณคอด้านหน้า: โตหรือกดเจ็บหรือไม่",
                "เป็นเกณฑ์ Modified Centor (+1) ใช้ตัดสินใจเรื่องยาปฏิชีวนะ")
    if not _DURATION_RE.search(text):
        why = ("เพื่อใช้เกณฑ์ IDSA (อาการ ≥10 วันไม่ดีขึ้น/แย่ลงหลังดีขึ้น) แยกไซนัสอักเสบจากไวรัสกับแบคทีเรีย" if sinus else
               "เพื่อประเมินระยะของโรคและกำหนดช่วงเวลาที่อาการควรดีขึ้น" if throat else
               "เพื่อแยกหวัด/ไวรัสทั่วไปออกจากภาวะแทรกซ้อน เช่น ไซนัสอักเสบจากแบคทีเรีย (อาการ ≥10 วัน) และประเมินระยะของโรค")
        add("duration", "ระยะเวลา", "อาการเป็นมากี่วันแล้ว และเคยเป็นแบบนี้มาก่อนหรือไม่", why)
    if not _MEDS_RE.search(text):
        add("meds", "ยาที่ใช้มาก่อน", "ใช้ยาอะไรมาก่อนมาร้านยาหรือยัง (ชื่อยา และได้ผลไหม)",
            "เพื่อป้องกันการใช้ยาซ้ำซ้อน/เกินขนาด (ยาลดไข้มักผสมอยู่ในยาสูตรผสมหลายตัว) และประเมินการตอบสนองต่อยาเดิม"
            + (" -- หูชั้นกลางอักเสบ: ถ้าเคยได้ amoxicillin ใน 30 วัน จะเปลี่ยนยาตัวแรก" if ear else ""))
    if not _ALLERGY_RE.search(text):
        add("allergy", "ประวัติแพ้ยา", "ประวัติแพ้ยา (แพ้ยาอะไร และอาการแพ้เป็นแบบใด เช่น ผื่นแดง ลมพิษ หน้าบวม หายใจลำบาก)",
            "เพื่อเลือกยาที่ปลอดภัย โดยเฉพาะหากต้องใช้ยาปฏิชีวนะ -- ชนิดของอาการแพ้เป็นตัวกำหนดว่าใช้ยากลุ่ม cephalosporin แทนได้หรือไม่")
    if not _COMORBID_RE.search(text):
        if ped:
            add("comorbid", "โรคประจำตัว", "โรคประจำตัว (เช่น หอบหืด ภูมิแพ้ G6PD โรคหัวใจ)",
                "เพื่อตรวจข้อห้ามของยาในเด็ก เช่น NSAIDs ในหอบหืด และผลิตภัณฑ์บางชนิดในผู้ที่เป็น G6PD")
        else:
            fem = bool(_FEMALE_RE.search(text))
            add("comorbid", "โรคประจำตัว", "โรคประจำตัว/ภาวะพิเศษ (เช่น ความดันโลหิตสูง โรคหัวใจ โรคไต โรคตับ แผลในกระเพาะอาหาร หอบหืด"
                + (" ตั้งครรภ์/ให้นมบุตร" if fem else "") + ")",
                "เพื่อตรวจข้อห้าม/ข้อควรระวังของยา เช่น ยาแก้คัดจมูกในความดันสูง/โรคหัวใจ, NSAIDs ในโรคไต/แผลในกระเพาะ/หอบหืด"
                + (" และความปลอดภัยของยาในหญิงตั้งครรภ์/ให้นมบุตร" if fem else ""))
    return {"missing": missing, "dx_given": bool(_DIAGNOSIS_GIVEN_RE.search(text)), "ped": ped}


def should_ask_first(text: str, chk: dict | None) -> bool:
    """ข้อมูลไม่ครบตาม pattern -> ต้องซักประวัติก่อน (ยกเว้น ผู้ใช้ระบุการวินิจฉัยมาแล้ว / สั่งให้ตอบจากข้อมูลที่มี)"""
    if not chk or not chk["missing"] or chk["dx_given"]:
        return False
    return not _ANSWER_NOW_RE.search(text or "")


def ask_first_reply(summary: str, chk: dict, text: str, f: dict) -> str:
    """คำตอบ 'ซักประวัติก่อน' (โครงสร้าง deterministic: ประเภท + สรุปอาการ + คำถามพร้อมเหตุผล + สรุปว่าคำตอบช่วยอะไร)"""
    labels = ", ".join(m["label"] for m in chk["missing"])
    parts: list[str] = []
    if _ATB_ASK_RE.search(text or ""):
        parts.append("ตอบคำถามเรื่องการจ่ายยาปฏิชีวนะได้อย่างมีหลักฐาน")
    parts.append("สรุปการวินิจฉัยแยกโรค")
    if f.get("sore_throat") and not (f.get("age") is not None and f["age"] < 3):
        parts.append("คำนวณคะแนน Modified Centor ให้ครบเพื่อตัดสินใจเรื่องยาปฏิชีวนะ (ทั้งตาม Guideline และแนวปฏิบัติจริงของไทย)")
    if f.get("sinus"):
        parts.append("ประเมินเกณฑ์ไซนัสอักเสบจากแบคทีเรียและความจำเป็นของยาปฏิชีวนะ")
    if f.get("ear"):
        parts.append("ประเมินหูชั้นกลางอักเสบและเลือกยาปฏิชีวนะตัวแรกให้เหมาะ")
    parts.append("เลือกยาบรรเทาอาการให้ตรงลักษณะอาการ" + (" พร้อมคำนวณขนาดยาตามน้ำหนักตัว" if chk["ped"] else ""))
    parts.append("และตรวจข้อห้ามใช้ยาของผู้ป่วยรายนี้")
    out = [
        f"ในเคสนี้ถือเป็น **เคสผู้ป่วยใหม่ (ประเภท 2) และ ข้อมูลไม่ครบ (ประเภท 4)** เนื่องจากยังขาดข้อมูลตามหลักการซักประวัติร้านยา "
        f"({labels}) ซึ่งจำเป็นต่อการวินิจฉัยและเลือกยาอย่างปลอดภัย จึงขอซักประวัติเพิ่มเติมก่อนสรุปการรักษาครับ",
        "",
        "**1. สรุปอาการ**",
        (summary or "").strip() or (text or "").strip(),
        "",
        f"**{ASK_HEADING}:**",
    ]
    out += [f"- **{m['q']}** -- เหตุผล: {m['why']}" for m in chk["missing"]]
    out += ["", "**สรุป:** เมื่อได้ข้อมูลข้างต้น ผมจะสามารถ" + " ".join(parts) + " ได้แม่นยำและปลอดภัยยิ่งขึ้นครับ "
            "(หากข้อใดไม่ทราบ แจ้งได้เลย ผมจะประเมินจากข้อมูลที่มีให้)"]
    return "\n".join(out)


def is_ask_first_reply(text: str) -> bool:
    return ASK_HEADING in (text or "") and "การรักษาด้วยยา" not in (text or "")


def post_ask_note(chk: dict | None) -> str:
    """เทิร์นที่ผู้ใช้ตอบคำถามซักประวัติแล้ว -> ตอบเต็ม (ไม่ถามซ้ำ) ข้อที่ยังไม่ทราบให้เสนอแบบมีเงื่อนไข"""
    lines = ["**ผู้ใช้ตอบคำถามซักประวัติของเคสนี้แล้ว (ข้อมูลเคส = ข้อความเคสเดิมในแชท + คำตอบล่าสุด):** ให้ตอบเต็มโครงสร้าง 5 ขั้น "
             "เป็น **เคสผู้ป่วยใหม่ (ประเภท 2)** โดยสรุปอาการที่รวมข้อมูลใหม่แล้ว -- ห้ามตอบแบบประเภท 4 และห้ามถามซ้ำข้อที่ผู้ใช้ตอบแล้ว"]
    rest = [m["label"] for m in (chk or {}).get("missing", [])]
    if rest:
        lines.append("- ข้อที่ยังไม่ทราบ/ผู้ใช้ไม่ได้ตอบ: " + ", ".join(rest) + " -> ประเมินจากข้อมูลที่มี เสนอยาแบบมีเงื่อนไข "
                     "(\"ถ้า... แนะนำ...\") และระบุสั้นๆ ใต้หัวข้อ 1 ว่าควรยืนยันข้อใดก่อนจ่ายยา")
    return "\n".join(lines)


# ─── Dosage-form label guard (ยาพ่นคอ / ยาอม / ยากลั้วคอ ต้องตรงรูปแบบของยาในรายการ) ─────────────
_FORM_WORDS = (("spray", r"ยาพ่น"), ("lozenge", r"ยาอม"), ("gargle", r"ยากลั้วคอ"))
_LABEL_LINE_RE = re.compile(r"^(\s*(?:[-*•]\s+)?(?:\*\*)?)([^:\n*\[\]]{0,60}?(?:ยาอม|ยาพ่น|ยากลั้วคอ)[^:\n*\[\]]{0,50}?)((?:\*\*)?\s*:)")
_FORM_KEYS: list[tuple["re.Pattern", str]] | None = None


def _form_keys() -> list[tuple["re.Pattern", str]]:
    global _FORM_KEYS
    if _FORM_KEYS is None:
        keys: dict[str, str] = {}
        for d in load_formulary():
            if d.get("form"):
                for k in d["keys"]:
                    keys.setdefault(k.lower(), d["form"])
        _FORM_KEYS = [(re.compile(r"(?<![A-Za-z])" + re.escape(k).replace(r"\ ", r"\s*") + r"(?![A-Za-z])", re.IGNORECASE), fm)
                      for k, fm in sorted(keys.items(), key=lambda kv: len(kv[0]), reverse=True)]
    return _FORM_KEYS


def _forms_in(text: str) -> set[str]:
    out: set[str] = set()
    taken: list[tuple[int, int]] = []
    for rx, fm in _form_keys():
        for m in rx.finditer(text or ""):
            if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            out.add(fm)
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _label_block_end(lines: list[str], i: int) -> int | None:
    """ตำแหน่งบรรทัดที่ปิดบล็อกของหัวข้อหมวดยาบรรทัด i (บรรทัดถัดไปที่ไม่เยื้องลึกกว่า) -- None = ยังไม่ปิด (ตอนสตรีม)"""
    base = _indent(lines[i])
    for j in range(i + 1, len(lines)):
        if lines[j].strip() and _indent(lines[j]) <= base:
            return j
    return None


def open_form_label_start(text: str) -> int | None:
    """(streaming) ตำแหน่งเริ่มบรรทัดหัวข้อหมวดยาเฉพาะที่คอที่บล็อกยังรับรายการยาไม่ครบ -> ต้องกันไว้ก่อนส่ง"""
    lines = text.split("\n")
    pos = 0
    starts = []
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    for i in range(len(lines) - 1, -1, -1):
        if _LABEL_LINE_RE.match(lines[i]):
            return starts[i] if _label_block_end(lines, i) is None else None
    return None


def fix_form_labels(text: str) -> str:
    """หัวข้อหมวดที่เรียกรูปแบบผิด (เช่น 'ยาอม:' แต่รายการเป็นยาพ่น/ยากลั้วคอ) -> แก้ชื่อหมวดให้ตรงรูปแบบยาที่อยู่ใต้หัวข้อ"""
    if not text or not re.search(r"ยาอม|ยาพ่น|ยากลั้วคอ", text):
        return text
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = _LABEL_LINE_RE.match(ln)
        if not m:
            continue
        end = _label_block_end(lines, i)
        block = ln[m.end():] + "\n" + "\n".join(lines[i + 1: end if end is not None else len(lines)])
        forms = _forms_in(block)
        if not forms:
            continue
        label = m.group(2)
        have = {fm for fm, pat in _FORM_WORDS if re.search(pat, label)}
        if have == forms:
            continue
        suffix = "บรรเทาอาการเจ็บคอ" if "เจ็บคอ" in label else ""
        names = {"spray": "ยาพ่น" if suffix else "ยาพ่นคอ", "lozenge": "ยาอม", "gargle": "ยากลั้วคอ"}
        new_label = "/".join(names[fm] for fm, _ in _FORM_WORDS if fm in forms) + suffix
        lines[i] = ln[: m.start(2)] + new_label + ln[m.end(2):]
    return "\n".join(lines)


def drugs_cited_by_page(text: str) -> dict[str, list[str]]:
    """{เลขหน้า Dose: [ชื่อยาที่คำตอบกล่าวถึงจริง]} -- ใช้ตั้งชื่อแหล่งอ้างอิง Dose ให้ตรงยาที่อ้าง"""
    out: dict[str, list[str]] = {}
    for d in load_formulary():
        if any(re.search(r"(?<![A-Za-z])" + re.escape(k).replace(r"\ ", r"\s*") + r"(?![A-Za-z])", text or "", re.IGNORECASE)
               for k in d["keys"]):
            lst = out.setdefault(d["page"], [])
            if d["display"] not in lst:
                lst.append(d["display"])
    return out


# ─── Penicillin allergy severity (safety backstop) ───────────────────────────
# ลมพิษ/anaphylaxis/angioedema/หายใจลำบาก = type 1 (IgE) -> ห้าม beta-lactam ทั้งหมด "รวม cephalosporin"
# (พบโมเดลตีความ "ผื่นลมพิษ" ผิดเป็น non-type 1 แล้วแนะนำ Cephalexin) -> ระบบตรวจแบบ deterministic แล้วแนบบันทึกให้ชัด
_ALLERGY_DRUG_RE = re.compile(r"penicill|เพนนิซิลลิน|เพนิซิลลิน|amoxi|อะม็อก|beta-?lactam|augmentin|cephal|เซฟ", re.IGNORECASE)
_ALLERGY_WORD_RE = re.compile(r"(?<!ภูมิ)แพ้|allerg", re.IGNORECASE)
_TYPE1_PATTERN = (r"ลมพิษ|urticaria|hives|anaphyla|angioedema|แน่นหน้าอก|หายใจลำบาก|หายใจไม่ออก|ความดัน(?:โลหิต)?ตก|"
                  r"หน้าบวม|ปากบวม|ตาบวม|บวมที่(?:หน้า|ปาก|ตา|คอ)")
_MILD_PATTERN = r"ผื่นแดงเล็กน้อย|ผื่นเล็กน้อย|ผื่นไม่รุนแรง|ผื่นแดง|ผื่นขึ้น|maculopapular|non-?type\s*1"


def allergy_note(text: str) -> str:
    """บันทึกความรุนแรงการแพ้ penicillin จากข้อความเคส ('' ถ้าไม่ได้กล่าวถึง/ไม่ทราบชนิดการแพ้ -> ให้ LLM ซักเอง)"""
    text = text or ""
    if not (_ALLERGY_WORD_RE.search(text) or re.search(r"เคยรับ[^\n]{0,40}(?:ผื่น|ลมพิษ)", text)):
        return ""
    if not _ALLERGY_DRUG_RE.search(text):
        return ""
    if _tri(text, _TYPE1_PATTERN) is True:
        return ("**ระบบตรวจพบประวัติแพ้ penicillin แบบรุนแรง/type 1 (IgE-mediated: ลมพิษ, anaphylaxis, angioedema, หายใจลำบาก, "
                "ความดันตก)** -- \"ผื่นลมพิษ\" = type 1 แม้ไม่ถึงขั้น anaphylaxis -> **ห้ามใช้ beta-lactam ทั้งหมด รวม cephalosporin** "
                "(เช่น amoxicillin, amoxicillin/clavulanate, cephalexin, cefdinir, cefpodoxime, cefixime) ให้เลือกเฉพาะยาที่ไม่ใช่ "
                "beta-lactam จากตารางใน Context (เช่น Clindamycin, Azithromycin/macrolide, Doxycycline ตามโรค)")
    if _tri(text, _MILD_PATTERN) is True:
        return ("**ระบบตรวจพบประวัติแพ้ penicillin แบบไม่รุนแรง (non-type 1: ผื่นแดงเล็กน้อย ไม่มีลมพิษ/บวม/หายใจลำบาก)** -> "
                "cephalosporin เป็นทางเลือกได้ตามตารางใน Context (ยืนยันลักษณะการแพ้อีกครั้งก่อนจ่าย)")
    return ""


def practice_flags(f: dict) -> list[str]:
    """เคสที่เข้าข่าย Expert Opinion (แนวปฏิบัติจริงไทย) -- ให้ LLM แสดงบล็อก 'ในทางปฏิบัติจริง' ต่อจากคำแนะนำ Guideline"""
    flags: list[str] = []
    if f.get("sore_throat"):
        flags.append("เจ็บคอ/คออักเสบ -> คำนวณคะแนน Centor/McIsaac จากข้อมูลจริงของเคสนี้ให้ครบทุกเกณฑ์ (ไม่ใช่แสดงเกณฑ์ลอยๆ) "
                     "แล้วแสดงทั้งคำแนะนำ Guideline และ RDU Practice ไทย (คะแนน 3-5 พิจารณาจ่ายยาปฏิชีวนะ first-line พร้อมขนาด+"
                     "ระยะเวลา / <3 หลีกเลี่ยง)")
    if f.get("sinus") and f.get("group") != "pediatric":
        flags.append("ไซนัสอักเสบ -> แสดงเกณฑ์ IDSA ตาม Guideline แล้วเสริมแนวปฏิบัติจริงของไทย (แม้ไม่ถึง 10 วัน "
                     "พิจารณาจ่ายยาปฏิชีวนะได้ถ้าอาการ/อาการแสดงเข้าได้กับไซนัสชัดเจน ตามดุลพินิจเภสัชกร) "
                     "+ ต้องยืนยันประวัติแพ้ยาก่อนจ่ายยาปฏิชีวนะ")
    return flags


# ─── Brand gateway (กันเชิงโฆษณา: ชื่อการค้าต้องมีตัวยาสำคัญกำกับ) ─────────────
# ตัวยาสำคัญของผลิตภัณฑ์ที่ "ชื่อในตาราง" ไม่ได้ระบุตัวยา -- ยืนยันจากเนื้อหาแถวยาเดียวกันใน Dose table
# (Solmax=Carbocisteine, Difflam=Benzydamine, Kamilosan=คาโมไมล์, Propoliz=ผลิตภัณฑ์จากผึ้ง, Betadine=ไอโอดีน)
# + Augmentin (AAFP ระบุคู่ Amoxicillin/clavulanate)
_BRAND_SUPPLEMENT: list[tuple[str, str, list[str]]] = [
    (r"Kamill?osan", "สารสกัดดอกคาโมมายล์ (Chamomile)", ["chamomile", "คาโมมายล์", "คาโมไมล์"]),
    (r"Propoliz", "สารสกัดโพรโพลิส (Propolis) จากผึ้ง", ["propolis", "โพรโพลิส"]),
    (r"Betadine|เบตาดีน", "Povidone-iodine", ["povidone", "โพวิโดน", "iodine", "ไอโอดีน"]),
    (r"Difflam", "Benzydamine HCl", ["benzydamine", "เบนซีดามีน"]),
    (r"Solmax", "Carbocysteine", ["carbocysteine", "carbocisteine", "คาร์โบซิสเทอีน"]),
    (r"Augmentin", "Amoxicillin/clavulanate", ["amoxicillin", "clavulan", "อะม็อกซี"]),
]
def brand_ingredient(d: dict) -> str:
    """ตัวยา/สารสำคัญของผลิตภัณฑ์ (สำหรับแสดงคู่ชื่อการค้าใน Catalog) -- '' ถ้าเป็นยาชื่อสามัญหรือชื่อที่แสดงมีตัวยาแล้ว"""
    if not (d.get("product") or d.get("form")):
        return ""
    m = re.search(r"\(([^)]*)\)", d["name"])
    if m and re.search(r"มีตัวยา|\d", m.group(1)):
        if m.group(0) in d.get("display", ""):
            return ""
        ingr = re.sub(r"มีตัวยา", "", m.group(1))
        ingr = re.sub(r"\s*\d[\d.,-]*\s*(?:mg|มิลลิกรัม|ml|มิลลิลิตร)\b", "", ingr, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", ingr).strip(" +")
    for pat, label, _ in _BRAND_SUPPLEMENT:
        if re.match(pat, d["name"], re.IGNORECASE):
            return label
    return ""


_DESCRIPTOR = r"(?:\s+(?:M|prin|DEY|spray|mouth|throat|forte|gargle|lozenges?|capsule|syrup|plus|\d+\s*ml)(?![A-Za-z]))*"
# (regex, ตัวยา, คีย์ตรวจว่าระบุตัวยาแล้ว, ต้องครบทุกคีย์?) -- สูตรผสมต้องเห็นครบทุกตัวยา / คำพ้อง (ไทย-อังกฤษ) เห็นตัวใดก็ได้
_BRAND_ENTRIES: list[tuple["re.Pattern", str, list[str], bool]] | None = None


def _brand_entries() -> list[tuple["re.Pattern", str, list[str], bool]]:
    global _BRAND_ENTRIES
    if _BRAND_ENTRIES is not None:
        return _BRAND_ENTRIES
    derived: list[tuple[str, str]] = []   # (head, ingredient label)
    for d in load_formulary():
        name = d["name"]
        m = re.match(r"^([A-Za-z][^()]*?)\s*\(([^)]*)\)", name)
        if not m or not re.search(r"มีตัวยา|\d", m.group(2)):
            continue
        ingr = re.sub(r"มีตัวยา", "", m.group(2))
        ingr = re.sub(r"\s*\d[\d.,-]*\s*(?:mg|มิลลิกรัม|ml|มิลลิลิตร)\b", "", ingr, flags=re.IGNORECASE)
        ingr = re.sub(r"\s+", " ", ingr).strip(" +")
        for head in re.split(r"\s*/\s*", m.group(1)):
            if head.strip():
                derived.append((head.strip(), ingr))
    first_word_ingr: dict[str, set[str]] = {}
    for head, ingr in derived:
        first_word_ingr.setdefault(head.split()[0].lower(), set()).add(ingr)
    entries: list[tuple["re.Pattern", str, list[str], bool]] = []
    for head, ingr in derived:
        words = head.split()
        if len(first_word_ingr[words[0].lower()]) > 1:     # ชื่อแรกใช้ร่วมหลายสูตร (เช่น Strepsils) -> ต้องระบุสูตร
            pat = r"\s+".join(re.escape(w) for w in words) + r"(?![A-Za-z])"
        else:
            pat = re.escape(words[0]) + r"(?![A-Za-z])" + _DESCRIPTOR
        kws = [re.split(r"[\s-]", x.strip())[0].lower() for x in ingr.split("+") if x.strip()]
        entries.append((re.compile(r"(?<![A-Za-z])" + pat, re.IGNORECASE), ingr, [k for k in kws if len(k) >= 4], True))
    for pat, label, kws in _BRAND_SUPPLEMENT:
        entries.append((re.compile(r"(?<![A-Za-z])(?:" + pat + r")(?![A-Za-z])" + _DESCRIPTOR, re.IGNORECASE),
                        label, kws, False))
    _BRAND_ENTRIES = entries
    return entries


def mentions_brand(text: str) -> bool:
    return any(e[0].search(text or "") for e in _brand_entries())


def apply_brand_gateway(text: str, line_prefix: str = "") -> str:
    """ชื่อการค้าที่ไม่มีตัวยาสำคัญในบรรทัดเดียวกัน -> เติม ' (ตัวยา: ...)' หลังชื่อครั้งแรกของบรรทัดนั้น"""
    if not text:
        return text
    entries = _brand_entries()
    if not entries:
        return text
    out: list[str] = []
    for i, line in enumerate(text.split("\n")):
        ctx = ((line_prefix if i == 0 else "") + line).lower()
        new = line
        for rx, label, kws, need_all in entries:
            m = rx.search(new)
            if not m or not kws:
                continue
            disclosed = all(k in ctx for k in kws) if need_all else any(k in ctx for k in kws)
            if disclosed or "ตัวยา" in new[m.end(): m.end() + 14]:
                continue
            new = new[: m.end()] + f" (ตัวยา: {label})" + new[m.end():]
            ctx += " " + label.lower()
        out.append(new)
    return "\n".join(out)


# ─── Dose page lookup (เลขหน้า Dose ของยาที่ถูกกล่าวถึงจริงในบรรทัดนั้น) ──────

_PAGE_KEYS: list[tuple["re.Pattern", str]] | None = None


def _page_keys() -> list[tuple["re.Pattern", str]]:
    global _PAGE_KEYS
    if _PAGE_KEYS is not None:
        return _PAGE_KEYS
    pairs: dict[str, str] = {}
    first: dict[str, set[str]] = {}
    for d in load_formulary():
        for k in d["keys"]:
            pairs.setdefault(k.lower(), d["page"])
        for part in re.split(r"\s*/\s*", re.split(r"\s*\(", d["name"])[0]):
            w = part.split()[0] if part.split() else ""
            if len(w) >= 5 and re.match(r"^[A-Za-z]", w):
                first.setdefault(w.lower(), set()).add(d["page"])
    for w, pages in first.items():
        if len(pages) == 1:                       # ชื่อแรกไม่กำกวม (เช่น Decolgen, Mefenamic) -> ใช้จับได้
            pairs.setdefault(w, next(iter(pages)))
    keys = sorted(pairs.items(), key=lambda kv: len(kv[0]), reverse=True)
    _PAGE_KEYS = [(re.compile(r"(?<![A-Za-z])" + re.escape(k).replace(r"\ ", r"\s*") + r"(?![A-Za-z])", re.IGNORECASE), p)
                  for k, p in keys]
    return _PAGE_KEYS


def dose_drug_hits(text: str) -> list[tuple[int, str]]:
    """[(ตำแหน่ง, เลขหน้า Dose)] ของยาที่พบในข้อความ (คีย์ยาวก่อน ไม่ซ้อนทับ) เรียงตามตำแหน่ง"""
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for rx, page in _page_keys():
        for m in rx.finditer(text or ""):
            if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), page))
    return sorted(hits)
