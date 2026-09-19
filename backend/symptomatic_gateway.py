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
        # แถวเดียวที่รวมหลายสูตร (เช่น "Strepsils chesty cough (Ambroxol) / Strepsils dry cough (Dextromethorphan)")
        # ต้องคงชื่อเต็ม ไม่งั้นเคสไอแห้งจะได้ชื่อสูตรละลายเสมหะไปแทน
        if name.count("(") > 1:
            return name.strip()
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
    """อายุขั้นต่ำที่ตาราง Dose ระบุ -- ตารางใหม่เขียนได้หลายแบบ (ไทย/อังกฤษ/ช่วงอายุ)
    เช่น 'อายุตั้งแต่ 3 ปีขึ้นไป', 'เด็กอายุ 6-12 ปี', 'Children 2-12 years', 'Children 2 to <6 years',
    'children over 12 years' -> ใช้ "อายุต่ำสุดที่ตารางระบุว่าใช้ได้" (ไม่ใช่ช่วงอายุที่มากที่สุด
    ไม่งั้นเด็กจะถูกตัดตัวเลือกที่ใช้ได้จริงทิ้ง) และเคารพข้อห้ามตามอายุเป็นพื้นขั้นต่ำเสมอ"""
    dose_txt = _nz(drug.get("ped") or drug.get("adult"))
    warn_txt = _nz(drug.get("warn"))
    found: list[float] = []
    for pat in (r"อายุ(?:ตั้งแต่)?(\d+)ปีขึ้นไป", r"เด็กอายุ(\d+)-\d+ปี",
                r"children(?:aged)?(\d+)(?:to|-|–|—)", r"children(?:over|>|≥)(\d+)(?:yr|years?)",
                r"(\d+)(?:yearsofageandolder|ปีขึ้นไป)"):
        found += [float(x) for x in re.findall(pat, dose_txt)]
    neg = [float(x) for x in re.findall(
        r"(?:ไม่ควรใช้|ห้ามใช้|ไม่แนะนำให้ใช้)(?:ใน|กับ)?เด็กอายุต่ำกว่า(\d+)", warn_txt + dose_txt)]
    floor = max(neg) if neg else None
    if found:
        low = min(found)
        return max(low, floor) if floor is not None else low
    return floor


# ยาพ่นจมูกสเตียรอยด์ -- ตารางใหม่เขียนข้อบ่งใช้เป็น "allergic rhinitis" เหมือนยาแก้แพ้
# จึงต้องแยกด้วยชื่อตัวยา (ความรู้ระดับกลุ่มยา ไม่ใช่การ hard-code รายเคส)
_INCS_NAMES = ("mometasone", "fluticasone", "budesonide", "triamcinolone", "beclomet", "ciclesonide")
_CONGESTION_KEYS = ("nasal congestion", "nasal and nasopharyngeal", "sinus congestion", "decongestant")
_RHINITIS_KEYS = ("allergic rhinitis", "hay fever", "runny nose", "rhinorrhea", "sneezing",
                  "upper respiratory allergies", "nasal allergies", "antihistamine", "vasomotor rhinitis")
_FEVER_PAIN_KEYS = ("fever", "antipyretic", "analgesic", "pain")
_DRY_COUGH_KEYS = ("cough (suppressant)", "cough suppressant", "antitussive")
_WET_COUGH_KEYS = ("mucolytic", "expectorant", "viscid", "viscous mucous", "viscous mucus",
                   "abnormal mucous", "abnormal, viscid", "mucous secretion", "mucus secretion")
_THROAT_KEYS = ("sore throat", "throat", "oral mucosa")


# ชั้นสำรอง: ถ้ากฎจากข้อบ่งใช้จัดกลุ่มไม่ได้ (ตาราง ingest ใหม่เขียนข้อบ่งใช้เฉพาะโรคหลักของยา เช่น
# Piroxicam = arthritis, Hydroxyzine = pruritus) ให้จัดกลุ่มจาก "ชื่อตัวยา" ตามความรู้ระดับกลุ่มยา
# -> ยาทุกตัวในตารางถูกนำมาใช้ได้ ไม่ตกหล่น (แต่ตัวที่ไม่ใช่ตัวเลือกทั่วไปของ URI ยังอยู่ท้ายสุดตาม tier)
_NAME_CLASS_FALLBACK: tuple[tuple[tuple[str, ...], str], ...] = (
    (("paracetamol", "acetaminophen", "ibuprofen", "naproxen", "diclofenac", "mefenamic",
      "piroxicam", "celecoxib", "etoricoxib", "aspirin"), "fever_pain"),
    (("chlorpheniramine", "brompheniramine", "diphenhydramine", "cyproheptadine", "hydroxyzine",
      "cetirizine", "loratadine", "fexofenadine", "levocetirizine", "desloratadine", "bilastine"), "antihistamine"),
    (("oxymetazoline", "xylometazoline", "naphazoline", "pseudoephedrine", "phenylephrine"), "decongestant"),
    (("dextromethorphan", "levodropropizine"), "cough_dry"),
    (("carbocysteine", "acetylcysteine", "bromhexine", "ambroxol", "guaifenesin"), "cough_wet"),
)
# รูปแบบยาในแถวนั้นไม่ตรงกับการใช้ในกลุ่มนี้ (เช่น Naphazoline แถวใหม่เป็นยาหยอดตา ไม่ใช่ชนิดพ่นจมูก)
_ROUTE_MISMATCH: tuple[tuple[str, str, str], ...] = (
    ("decongestant", "ophthalmic", "เป็นยาแก้คัดจมูกชนิดเดี่ยวที่ใช้ได้ในทางปฏิบัติ แต่แถวในตาราง Dose ฉบับปัจจุบันเป็นข้อมูลของรูปแบบยาหยอดตา (ophthalmic) จึงไม่มีขนาดยาสำหรับพ่น/หยดจมูกให้อ้างอิง -- ถ้าจะใช้ ต้องตรวจขนาดจากฉลากผลิตภัณฑ์ชนิดพ่นจมูก (ห้ามใช้ขนาดยาหยอดตา)"),
)


def _fallback_class(d: dict) -> tuple[set[str], str]:
    """(คลาสสำรองจากชื่อตัวยา, เหตุผลที่ห้ามแนะนำถ้ารูปแบบยาไม่ตรง)"""
    name = d["name"].lower()
    dose_txt = ((d.get("adult") or "") + " " + (d.get("ped") or "")).lower()
    for keys, cls in _NAME_CLASS_FALLBACK:
        if any(k in name for k in keys):
            for want_cls, bad_route, why in _ROUTE_MISMATCH:
                if cls == want_cls and bad_route in dose_txt and "nasal" not in dose_txt:
                    return {cls}, why
            return {cls}, ""
    return set(), ""

def _classify(d: dict) -> dict:
    """จัดกลุ่มยาจาก 'ข้อบ่งใช้ในตาราง' (ตารางใหม่เป็น monograph อังกฤษ + ข้อความไทยของผลิตภัณฑ์)"""
    raw = " ".join((d["indication"] or "").split()).lower()   # คงช่องว่างไว้สำหรับคีย์ภาษาอังกฤษ
    ind = _nz(d["indication"])                                 # ตัดช่องว่าง สำหรับคีย์ภาษาไทย
    name = d["name"].lower()
    has_en = lambda keys: any(k in raw for k in keys)          # noqa: E731
    has_th = lambda keys: any(k in ind for k in keys)          # noqa: E731

    congestion = has_en(_CONGESTION_KEYS) or has_th(("คัดจมูก",))
    rhinitis = has_en(_RHINITIS_KEYS) or has_th(("ลดน้ำมูก", "น้ำมูกไหล", "แก้แพ้"))
    febrile = has_en(_FEVER_PAIN_KEYS) or has_th(("ลดไข้", "แก้ปวด", "เป็นไข้", "ปวดศีรษะ"))
    dry_cough = has_en(_DRY_COUGH_KEYS) or has_th(("ไอแห้ง", "ไม่มีเสมหะ")) or "dextromethorphan" in name
    # "มีเสมหะ" ต้องดูคำปฏิเสธข้างหน้า: ind ถูกตัดช่องว่างทิ้ง ทำให้ "ไอแบบไม่มีเสมหะ" (Dextromethorphan,
    # Levodropropizine, Strepsils dry cough = ยากดไอ) มี "มีเสมหะ" เป็นสตริงย่อย -> เคยถูกจัดเป็น
    # ยาละลาย/ขับเสมหะ แล้วถูกเสนอในเคส "ไอมีเสมหะ" ซึ่งผิดหลักการใช้ยา (ห้ามกดไอที่มีเสมหะ)
    # ยาที่บ่งใช้ทั้งสองแบบจริง (Terco-D: "...ไอแบบมีเสมหะและไอแห้งหรือไอไม่มีเสมหะ") ยังเข้าเงื่อนไข
    # เพราะมีตำแหน่ง "มีเสมหะ" ที่ไม่ได้ตามหลัง "ไม่" อยู่ด้วย
    wet_cough = (has_en(_WET_COUGH_KEYS) or has_th(("ขับเสมหะ", "ละลายเสมหะ"))
                 or bool(re.search(r"(?<!ไม่)มีเสมหะ", ind)))
    throat = has_en(_THROAT_KEYS) or has_th(("เจ็บคอ", "ระคายคอ", "ลำคอ", "คออักเสบ", "ช่องปาก"))
    is_spray = "spray" in name or has_th(("สเปรย์", "พ่น"))
    is_lozenge = "lozenge" in name or "strepsils" in name or has_th(("ยาอม",))
    is_gargle = "gargle" in name or has_th(("กลั้วคอ", "บ้วนปาก"))

    cls: set[str] = set()
    if any(n in name for n in _INCS_NAMES):
        cls.add("incs")
    elif congestion and rhinitis:
        cls.add("combo_flu" if (febrile and has_th(("ไข้",)) or "fever" in raw) else "combo_cold")
    elif congestion:
        cls.add("decongestant")
    elif rhinitis:
        cls.add("antihistamine")
    elif febrile:
        cls.add("fever_pain")
    if dry_cough:
        cls.add("cough_dry")
    if wet_cough:
        cls.add("cough_wet")
    if throat and is_spray:
        cls.add("throat_spray")
    if throat and is_lozenge:
        cls.add("throat_lozenge")
    if is_gargle:
        cls.add("gargle")

    route_note = ""
    if not cls:
        cls, route_note = _fallback_class(d)

    # รูปแบบยา (form) มาจาก "รูปแบบจริงของผลิตภัณฑ์" ไม่ผูกกับกลุ่มอาการเจ็บคอ --
    # ไม่งั้นยาอมที่ข้อบ่งใช้ไม่ได้พูดถึงคอ (เช่น Strepsils chesty cough = ยาอมละลายเสมหะ)
    # จะไม่มี form แล้วถูกเรียกชื่อหมวดผิด (เคยเจอ: เรียกยากลั้วคอว่า "ยาอม")
    form = "spray" if is_spray else "lozenge" if is_lozenge else "gargle" if is_gargle else None
    return {
        "classes": cls,
        "first_gen": "+" not in d["name"] and any(k in name for k in _FIRST_GEN_AH),
        "herbal": bool(re.match(r"^[฀-๿]", d["name"])),
        "nsaid": "fever_pain" in cls and "paracetamol" not in name,
        "product": bool(re.search(r"\([^)]*(?:มีตัวยา|\d)", d["name"])) or bool(_PRODUCT_RE.match(d["name"])),
        "rare_uri": any(k in name for k in _RARE_URI),
        "form": form,
        "route_note": route_note,
    }


# ผลิตภัณฑ์ (ชื่อการค้า/สูตรผสม) -- จัดเป็น "ผลิตภัณฑ์ทางเลือก" ต่อจากยาหลักชื่อสามัญ (กันเชิงโฆษณา + ยาหลักขึ้นก่อน)
_PRODUCT_RE = re.compile(r"^(?:Solmax|Muclear|Strepsils|Terco|Decolgen|Difflam|Kamill?osan|Propoliz|Betadine)", re.IGNORECASE)
# มีในตาราง Dose แต่ไม่ใช่ตัวเลือกทั่วไปสำหรับอาการ URI (แสดงเฉพาะเมื่อผู้ใช้ขอดูทั้งหมด) -- ความรู้ระดับกลุ่มยา
_RARE_URI = ("aspirin", "piroxicam", "celecoxib", "etoricoxib", "diphenhydramine", "cyproheptadine",
             "hydroxyzine")
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
    # กันเงียบ: ถ้าตาราง Dose ถูก ingest ใหม่แล้วเขียนข้อบ่งใช้คนละแบบ กฎจัดกลุ่มอาจใช้ไม่ได้
    # -> DOSE CATALOG จะว่างโดยไม่มีใครรู้ จึงเตือนใน log ให้ไปตรวจกฎใน _classify()
    unclassified = [d["display"] for d in out if not d["classes"]]
    if out and len(unclassified) > len(out) * 0.4:
        print(f"[SYMPT] เตือน: จัดกลุ่มยาไม่ได้ {len(unclassified)}/{len(out)} ตัว "
              f"(ตาราง Dose อาจเปลี่ยนรูปแบบ) เช่น {unclassified[:5]}")
    _FORMULARY = out
    return out


def drug_by_name(name: str) -> dict | None:
    for d in load_formulary():
        if d["name"] == name:
            return d
    return None


# ─── Case features (Thai-negation aware) ─────────────────────────────────────

_NEG_TAIL_RE = re.compile(
    r"(ไม่มี(?:อาการ|ภาวะ)?|ไม่พบ(?:ว่ามี)?|ไม่ได้|ไม่|ปฏิเสธ|\bno\b|without|denies)"
    # A negation can govern a *list* of symptoms joined by "หรือ"/"และ"
    # ("ไม่มีอาการไอหรือน้ำมูก" = no cough OR runny nose — negates both), not
    # just the word directly after it. Without this, the match for the
    # *second* item in the list sees only "...ไอหรือ" immediately before it —
    # no negation word right there — and gets scored as present instead of
    # negated (real case: patient states "ไม่มีอาการไอหรือน้ำมูก", app asked a
    # follow-up about nasal discharge character anyway).
    r"(?:[ก-๙a-zA-Z]{1,20}(?:หรือ|และ))?\s*$", re.IGNORECASE)

_FEATURE_PATTERNS: dict[str, str] = {
    "fever": r"ไข้(?!หวัด)|fever|\d{2}(?:\.\d)?\s*(?:°|องศา)",
    "pain": r"ปวด|เจ็บ|headache|myalgia|paracetamol|พารา",
    "runny": r"น้ำมูก|rhinorrh|post\s*-?nasal|จาม",
    "congestion": r"คัดจมูก|แน่นจมูก|จมูกตัน|nasal\s*congestion|stuffy",
    "cough": r"ไอ(?!โอดีน|น้ำ|ศกรีม)|cough",
    "sore_throat": r"เจ็บคอ|คอแดง|ระคาย(?:เคือง)?คอ|คออักเสบ|pharyngitis|ทอนซิล|tonsil|กลืน(?:เจ็บ|ลำบาก)|เยื่อบุคอ",
    "hoarse": r"เสียงแหบ|laryngitis|กล่องเสียงอักเสบ|สายเสียง",
    "ear": r"ปวดหู|หูอื้อ|น้ำหนวก|หูชั้นกลาง|otitis|\bAOM\b",
    # ฝาปิดกล่องเสียงอักเสบ -- คนละโรคกับ laryngitis (ภาวะฉุกเฉิน) และไม่ใช่ pharyngitis เช่นกัน
    "epiglottitis": r"epiglott|ฝาปิดกล่องเสียง|stridor|เสียงฮึด|น้ำลายไหลยืด|drooling|muffled\s*voice|เสียง(?:พูด)?อู้อี้",
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


# ─── Drug-allergy gateway (Phase2 opt4) ──────────────────────────────────────
# feedback หลัง deploy: โจทย์ระบุ "มีประวัติแพ้ยา Ibuprofen" ชัดเจนตั้งแต่เทิร์นแรก แต่พอผู้ใช้
# ถามต่อ ("อยากรู้ว่ายาภายในร้านมีอะไรบ้าง") ระบบกลับเสนอ Ibuprofen พร้อมขนาดยาเต็มๆ
# สาเหตุ: DOSE CATALOG สร้างจาก "อาการ" อย่างเดียว ไม่เคยรู้จัก "ยาที่ผู้ป่วยแพ้" -> โมเดลลอกตามคลังยา
# (ยิ่งเคสที่บอกว่า "ใช้พาราไม่ดีขึ้น" ระบบยิ่งดัน NSAIDs ขึ้นมา = ชี้ไปที่ยาที่แพ้พอดี)
# -> สกัดชื่อยาที่แพ้แบบ deterministic (ไม่พึ่งโมเดล) แล้วกัน 3 ชั้น:
#    (1) ตัดออกจากคลังยาใน Context  (2) บันทึกข้อห้ามให้ LLM  (3) ตรวจคำตอบก่อนส่งถึงผู้ใช้
#
# cross-reactivity ที่ใช้: แพ้ NSAID ตัวใดตัวหนึ่ง -> เลี่ยง NSAIDs ตัวอื่นทั้งกลุ่ม
# (COX-1 mediated hypersensitivity เป็น cross-reactive ระดับกลุ่ม -- ความรู้ระดับกลุ่มยา ไม่ใช่รายเคส)

# (ชื่อที่ใช้แสดง, regex ของชื่อยา/ตัวยาทั้งอังกฤษและไทย, แท็กกลุ่มสำหรับ cross-reactivity)
_ALLERGEN_LEXICON: tuple[tuple[str, str, str], ...] = (
    ("NSAIDs", r"nsaids?|เอ็นเสด|เอ็นเซด", "nsaid"),
    ("Ibuprofen", r"ibuprofen|brufen|บรูเฟน|ไอบู(?:โปรเฟน|โพรเฟน|พรอเฟน)?", "nsaid"),
    ("Naproxen", r"naproxen|นาโพรเซน|นาพรอกเซน", "nsaid"),
    ("Diclofenac", r"diclofenac|voltaren|ไดโคลฟีแนค|ไดโคลฟีแนก|โวลทาเรน", "nsaid"),
    ("Mefenamic acid", r"mefenamic|ponstan|พอนสแตน|เมเฟนามิก", "nsaid"),
    ("Piroxicam", r"piroxicam|พิร็อกซิแคม", "nsaid"),
    ("Celecoxib", r"celecoxib|เซเลค็อกซิบ", "nsaid"),
    ("Etoricoxib", r"etoricoxib|arcoxia|อีโทริค็อกซิบ", "nsaid"),
    ("Aspirin", r"aspirin|acetylsalicyl|แอสไพริน|แอสไพลิน", "nsaid"),
    ("Paracetamol", r"paracetamol|acetaminophen|พาราเซตามอล|พารา(?!ไซ)", "paracetamol"),
    ("Penicillin", r"penicill|เพน(?:น)?ิซิลลิน|เพนนิซิลิน", "beta_lactam"),
    ("Amoxicillin", r"amoxicillin|amoxycillin|อะม็อกซิ|อะมอกซี|อม็อกซิ", "beta_lactam"),
    ("Amoxicillin/clavulanate", r"augmentin|clavulan|ออกเมนติน", "beta_lactam"),
    ("Cephalosporin", r"cephalexin|cefdinir|cefpodoxime|cefixime|ceftriaxone|cefuroxime|"
                      r"cephalospor|เซฟาเลกซิน|เซฟไตรอะโซน", "beta_lactam"),
    ("Azithromycin", r"azithromycin|อะซิโทรมัยซิน", "macrolide"),
    ("Erythromycin", r"erythromycin|อีริโทรมัยซิน", "macrolide"),
    ("Clarithromycin", r"clarithromycin|คลาริโทรมัยซิน", "macrolide"),
    ("Clindamycin", r"clindamycin|คลินดามัยซิน", ""),
    ("Doxycycline", r"doxycycline|tetracyclin|ด็อกซีไซคลิน|เตตร้าไซคลิน", ""),
    ("Cotrimoxazole", r"cotrimoxazole|co-?trimoxazole|bactrim|sulfa|ซัลฟา|แบคทริม", "sulfa"),
    ("Chlorpheniramine", r"chlorpheniramine|คลอร์เฟนิรามีน", ""),
    ("Cetirizine", r"cetirizine|เซทิริซีน", ""),
    ("Loratadine", r"loratadine|ลอราทาดีน", ""),
    ("Dextromethorphan", r"dextromethorphan|เดกซ์โทรเมทอร์แฟน", ""),
    ("Pseudoephedrine", r"pseudoephedrine|ซูโดอีเฟดรีน", ""),
    ("Codeine", r"codeine|โคเดอีน", ""),
)
_ALLERGEN_LEXICON_MAP = tuple((name, pat) for name, pat, _t in _ALLERGEN_LEXICON)
_ALLERGEN_RES: tuple[tuple[str, "re.Pattern", str], ...] = tuple(
    (name, re.compile(pat, re.IGNORECASE), tag) for name, pat, tag in _ALLERGEN_LEXICON)
_NSAID_ALLERGEN_NAMES = tuple(name for name, _p, tag in _ALLERGEN_LEXICON if tag == "nsaid")
# ตัวยา NSAID ที่อาจอยู่ในผลิตภัณฑ์นอกหมวดยาแก้ปวด/ลดไข้ (เช่น ยาอมเจ็บคอที่มี flurbiprofen)
_NSAID_INGREDIENT_RE = re.compile(
    r"ibuprofen|flurbiprofen|ketoprofen|dexibuprofen|diclofenac|naproxen|mefenamic|piroxicam|"
    r"meloxicam|celecoxib|etoricoxib|indomethacin|aspirin|acetylsalicyl|salicylate", re.IGNORECASE)
# ตัวคั่นรายการยาที่แพ้ ("แพ้ยา Ibuprofen, paracetamol") -- ข้ามได้โดยยังอยู่ในรายการเดิม
_ALLERGY_SEP_RE = re.compile(
    r"^(?:[\s,;/·•\-]+|และ|หรือ|กับ|ทั้ง|ยา|ตัว|กลุ่ม|ชนิด|คือ|ได้แก่|:|\()+", re.IGNORECASE)
# จุดเริ่มของ "รายการยาที่แพ้" -- กัน "ภูมิแพ้" (ไม่ใช่การแพ้ยา) ด้วย lookbehind เหมือน _ALLERGY_WORD_RE เดิม
_ALLERGY_HEAD_RE = re.compile(
    r"(?<!ภูมิ)แพ้|allergic\s+to|allergy\s+to|drug\s+allerg\w*|ห้ามใช้", re.IGNORECASE)
# ปฏิเสธการแพ้ -> ข้ามจุดนั้นไป (ดูข้อความ 24 ตัวอักษรก่อนหน้า)
_ALLERGY_DENY_RE = re.compile(
    r"ไม่(?:มี|เคย|ได้)?(?:ประวัติ)?$|ปฏิเสธ|no\s+known|NKDA|ไม่ทราบ(?:ประวัติ)?$", re.IGNORECASE)


def extract_drug_allergies(text: str) -> list[str]:
    """ชื่อยาที่ผู้ใช้ระบุว่า "ผู้ป่วยแพ้" (deterministic, เรียงตามที่พบ) -- [] ถ้าไม่มี/ปฏิเสธการแพ้

    อ่านเฉพาะ "รายการยาที่ต่อจากคำว่าแพ้" เท่านั้น จึงไม่ไปจับยาที่อยู่ในประโยคอื่น
    (เช่น "แพ้ยา Ibuprofen ใช้พาราไม่ดีขึ้นเลย" -> ได้ Ibuprofen ตัวเดียว ไม่เอา Paracetamol)"""
    text = text or ""
    found: list[str] = []
    for m in _ALLERGY_HEAD_RE.finditer(text):
        if _ALLERGY_DENY_RE.search(text[max(0, m.start() - 24): m.start()]):
            continue
        pos = m.end()
        # ไล่อ่านทีละโทเคน: ตัวคั่น -> ชื่อยา -> ตัวคั่น -> ชื่อยา ... หยุดทันทีที่เจอคำที่ไม่ใช่ทั้งสองอย่าง
        while True:
            sep = _ALLERGY_SEP_RE.match(text[pos: pos + 48])
            if sep:
                pos += sep.end()
            seg = text[pos: pos + 48]
            hit = None
            for name, rx in _drug_name_res():
                mm = rx.match(seg)
                if mm and (hit is None or mm.end() > hit[1]):
                    hit = (name, mm.end())
            if hit is None:
                break
            if hit[0] not in found:
                found.append(hit[0])
            pos += hit[1]
    return found


def allergy_profile(text: str) -> dict:
    """{"drugs": [ชื่อยาที่แพ้], "tags": {แท็กกลุ่มสำหรับ cross-reactivity}} -- ว่างเปล่าถ้าไม่พบ"""
    drugs = extract_drug_allergies(text)
    tags = {tag for name, _rx, tag in _ALLERGEN_RES if tag and name in drugs}
    # ยาที่มาจากตาราง Dose ไม่มีแท็กกลุ่มในพจนานุกรม -> เติมแท็ก nsaid จากตัวยาในชื่อผลิตภัณฑ์
    if any(_NSAID_INGREDIENT_RE.search(n) for n in drugs):
        tags.add("nsaid")
    return {"drugs": drugs, "tags": tags}


# พจนานุกรมข้างบนเขียนด้วยมือ จึงครอบเฉพาะยาที่พบบ่อย + ยาปฏิชีวนะ (ซึ่งไม่ได้อยู่ในตาราง Dose)
# แต่ "ยาที่ระบบเสนอได้จริง" คือยาในตาราง Dose ทั้งหมด -> ต้องจับชื่อให้ครบทุกตัว ไม่งั้นประโยคอย่าง
# "ทาน Bromhexine มาแล้วไม่ดีขึ้น" จะหลุด แล้วระบบก็เสนอ Bromhexine ซ้ำ
# -> รวมสองแหล่ง: พจนานุกรม (ชื่อไทย/ชื่อพ้อง/ยาปฏิชีวนะ) + ชื่อยาจากตาราง Dose (data-driven)
_DRUG_NAME_RES: list[tuple[str, "re.Pattern"]] | None = None


def _key_pattern(key: str) -> str:
    """regex ของคีย์ชื่อยา -- ชื่ออังกฤษต้องไม่ไปโผล่กลางคำอื่น ส่วนภาษาไทยไม่มีขอบคำให้ยึด"""
    pat = re.escape(key.strip())
    if re.match(r"^[A-Za-z]", key):
        pat = r"(?<![A-Za-z])" + pat
    if re.search(r"[A-Za-z]$", key):
        pat += r"(?![A-Za-z])"
    return pat


def _drug_name_res() -> list[tuple[str, "re.Pattern"]]:
    """[(ชื่อที่ใช้แสดง, regex)] ของยาทุกตัวที่ระบบรู้จัก (lazy + cache ครั้งเดียวต่อโปรเซส)"""
    global _DRUG_NAME_RES
    if _DRUG_NAME_RES is not None:
        return _DRUG_NAME_RES
    pats: dict[str, list[str]] = {}
    for name, _rx, _t in _ALLERGEN_RES:
        pats.setdefault(name, []).append(dict(_ALLERGEN_LEXICON_MAP)[name])
    try:
        for d in load_formulary():
            keys = [k for k in (d.get("keys") or []) if len(k) >= 4]
            if keys:
                pats.setdefault(d["display"], []).extend(_key_pattern(k) for k in keys)
    except Exception as e:  # noqa: BLE001 -- ตาราง Dose โหลดไม่ได้ ก็ยังใช้พจนานุกรมได้
        print(f"[SYMPT] drug-name matcher: formulary skipped ({e})")
    # ชื่อยาวก่อน -> "Strepsils dry cough" ต้องชนะ "Strepsils" เมื่อข้อความมีทั้งคู่
    _DRUG_NAME_RES = sorted(
        ((n, re.compile("|".join(v), re.IGNORECASE)) for n, v in pats.items()),
        key=lambda x: -len(x[0]))
    return _DRUG_NAME_RES


def _name_res(names: list[str]) -> list[tuple[str, "re.Pattern"]]:
    """regex ของชื่อยาที่ระบุมา (ใช้ทั้งกับรายการแพ้ยาและรายการยาที่ใช้แล้วไม่ได้ผล)"""
    want = set(names)
    return [(n, rx) for n, rx in _drug_name_res() if n in want]


def _allergen_res(names: list[str]) -> list[tuple[str, "re.Pattern"]]:
    return _name_res(names)


def allergy_drug_block(d: dict, f: dict) -> str:
    """เหตุผลที่ "ห้ามแนะนำยาตัวนี้" จากประวัติแพ้ยาของเคส ("" = ไม่เกี่ยวกับการแพ้)"""
    alg = (f or {}).get("allergy") or {}
    names = alg.get("drugs") or []
    if not names:
        return ""
    # ตัวยาที่ใช้เทียบ: ชื่อในตาราง + ตัวยาสำคัญของผลิตภัณฑ์ (สูตรผสมที่ชื่อไม่ได้บอกตัวยา เช่น Decolgen/TIFFY)
    hay = " ".join([d.get("name") or "", brand_ingredient(d) or ""])
    for name, rx in _allergen_res(names):
        if rx.search(hay):
            return (f"**ผู้ป่วยรายนี้มีประวัติแพ้ยา {name}** -- ห้ามแนะนำยาตัวนี้/ผลิตภัณฑ์ที่มีตัวยานี้ "
                    f"ในทุกรูปแบบและทุกหัวข้อของคำตอบ")
    # ผลิตภัณฑ์นอกหมวดยาแก้ปวดก็มีตัวยา NSAID ได้ (เช่น ยาอมที่มี flurbiprofen) -> ต้องดูตัวยาด้วย
    # ไม่ใช่ดูแค่ธง nsaid ของหมวดยาแก้ปวด/ลดไข้
    if ("nsaid" in (alg.get("tags") or set())
            and (d.get("nsaid") or _NSAID_INGREDIENT_RE.search(hay))):
        who = ", ".join(n for n in names if n in _NSAID_ALLERGEN_NAMES) or "ยากลุ่ม NSAIDs"
        return (f"เลี่ยงทั้งกลุ่ม NSAIDs เพราะผู้ป่วยแพ้ {who} ซึ่งเป็น NSAID "
                f"(cross-reactivity ระดับกลุ่ม -- แพ้ตัวหนึ่งมีโอกาสแพ้ตัวอื่นในกลุ่มเดียวกัน)")
    return ""


# ─── ยาที่ "ใช้มาแล้วไม่ได้ผล" ต้องไม่ถูกเสนอซ้ำ (Phase2 opt4 -- รอบเก็บรายละเอียด) ──────
# feedback: เคส "แพ้ Ibuprofen + ใช้พาราไม่ดีขึ้นเลย" ระบบตัด NSAIDs ออกถูกแล้ว แต่ยังเหลือ
# Paracetamol เป็นตัวเลือกเดียวในคลังยา -> โมเดลก็เสนอพาราซ้ำ ทั้งที่โจทย์บอกว่าใช้แล้วไม่ดีขึ้น
# (เคสที่เขียนว่า "แพ้ Ibuprofen, paracetamol" ตรงๆ ไม่มีปัญหา เพราะเข้าเส้นทางประวัติแพ้ยา)
# -> สกัด "ยาที่ใช้มาแล้วอาการไม่ดีขึ้น" แบบ deterministic ด้วยพจนานุกรมชื่อยาชุดเดียวกับการแพ้ยา
#    แล้วตัดออกจากคลังยาเหมือนกัน (ผู้ป่วยได้ยาตัวนั้นมาแล้วและไม่ตอบสนอง = ไม่ใช่ทางเลือกของเทิร์นนี้)
_FAIL_RE = re.compile(
    r"ไม่ดีขึ้น|ไม่หาย|ไม่ลด|ไม่ได้ผล|ไม่ทุเลา|ไม่บรรเทา|ไม่ตอบสนอง|เอาไม่อยู่|อาการเดิม|"
    r"ยังมีไข้|ยังปวด|ยังเจ็บ|ยังไอ|ยังมีน้ำมูก|ยังคัดจมูก|ไข้ไม่ลด|อาการยังเหมือนเดิม|กลับมาเป็นอีก|"
    r"no\s+improvement|not\s+work|ineffective",
    re.IGNORECASE)
# คำที่บอกว่า "ยาได้ผล" -- ถ้ามีคั่นอยู่ระหว่างชื่อยากับคำว่าไม่ดีขึ้น แปลว่าอาการดีขึ้นแล้วแต่ยังเหลือบางอย่าง
# ("ใช้พาราแล้วดีขึ้นมาก แต่ยังเจ็บคออยู่") = ยาได้ผล ไม่ใช่ล้มเหลว -> ห้ามตัดยาตัวนั้นทิ้ง
_IMPROVED_RE = re.compile(
    r"(?<!ไม่)(?:ดีขึ้น|ทุเลา|หายดี|ได้ผล|บรรเทา|ลดลง|เบาลง|น้อยลง)|หายแล้ว|ค่อยยังชั่ว", re.IGNORECASE)


# ขอบประโยค -- ตัดกรอบค้นหาไม่ให้ข้ามไปประโยคอื่น (กันโทษยาผิดตัวเมื่อข้อความยาว)
_SENT_BREAK_RE = re.compile(r"[\n.;!?]|(?:แต่|ส่วน|ส่วนตัว|ทั้งนี้|อย่างไรก็ตาม)(?=[ก-๙])")


def _failed_after(seg: str) -> bool:
    """ข้อความ "หลังชื่อยา" บ่งว่าใช้แล้วไม่ได้ผลหรือไม่ (โดยไม่มีคำว่าดีขึ้นมาคั่นก่อน)

    กรอบกว้างพอให้ข้ามขนาดยา/วงเล็บตัวยาได้ ("ทาน Paracetamol 500 mg ทุก 6 ชม. มา 2 วันแล้วไม่ดีขึ้น")
    แต่ตัดที่ขอบประโยคเสมอ เพื่อไม่ให้ข้ามไปโทษยาที่พูดถึงในประโยคถัดไป
    """
    m = _FAIL_RE.search(seg)
    if not m:
        return False
    brk = _SENT_BREAK_RE.search(seg)
    if brk and brk.start() < m.start():
        return False
    return not _IMPROVED_RE.search(seg[:m.start()])


def _failed_before(seg: str) -> bool:
    """ข้อความ "ก่อนชื่อยา" บ่งว่าใช้แล้วไม่ได้ผลหรือไม่ (ดูคำที่ใกล้ชื่อยาที่สุด)"""
    last = None
    for last in _FAIL_RE.finditer(seg):
        pass
    return bool(last) and not _IMPROVED_RE.search(seg[last.end():])


def extract_failed_drugs(text: str) -> list[str]:
    """ชื่อยาที่ข้อความเคสบอกว่า "ใช้มาแล้วอาการไม่ดีขึ้น" (deterministic) -- [] ถ้าไม่มี

    จับทั้งสองทิศทาง: "ใช้พาราไม่ดีขึ้นเลย" (ยา -> คำว่าไม่ดีขึ้น) และ
    "อาการไม่ดีขึ้นหลังกิน paracetamol" (คำว่าไม่ดีขึ้น -> ยา)
    """
    text = text or ""
    found: list[str] = []
    for name, rx in _drug_name_res():
        for m in rx.finditer(text):
            if _failed_after(text[m.end(): m.end() + 90]) or _failed_before(text[max(0, m.start() - 40): m.start()]):
                if name not in found:
                    found.append(name)
                break
    return found


def _in_formulary(name: str) -> bool:
    """ชื่อยานี้มีอยู่ในตาราง Dose หรือไม่ (ขอบเขตของ gateway ยาบรรเทาอาการ)"""
    rx = next((r for n, r in _drug_name_res() if n == name), None)
    if rx is None:
        return False
    return any(rx.search(" ".join([d.get("name") or "", brand_ingredient(d) or ""]))
               for d in load_formulary())


def failed_drug_block(d: dict, f: dict) -> str:
    """เหตุผลที่ "ห้ามเสนอยาตัวนี้ซ้ำ" เพราะผู้ป่วยใช้มาแล้วไม่ได้ผล ("" = ไม่เกี่ยว)"""
    names = (f or {}).get("failed_drugs") or []
    if not names:
        return ""
    hay = " ".join([d.get("name") or "", brand_ingredient(d) or ""])
    for name, rx in _allergen_res(names):
        if rx.search(hay):
            return (f"**ผู้ป่วยใช้ {name} มาแล้วอาการไม่ดีขึ้น** -- ห้ามเสนอตัวนี้ซ้ำเป็นทางเลือกในเคสนี้ "
                    f"(รวมยาสูตรผสมที่มี {name} เป็นส่วนประกอบ) ให้เลือกยาที่กลไก/กลุ่มต่างออกไป")
    return ""


def prior_failed_note(f: dict) -> str:
    """บันทึกให้ LLM รู้ว่ายาตัวไหน "ใช้มาแล้วไม่ได้ผล" -- ใส่ทุกเทิร์นเหมือนบันทึกประวัติแพ้ยา"""
    names = (f or {}).get("failed_drugs") or []
    if not names:
        return ""
    return chr(10).join([
        "**ยาที่ผู้ป่วยใช้มาแล้วอาการไม่ดีขึ้น (ระบบสกัดจากข้อความเคส -- ใช้กับทุกเทิร์นของเคสนี้):** "
        + ", ".join(names),
        "- **ห้ามเสนอยาเหล่านี้ซ้ำเป็นทางเลือกการรักษา** ทั้งในหัวข้อยา ตารางสรุปขนาดยา และคำแนะนำดูแลตัวเอง "
        "(รวมยาสูตรผสม/ผลิตภัณฑ์ที่มีตัวยาเหล่านี้) -- ผู้ป่วยได้ยานี้มาแล้วและไม่ตอบสนอง",
        "- ถ้าต้องเอ่ยถึง ให้เขียนว่า \"ใช้มาแล้วอาการไม่ดีขึ้น จึงไม่เสนอซ้ำ\" แล้วอธิบายว่าจะเปลี่ยนไปใช้อะไรแทน",
        "- **ถ้าตัวเลือกที่เหลือในกลุ่มนั้นถูกตัดออกหมด** (เช่น ตัวที่เหลือเป็นยาที่ผู้ป่วยแพ้) "
        "ให้บอกตรงๆ ว่าไม่มีตัวเลือกในกลุ่มนี้ที่เหมาะกับผู้ป่วยรายนี้ แล้วเสนอการดูแลแบบไม่ใช้ยา "
        "+ แนะนำให้พบแพทย์เพื่อเลือกยาที่เหมาะสม -- **ห้ามเสนอยาที่แพ้หรือยาที่ใช้แล้วไม่ได้ผลกลับมาอีก**",
    ])


def allergy_gate_note(f: dict) -> str:
    """บันทึกข้อห้ามจากประวัติแพ้ยา -- ใส่ใน clinical_notes ทุกเทิร์น (รวมคำถามต่อเนื่อง) กันข้อมูลหล่นกลางแชท"""
    alg = (f or {}).get("allergy") or {}
    names = alg.get("drugs") or []
    if not names:
        return ""
    lines = ["**ข้อห้ามด้านการแพ้ยาของผู้ป่วยรายนี้ (ระบบสกัดจากข้อความเคส -- ใช้กับทุกเทิร์นของเคสนี้):** "
             + ", ".join(names),
             "- **ห้ามเสนอ/ห้ามระบุขนาดยาของยาเหล่านี้เป็นทางเลือกการรักษาเด็ดขาด** ทั้งในหัวข้อยา "
             "ตารางสรุปขนาดยา และคำแนะนำดูแลตัวเอง -- รวมถึงยาสูตรผสม/ผลิตภัณฑ์ที่มีตัวยาเหล่านี้เป็นส่วนประกอบ",
             "- ถ้าจำเป็นต้องเอ่ยถึง ให้เขียนในเชิง \"หลีกเลี่ยง/ห้ามใช้ในผู้ป่วยรายนี้ เพราะมีประวัติแพ้\" เท่านั้น"]
    if "nsaid" in (alg.get("tags") or set()):
        lines.append("- ผู้ป่วยแพ้ยาในกลุ่ม NSAIDs -> **หลีกเลี่ยง NSAIDs ตัวอื่นทั้งกลุ่มด้วย** "
                     "(cross-reactivity ระดับกลุ่ม) และอธิบายเหตุผลข้อนี้ให้เภสัชกรเห็นชัดในคำตอบ "
                     "-- ยาแก้ปวด/ลดไข้ที่ยังใช้ได้ให้เลือกจาก DOSE CATALOG เท่านั้น")
    if "beta_lactam" in (alg.get("tags") or set()):
        lines.append("- ผู้ป่วยแพ้ยากลุ่ม beta-lactam -> เลือกยาปฏิชีวนะทางเลือกตามชนิดการแพ้จากตารางใน Context "
                     "ห้ามเสนอ first-line ที่เป็น beta-lactam เป็นยาของผู้ป่วยรายนี้")
    lines.append("- **ก่อนจบคำตอบ ให้ไล่ตรวจชื่อยาทุกตัวที่เขียนไปว่าไม่มีตัวใดอยู่ในรายการแพ้ข้างต้น** "
                 "(รวมชื่อการค้า/สูตรผสม) ถ้ามีให้ตัดออกแล้วเสนอทางเลือกอื่นแทน")
    return chr(10).join(lines)


# ─── Answer audit: ยาที่แพ้ต้องไม่หลุดออกไปเป็น "คำแนะนำ" (backstop ชั้นสุดท้าย) ──────
# ทำงานระดับ "บรรทัด" จึงใช้ได้ทั้งโหมดสตรีมและไม่สตรีม
_AVOID_CUE_RE = re.compile(
    r"หลีกเลี่ยง|ห้าม|ไม่แนะนำ|ไม่ควร|ไม่เหมาะ|งด|เลี่ยง|แพ้|ข้อห้าม|ตัดออก|contraindicat|avoid",
    re.IGNORECASE)
_DRUG_ITEM_RE = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s*)(.+)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(?!\s*[-:]+\s*\|)(.+)\|\s*$")
# "<ชื่อผลิตภัณฑ์> (ตัวยา: X)" -- ใช้ดูตัวยาสำคัญที่วงเล็บหัวบรรทัดระบุไว้ (ไม่ไปจับชื่อยากลางประโยค)
_PROD_INGREDIENT_RE = re.compile(r"[^(\n]{0,40}\(([^)\n]{0,60})\)")


def _line_names_allergen(line: str, pairs: list[tuple[str, "re.Pattern"]]) -> str | None:
    for name, rx in pairs:
        if rx.search(line):
            return name
    return None


def audit_allergy_lines(text: str, f: dict) -> str:
    """ตรวจคำตอบทีละบรรทัด: บรรทัดที่ "เสนอ" ยาที่ผู้ป่วยแพ้ (ไม่มีคำเตือนกำกับ) -> แทนด้วยบรรทัดเตือน

    ไม่แตะบรรทัดที่พูดถึงยานั้นในเชิงหลีกเลี่ยง/ห้ามใช้อยู่แล้ว (เช่น "หลีกเลี่ยง Ibuprofen เพราะแพ้")"""
    alg = (f or {}).get("allergy") or {}
    names = alg.get("drugs") or []
    if not names or not text:
        return text
    pairs = _allergen_res(names)
    # แพ้ NSAID ตัวหนึ่ง -> ตัวอื่นในกลุ่มก็ต้องไม่หลุดเป็นคำแนะนำ (ให้ตรงกับที่ DOSE CATALOG ตัดไปแล้ว)
    # -- ใช้เฉพาะกลุ่ม NSAIDs เท่านั้น ส่วน beta-lactam ปล่อยให้กฎเรื่องชนิดการแพ้ (type 1 / non-type 1)
    #    ตัดสินตามเดิม เพราะ cephalosporin ยังเป็นทางเลือกที่ถูกต้องในการแพ้แบบไม่รุนแรง
    cross = ([(n, rx) for n, rx, tag in _ALLERGEN_RES if tag == "nsaid" and n not in names]
             if "nsaid" in (alg.get("tags") or set()) else [])
    out: list[str] = []
    skip_deeper: int | None = None
    for line in text.split(chr(10)):
        item = _DRUG_ITEM_RE.match(line)
        row = _TABLE_ROW_RE.match(line) if not item else None
        # บรรทัดย่อย (เช่น "ขนาด: 200-400 mg") ที่ห้อยอยู่ใต้ยาที่เพิ่งถูกตัด -> ตัดตามไปด้วย
        # ไม่งั้นขนาดยาของยาที่แพ้จะค้างอยู่ในคำตอบแบบไม่มีหัวเรื่อง
        if skip_deeper is not None:
            if item and len(item.group(1)) - len(item.group(1).lstrip()) > skip_deeper:
                continue
            skip_deeper = None
        if not item and not row:
            out.append(line)
            continue
        body = item.group(2) if item else row.group(1)
        # ต้องเป็น "ชื่อยาที่ขึ้นต้นบรรทัด" เท่านั้น (ไม่ใช่ชื่อที่เอ่ยผ่านกลางประโยค เช่น
        # "Paracetamol เป็นทางเลือกแทน NSAIDs" ซึ่งเป็นคำแนะนำที่ถูกต้องอยู่แล้ว)
        head = body.lstrip(" *_~`[")
        hit = next((n for n, rx in pairs if rx.match(head)), None)
        why = f"มีประวัติแพ้ยา {hit}" if hit else ""
        # ผลิตภัณฑ์ที่ระบุตัวยา NSAID ไว้ในวงเล็บหัวบรรทัด (เช่น "Strepsils Maxpro (ตัวยา: Flurbiprofen)")
        prod = _PROD_INGREDIENT_RE.match(head) if cross else None
        if not hit and (next((n for n, rx in cross if rx.match(head)), None)
                        or (prod and _NSAID_INGREDIENT_RE.search(prod.group(1)))):
            hit = "NSAIDs"
            why = "แพ้ยาในกลุ่ม NSAIDs (cross-reactivity ระดับกลุ่ม)"
        if not hit or _AVOID_CUE_RE.search(body):
            out.append(line)
            continue
        warn = f"**ห้ามใช้ในผู้ป่วยรายนี้ -- {why}** (ระบบตัดออกอัตโนมัติเพื่อความปลอดภัย)"
        if item:
            drug = re.split(r"\s*(?::|\||--|\(|,)", head[:40])[0].strip(" *_")
            out.append(f"{item.group(1)}~~{drug}~~ {warn}")
            skip_deeper = len(item.group(1)) - len(item.group(1).lstrip())
        else:
            cells = row.group(1).split("|")
            drug = cells[0].strip() or hit
            out.append("| " + " | ".join([drug] + [warn if i == len(cells) - 1 else "-"
                                                   for i in range(1, len(cells))]) + " |")
    return chr(10).join(out)


def mentions_allergen(text: str, f: dict) -> bool:
    """(streaming) เคสที่มีประวัติแพ้ยา -> กันบรรทัดที่ยังไม่จบไว้ก่อนเสมอ

    ไม่เช็คเฉพาะ "บรรทัดที่เห็นชื่อยาแล้ว" เพราะชื่อยาอาจถูกหั่นคนละ chunk ("- Ibu" / "profen: 400 mg")
    ซึ่งจะหลุดออกไปก่อนที่ audit จะได้ตรวจ -- เรื่องความปลอดภัยจึงกันทั้งบรรทัดไปเลย
    (ต้นทุน: รอจบบรรทัดเท่านั้น และเกิดเฉพาะเคสที่มีประวัติแพ้ยาจริง)"""
    return bool(text and ((f or {}).get("allergy") or {}).get("drugs"))


def _tri(text: str, pattern: str) -> bool | None:
    """True = มีอาการ, False = ระบุว่าไม่มี, None = ไม่ได้กล่าวถึง"""
    pos = neg = False
    for m in re.finditer(pattern, text, re.IGNORECASE):
        # 30 chars (not 14) so a negation word governing a "X หรือ Y" list
        # ("ไม่มีอาการไอหรือน้ำมูก") stays inside the window even for the
        # second item — 14 chars was cutting the leading "ไม" off "ไม่มี"
        # for this exact real phrase, in a language with no spaces between
        # words to shrink the window naturally.
        pre = text[max(0, m.start() - 30): m.start()]
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
    # ยาที่ผู้ป่วยแจ้งว่าแพ้ -- ต้องติดไปกับ features ทุกเทิร์น ไม่ใช่รู้เฉพาะเทิร์นที่ผู้ใช้พิมพ์คำว่า "แพ้"
    f["allergy"] = allergy_profile(text)
    # ยาที่ใช้มาแล้วอาการไม่ดีขึ้น -> ต้องไม่ถูกเสนอซ้ำ (ผูกกับเคส ไม่ใช่กับเทิร์นที่พิมพ์)
    # ตัดยาที่อยู่ในรายการ "แพ้" ออก: "แพ้ยา Ibuprofen ใช้พาราไม่ดีขึ้นเลย" -> Ibuprofen คือยาที่แพ้
    # ไม่ใช่ยาที่ใช้แล้วไม่ได้ผล (ถูกตัดด้วยเหตุผลการแพ้อยู่แล้ว และเหตุผลที่แจ้ง LLM ต้องตรงความจริง)
    # ...และจำกัดไว้เฉพาะยาที่อยู่ใน "ตาราง Dose" (ขอบเขตของ gateway นี้คือยาบรรเทาอาการ)
    # ยาปฏิชีวนะที่เคยได้มาก่อน มีกฎของตัวเองอยู่แล้ว (prior_antibiotic_note + ตารางใน Guideline ที่บอกให้
    # เปลี่ยนไป amoxicillin/clavulanate) -- ถ้าเอากฎ "ห้ามเสนอซ้ำ" มาทับ จะไปห้ามสูตรผสมที่ถูกต้องด้วย
    _allergic = set(f["allergy"]["drugs"])
    f["failed_drugs"] = [x for x in extract_failed_drugs(text)
                         if x not in _allergic and _in_formulary(x)]
    # สิ่งตรวจพบที่บ่งชี้ GABHS pharyngitis (ใช้ตัดสินว่าเคสนี้อยู่ในขอบเขตของเกณฑ์ Centor หรือไม่)
    f["tonsil_finding"] = _finding(text, _TONSIL_PATTERN, _TONSIL_NORMAL_RE)
    f["lymph_finding"] = _finding(text, _LYMPH_PATTERN, _LYMPH_NORMAL_RE)
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
    """คืน {class: (status, reason)} -- status: fit | partial (กลุ่มใช้ได้แต่บางตัวในกลุ่มไม่เหมาะ) | conditional | avoid"""
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
            # ประวัติแพ้ NSAID ต้องมาก่อน "พาราไม่ได้ผล" เสมอ -- ไม่งั้นระบบจะชี้ไปที่กลุ่มยาที่ผู้ป่วยแพ้พอดี
            # (feedback opt4 เคส 2: "แพ้ Ibuprofen + ใช้พาราไม่ดีขึ้น" -> เดิมดัน NSAIDs ขึ้นมาเป็นทางเลือก)
            why += (" -- ใช้ Paracetamol แล้วไม่ดีขึ้น แต่**ผู้ป่วยมีประวัติแพ้ยากลุ่ม NSAIDs จึงห้ามใช้ NSAIDs แทน**: "
                    "ให้เลี่ยงทั้งกลุ่มแล้วเสนอการดูแลแบบไม่ใช้ยา/ส่งต่อแพทย์"
                    if "nsaid" in ((f.get("allergy") or {}).get("tags") or set()) else
                    " -- ใช้ Paracetamol แล้วไม่ดีขึ้น: พิจารณายาแก้ปวดกลุ่ม NSAIDs เป็นทางเลือก (ตรวจข้อห้าม/อายุ/น้ำหนัก)")
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
            # น้ำมูกข้นเหนียว: ตัด "เฉพาะรุ่นที่ 1" ออก (ลดสารคัดหลั่งแรงจนน้ำมูกแห้งติดโพรงจมูก)
            # ส่วนรุ่นที่ 2 ยังใช้ลดน้ำมูกได้ -> ห้ามเหมารวมทั้งกลุ่มว่า "ไม่เหมาะกับเคสนี้"
            g1 = class_drug_names("antihistamine", first_gen=True)
            g1_txt = f" ในตารางมี {len(g1)} ตัว ได้แก่ {', '.join(g1)}" if g1 else ""
            plan["antihistamine"] = ("partial", "น้ำมูกข้นเหนียว -- **ยาแก้แพ้รุ่นที่ 1 ไม่เหมาะกับเคสนี้ทุกตัว**"
                                     + g1_txt + " (**ถ้าจะกล่าวถึงในคำตอบ ต้องเอ่ยชื่อให้ครบทุกตัว ห้ามยกมาแค่ตัวเดียว**) "
                                     "เพราะลดสารคัดหลั่งแรงจนน้ำมูกข้นเหนียวแห้งติดในโพรงจมูก "
                                     "(รุ่นที่ 1 เหมาะกับเคสน้ำมูกใสเหลว/ไหลเป็นสายไม่หยุด); "
                                     "**รุ่นที่ 2 (ง่วงน้อย) ยังใช้ลดน้ำมูกในเคสนี้ได้** โดยแนะนำให้ใช้ควบคู่กับการล้างจมูกด้วยน้ำเกลือ "
                                     "-- ในรายการด้านล่างระบบตัดรุ่นที่ 1 ออกให้แล้ว ให้เสนอเฉพาะตัวที่แสดงไว้")
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


def class_drug_names(cls: str, *, first_gen: bool | None = None) -> list[str]:
    """ชื่อยาทุกตัวของกลุ่มนั้นในตาราง Dose (เรียงตามหน้า) -- ใช้เขียนข้อความ "ได้แก่ ..." ให้ครบทุกตัวจริง

    ไม่ hardcode ชื่อยาไว้ในข้อความ: ถ้าตาราง Dose เพิ่ม/ลดยา ข้อความก็เปลี่ยนตามเอง
    (feedback อาจารย์: ยก "เช่น <ยา 1 ตัว>" ทั้งที่มีหลายตัวเข้าข่าย -> ผู้อ่าน bias ไปที่ตัวที่ยกมา)"""
    out: list[str] = []
    for d in load_formulary():
        if cls not in d["classes"]:
            continue
        if first_gen is not None and bool(d.get("first_gen")) != first_gen:
            continue
        if d["display"] not in out:
            out.append(d["display"])
    return out


def class_drug_block(d: dict, cls: str, f: dict) -> str:
    """ข้อห้ามระดับ 'ตัวยาในกลุ่ม' (กลุ่มยังใช้ได้ แต่ยาตัวนี้ไม่เหมาะ) -> เหตุผล, "" = ใช้ได้
    ใช้กับกรณีที่เหมารวมทั้งกลุ่มไม่ได้ เช่น น้ำมูกข้นเหนียวห้ามเฉพาะ antihistamine รุ่นที่ 1
    ส่วนรุ่นที่ 2 ยังใช้ลดน้ำมูกได้ (feedback อาจารย์ Phase2 opt3)"""
    if (cls == "antihistamine" and d.get("first_gen")
            and f.get("runny_char") == "thick" and not f.get("allergic")):
        return ("ยาแก้แพ้รุ่นที่ 1 ลดสารคัดหลั่งแรง ทำให้น้ำมูกข้นเหนียวแห้งติดในโพรงจมูก "
                "-- เหมาะกับน้ำมูกใสเหลว/ไหลเป็นสาย ไม่ใช่เคสน้ำมูกข้นเหนียวแบบนี้")
    return ""


def practical_options(f: dict) -> list[str]:
    """ทางเลือกที่ไม่ใช้ยา/พฤติกรรมปฏิบัติจริงหน้าร้าน (Expert practice)"""
    out: list[str] = []
    if f.get("runny_char") == "thick" or f.get("sinus") or f.get("congestion"):
        out.append("ล้างจมูกด้วยน้ำเกลือ (Normal saline nasal irrigation) -- ช่วยระบายน้ำมูกข้นเหนียว/ไซนัส "
                   "ใช้ควบคู่กับยาลดน้ำมูก (ถ้าจำเป็น) และเหมาะกว่ายาแก้แพ้รุ่นที่ 1 ในเคสน้ำมูกข้น")
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
    """ตรวจอายุ/ขนาดเด็ก/รูปแบบยา จากตารางเอง (data-driven)"""
    if d.get("route_note"):
        return False, d["route_note"]
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
        "วิธีใช้: ใช้เฉพาะกลุ่มที่ 'เหมาะกับเคสนี้/ใช้ได้เฉพาะบางตัวในกลุ่ม/ขึ้นกับข้อมูลที่ยังไม่ทราบ' -- เสนอ **ตัวเลือกให้ครบทุกตัวที่อยู่ในกลุ่มนั้นและเหมาะกับผู้ป่วยรายนี้** "
        "(ไม่จำกัด 2-4 ตัว แต่ต้องถูกต้อง) เรียงตามลำดับ: ยาหลัก -> ทางเลือกในกลุ่ม -> ผลิตภัณฑ์/ทางเลือกเสริม; เรียกหมวดตาม [รูปแบบ] ของยา "
        "(ยาพ่นคอ/ยาอม/ยากลั้วคอ ห้ามปนหมวด) และผลิตภัณฑ์ต้องมีตัวยา/สารสำคัญกำกับ + ขนาดยา + [Ref: Dose, หน้า N] (N = Page ท้ายชื่อยา) "
        "-- ขนาดยาด้านล่างเป็นข้อความตัดตอนตรงจากตาราง (ตัวเลขตรงต้นฉบับ)",
        f"{_DEPTH_GUIDE[depth]} (เหตุผล: {depth_why})",
    ]
    used: list[dict] = []
    avoided: list[str] = []
    # ยาที่อยู่ได้หลายหมวดพร้อมกัน (Terco-D = กดไอ+ขับเสมหะ, Strepsils dry cough = ยาอมเจ็บคอ+ไอแห้ง)
    # เคยถูกลงรายการซ้ำทุกหมวด แล้วโมเดลก็เสนอซ้ำในคำตอบตามไปด้วย (feedback: "ยาพ่น/ยาอม มีซ้ำ")
    # -> หมวดหลังอ้างถึงหมวดแรกแทนการลงรายละเอียดซ้ำ
    listed_in: dict[str, str] = {}
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
        head = ("เหมาะกับเคสนี้" if status == "fit" else
                "ใช้ได้เฉพาะบางตัวในกลุ่ม (ระบบคัดตัวที่ไม่เหมาะออกให้แล้ว)" if status == "partial" else
                "ขึ้นกับข้อมูลที่ยังไม่ทราบ")
        lines.append(f"\n■ {CLASS_LABELS[cls]} -- {head}: {reason}")
        not_ok: list[tuple[str, str]] = []
        rare: list[str] = []
        cur_tier = None
        n_listed = 0
        # ยาแก้แพ้รุ่นที่ 1 (ง่วง/ลดสารคัดหลั่งแรง) ไว้ท้ายกลุ่มยาหลัก -> รุ่นที่ 2 ขึ้นก่อนตาม feedback อาจารย์
        for d in sorted(members, key=lambda x: (_TIER_ORDER[_tier(x, cls)], 1 if x.get("first_gen") else 0)):
            ok, why = _eligible(d, f)
            if not ok:
                not_ok.append((d["display"], why))
                continue
            # ประวัติแพ้ยา + ยาที่ใช้แล้วไม่ได้ผล มาก่อนข้อพิจารณาอื่นเสมอ
            # (ห้ามหลุดเป็นตัวเลือก แม้กลุ่มยานั้นจะเหมาะกับอาการ)
            blocked = allergy_drug_block(d, f) or failed_drug_block(d, f) or class_drug_block(d, cls, f)
            if blocked:
                not_ok.append((d["display"], blocked))
                continue
            tier = _tier(d, cls)
            if tier == "rare" and not full:
                rare.append(d["display"])
                continue
            if d["name"] in listed_in:
                lines.append(f"  - {d['display']} (ยาตัวเดียวกับที่อยู่ในหมวด \"{listed_in[d['name']]}\" ข้างบน "
                             f"-- ใช้ครอบทั้งสองอาการได้ แต่ **เสนอได้ครั้งเดียว ห้ามเขียนซ้ำสองหมวด**)")
                continue
            if tier != cur_tier and tier in _TIER_LABELS:
                lines.append(f"  [{_TIER_LABELS[tier]}]")
                cur_tier = tier
            lines.append(_drug_line(d, cls, f, full=full))
            listed_in[d["name"]] = CLASS_LABELS[cls]
            n_listed += 1
            if d not in used:
                used.append(d)
        # ประวัติแพ้ยาอาจตัดยาในกลุ่มออกจนหมด (เช่น แพ้ทั้ง Paracetamol และ NSAIDs) -> ต้องบอกให้ชัด
        # ไม่งั้นโมเดลจะไปหยิบชื่อยาจากที่อื่นมาเติมเอง หรือเงียบไปทั้งหัวข้อ
        if n_listed == 0 and not_ok:
            lines.append("  **ไม่มียาในกลุ่มนี้ที่เหมาะกับผู้ป่วยรายนี้เลย** (ถูกตัดออกด้วยประวัติแพ้ยา/ใช้แล้วไม่ได้ผล) -- ห้ามเสนอยาในกลุ่มนี้ "
                         "ให้เขียนว่าเลี่ยงทั้งกลุ่มพร้อมเหตุผล แล้วเสนอทางเลือกที่ไม่ใช้ยา "
                         "และแนะนำให้ปรึกษาแพทย์เพื่อเลือกยาที่ปลอดภัยแทน (ห้ามหยิบชื่อยานอก DOSE CATALOG มาเติมเอง)")
        if rare:
            lines.append("  (มีในตารางแต่ไม่ใช่ตัวเลือกทั่วไปสำหรับอาการ URI -- ไม่ต้องแนะนำ เว้นแต่ผู้ใช้ขอดูทั้งหมด: "
                         + ", ".join(rare) + ")")
        if not_ok:
            # รวมยาที่ "เหตุผลเดียวกัน" ไว้บรรทัดเดียว -> โมเดลเห็นเป็นชุดและคัดลอกชื่อไปครบ
            # (เดิมไล่ทีละตัวพร้อมเหตุผลซ้ำๆ -> โมเดลย่อเหลือ "เช่น <ตัวแรก>" ตัวเดียว = bias)
            groups: dict[str, list[str]] = {}
            for name, why in not_ok:
                groups.setdefault(why, []).append(name)
            parts = [f"{', '.join(names)} -- {why}" for why, names in groups.items()]
            lines.append("  (มีในตารางแต่ไม่แนะนำในเคสนี้ -- **ถ้าจะกล่าวถึงในคำตอบ ต้องเอ่ยชื่อให้ครบทุกตัวของแต่ละเหตุผล "
                         "ห้ามยกมาแค่ตัวเดียว**; เหตุผลจากข้อมูลในตาราง: " + "; ".join(parts) + ")")
    if avoided:
        lines.append("\n■ ไม่เหมาะกับเคสนี้ (ห้ามแนะนำเป็นการรักษา) -- **บังคับ: ถ้าคำตอบนี้ลงรายชื่อยา "
                     "ต้องปิดท้ายหัวข้อ 3b ด้วยหัวข้อย่อย \"กลุ่มที่ไม่เหมาะกับเคสนี้\" ที่ไล่ครบทุกหมวดด้านล่าง "
                     "หมวดละ 1 บรรทัด พร้อมชื่อยาครบทุกตัว + เหตุผลสั้นๆ ห้ามข้ามหมวดใด และ "
                     "ห้ามนับโน้ตในวงเล็บใต้กลุ่มยาที่แนะนำว่าทำข้อนี้แล้ว**:")
        lines += avoided
    prac = practical_options(f)
    if prac:
        lines.append("\n■ ทางเลือกที่ไม่ใช้ยา / แนวปฏิบัติจริงหน้าร้าน (Expert practice -- ต้องใส่ในคำตอบเมื่อเกี่ยวข้อง, "
                     "ไม่ใช่ยาในตาราง Dose จึงห้ามอ้าง [Ref: Dose]):")
        lines += [f"  - {p}" for p in prac]
    return "\n".join(lines), used


# ─── "กลุ่มที่ไม่เหมาะกับเคสนี้" -- backstop แบบ deterministic ────────────────────────
# feedback อาจารย์: หัวข้อนี้ "หายไป" จากคำตอบ (วัดแล้วหาย 3 ใน 4 ครั้ง) เพราะกฎใน prompt เดิม
# เป็นเงื่อนไข ("ถ้าจะกล่าวถึง") + พอย้ายยาแก้แพ้ไปเป็น partial โมเดลก็ถือว่าโน้ตในวงเล็บพอแล้ว
# -> ถ้าคำตอบลงรายชื่อยาแล้วแต่ยังไม่พูดถึงกลุ่มที่ห้าม ให้ระบบเติมหัวข้อนี้ให้เอง
_DOSE_REF_RE = re.compile(r"\[Ref:\s*Dose[^\]]*?หน้า\s*\d+")
_AVOID_ANCHORS = ("หากต้องการดูตัวเลือกอื่นในกลุ่ม", "หากต้องการทราบว่ายาที่มีในร้าน",
                  "สรุปตารางขนาดยา", "**4.", "4. คำแนะนำดูแลตัวเอง")


def avoided_groups(plan: dict[str, tuple[str, str]]) -> list[tuple[str, list[str], str]]:
    """[(ชื่อหมวด, [ชื่อยาครบทุกตัว], เหตุผล)] ของกลุ่มที่ gateway ตัดสินว่า 'ไม่เหมาะกับเคสนี้'"""
    out: list[tuple[str, list[str], str]] = []
    formulary = load_formulary()
    for cls in CLASS_ORDER:
        if cls not in plan or plan[cls][0] != "avoid":
            continue
        names = list(dict.fromkeys(d["display"] for d in formulary if cls in d["classes"]))
        if names:
            out.append((CLASS_LABELS[cls], names, plan[cls][1]))
    return out


def missing_avoided_block(text: str, plan: dict[str, tuple[str, str]]) -> str:
    """ข้อความหัวข้อ 'กลุ่มที่ไม่เหมาะกับเคสนี้' เฉพาะหมวดที่คำตอบยังไม่ได้พูดถึง ("" = ไม่ต้องเติม)

    เติมเฉพาะคำตอบที่ลงรายชื่อยาจริง (มี [Ref: Dose, หน้า N] ตั้งแต่ 2 จุด) -- คำตอบแรกที่บอกแค่ชื่อ
    กลุ่มยายังไม่ต้องมีหัวข้อนี้ (คงสไตล์เดิมของระบบ) และถ้าโมเดลเขียนครบเองแล้วจะไม่แตะ
    """
    groups = avoided_groups(plan or {})
    if not text or not groups or len(_DOSE_REF_RE.findall(text)) < 2:
        return ""
    low = text.lower()

    def named(names: list[str]) -> bool:
        return any(re.split(r"[\s/(]", n.strip())[0].lower() in low for n in names if len(n) >= 4)

    missing = [g for g in groups if not named(g[1])]
    if not missing:
        return ""
    rows = ["**กลุ่มที่ไม่เหมาะกับเคสนี้ (ไม่แนะนำให้ใช้):**"]
    for label, names, reason in missing:
        shown = ", ".join(n if len(n) <= 70 else n[:67].rstrip() + "…" for n in names)
        why = re.split(r"\s--\s|;\s", (reason or "").strip())[0]
        rows.append(f"- **{label}:** {shown}" + (f" -- {why}" if why else ""))
    return "\n".join(rows) + "\n"


def ensure_avoided_section(text: str, plan: dict[str, tuple[str, str]]) -> str:
    """(คำตอบแบบไม่สตรีม) แทรกหัวข้อที่ขาดไว้ก่อนบรรทัดเชิญถามต่อ/ตารางสรุป/หัวข้อ 4"""
    block = missing_avoided_block(text, plan)
    if not block:
        return text
    for anchor in _AVOID_ANCHORS:
        i = text.find(anchor)
        if i > 0:
            j = text.rfind("\n", 0, i)
            cut = j + 1 if j >= 0 else i
            return text[:cut].rstrip("\n") + "\n\n" + block + "\n" + text[cut:]
    return text.rstrip("\n") + "\n\n" + block


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
        in_plan = [k for k in d["classes"] if k in plan]
        statuses = [plan[k][0] for k in in_plan]
        if statuses and all(s == "avoid" for s in statuses):
            continue
        # ยาที่ถูกกันระดับตัวยาในทุกกลุ่มที่เกี่ยวข้อง (เช่น CPM ในเคสน้ำมูกข้นเหนียว) -> ไม่ต้องแนบ chunk
        if in_plan and all(class_drug_block(d, k, f) for k in in_plan):
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


# ─── "ขอดูตัวเลือกยา" != "บรรยายเคสใหม่" (Phase2 opt4) ───────────────────────
# feedback: แค่เปลี่ยนสำนวนคำถามต่อเนื่องเล็กน้อย บริบทเคสเดิมก็หายไปทั้งก้อน
# สาเหตุ: ชื่อกลุ่มยามีคำอาการอยู่ในตัว ("ยาแก้ปวดลดไข้" -> ระบบอ่านว่าเจอ "ปวด" + "ไข้")
# -> ข้อความอย่าง "ขอตัวเลือกยาแก้ปวดลดไข้ทั้งหมดที่มีในร้าน" ถูกนับเป็น "คำบรรยายเคสของผู้ป่วยรายใหม่"
#    แล้วระบบก็ทิ้งเคสเดิม (อายุ/ประวัติแพ้ยา) ไปซักประวัติใหม่ตั้งแต่ต้น
# -> ถ้าอาการทั้งหมดในข้อความอยู่ใน "ชื่อกลุ่มยา" เท่านั้น และไม่มีคำบ่งชี้ตัวผู้ป่วย = เป็นคำขอดูยา ไม่ใช่เคสใหม่
_DRUG_CLASS_PHRASE_RE = re.compile(
    r"ยา(?:แก้|ลด|บรรเทา(?:อาการ)?|ระงับ|ละลาย|ขับ|พ่น|อม|กลั้ว|ต้าน|หยอด)?"
    r"(?:ปวด(?:หัว|ศีรษะ|เมื่อย)?|ไข้|ไอ|แพ้|น้ำมูก|คัดจมูก|เจ็บคอ|เสมหะ|หวัด|อักเสบ|คอ|จมูก|ปฏิชีวนะ)"
    r"(?:[ /\-,]{0,2}(?:แก้|ลด|บรรเทา(?:อาการ)?|ละลาย|ขับ|ระงับ)"
    r"(?:ปวด(?:หัว|ศีรษะ|เมื่อย)?|ไข้|ไอ|แพ้|น้ำมูก|คัดจมูก|เจ็บคอ|เสมหะ|หวัด|อักเสบ))*")
_DRUG_REQUEST_CUE_RE = re.compile(
    r"ขอ|อยาก(?:รู้|ได้|ทราบ)|แนะนำ|มีอะไรบ้าง|มียาอะไร|ตัวไหน|ตัวเลือก|ทางเลือก|ในร้าน|ทั้งหมด|"
    r"ชื่อยา|ขนาดยา|ดูยา|list|option", re.IGNORECASE)


def is_drug_request_only(text: str) -> bool:
    """ข้อความเป็น "คำขอดูตัวเลือกยา" ล้วนๆ (ไม่ใช่การบรรยายเคสผู้ป่วยรายใหม่)

    เงื่อนไขครบทั้งสามข้อ: ไม่มีคำบ่งชี้ตัวผู้ป่วย + มีคำขอดูยา + อาการที่ตรวจพบมาจากชื่อกลุ่มยาเท่านั้น"""
    text = text or ""
    if _PATIENT_RE.search(text) or not _DRUG_REQUEST_CUE_RE.search(text):
        return False
    if not has_uri_symptom(extract_case_features(text)):
        return False        # ไม่มีอาการอยู่แล้ว -> ไม่ใช่เคสตั้งแต่ต้น ไม่ต้องใช้ตัวช่วยนี้
    masked = _DRUG_CLASS_PHRASE_RE.sub(" ", text)
    return not has_uri_symptom(extract_case_features(masked))


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
        minimal_missing.append("อายุ -- เพื่อเลือก Guideline ให้ตรงกลุ่มอายุ ตรวจข้อห้ามใช้ตามอายุ"
                               + (" และให้คะแนน Centor ได้ถูก" if centor_scope(f)[0] else ""))
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
                               "(น้ำมูกข้นเหนียวใช้ยาแก้แพ้รุ่นที่ 1 ไม่ได้ แต่รุ่นที่ 2 ยังใช้ได้ ร่วมกับล้างจมูก)")
    if f.get("cough") and not f.get("cough_type"):
        helpful_missing.append("ลักษณะการไอ (ไอแห้ง/ไม่มีเสมหะ หรือ ไอมีเสมหะ) -- เพื่อเลือกระหว่างยาบรรเทาอาการไอแห้ง "
                               "กับยาละลาย/ขับเสมหะ")
    if f.get("fever") and not _TEMP_RE.search(text) and (f.get("sore_throat") or f.get("sinus") or ped):
        helpful_missing.append("อุณหภูมิที่วัดได้ -- ใช้ประเมิน"
                               + ("เกณฑ์ Centor (≥38°C)/" if centor_scope(f)[0] else "") + "ความรุนแรง")
    if _MEDS_RE.search(text):
        known.append("ยาที่ใช้มาก่อน")
    else:
        helpful_missing.append("ยาที่ใช้มาก่อนมาร้าน (ชื่อยา/ได้ผลไหม) -- เพื่อไม่ให้ใช้ยาซ้ำซ้อน/เกินขนาด และประเมินการตอบสนอง")
    if _COMORBID_RE.search(text):
        known.append("โรคประจำตัว/ภาวะพิเศษ")
    else:
        helpful_missing.append("โรคประจำตัว/ภาวะพิเศษ (ความดัน โรคหัวใจ ไต ตับ ตั้งครรภ์) -- เพื่อตรวจข้อห้าม/การปรับขนาดยา "
                               "(ตอนถามไม่ต้องยกตัวอย่างชื่อยา)")

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


def centor_scope(f: dict) -> tuple[bool, str]:
    """เกณฑ์ Modified Centor ใช้กับ 'คออักเสบ (pharyngitis)' เท่านั้น
    AAFP หน้า 4: "...using the modified Centor criteria when evaluating patients with **pharyngitis**
    to determine the likelihood of group A beta-hemolytic streptococcal infection"
    -> เคสที่อาการเด่นเป็นกล่องเสียงอักเสบ (เสียงแหบ/สายเสียงอักเสบ = laryngitis ซึ่ง AAFP ระบุว่าเป็นไวรัส
    รักษาประคับประคอง ไม่ใช้ ATB) ไม่ต้องประเมิน Centor -- เว้นแต่มีสิ่งตรวจพบที่บ่งชี้ GABHS pharyngitis
    จริง (ทอนซิลบวม/มีหนอง หรือต่อมน้ำเหลืองคอด้านหน้าโต) ร่วมด้วย
    คืน (ใช้ได้ไหม, เหตุผลที่ใช้ไม่ได้: "" | "child3" | "laryngitis")"""
    if not f.get("sore_throat"):
        return False, ""
    age = f.get("age")
    if age is not None and age < 3:
        return False, "child3"
    if f.get("hoarse") and not (f.get("tonsil_finding") is True or f.get("lymph_finding") is True):
        # ฝาปิดกล่องเสียงอักเสบ (epiglottitis) ก็ไม่ใช่ pharyngitis เช่นกัน -> ไม่ต้องใช้ Centor
        # แต่ห้ามติดป้ายว่าเป็น laryngitis (คนละโรค เป็นภาวะฉุกเฉิน) -> ไม่ส่งบันทึกใดๆ เข้า Context
        return False, ("" if f.get("epiglottitis") else "laryngitis")
    return True, ""


def centor_assessment(text: str, f: dict) -> dict | None:
    """คะแนน Modified Centor รายข้อจากข้อมูลที่ผู้ใช้ให้มาจริง (None = ยังไม่ทราบ -- ห้ามเดา) -- เฉพาะเคส pharyngitis"""
    ok, why = centor_scope(f)
    if not ok:
        if not why:
            return None
        return {"applicable": False, "reason": why, "items": [], "known": 0, "max": 0, "unknown": []}
    text = text or ""
    age = f.get("age")
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
        if ca.get("reason") == "laryngitis":
            return ("**Modified Centor -- ไม่ต้องประเมินในเคสนี้ (สำคัญ):** อาการเด่นของเคสนี้คือ **กล่องเสียงอักเสบ "
                    "(เสียงแหบ/สายเสียงอักเสบ = laryngitis)** ไม่ใช่คออักเสบ (pharyngitis) และยังไม่มีสิ่งตรวจพบที่บ่งชี้ "
                    "GABHS pharyngitis (ทอนซิลบวม/มีหนอง หรือต่อมน้ำเหลืองคอด้านหน้าโตกดเจ็บ) "
                    "-- เกณฑ์ Modified Centor ใน Context ระบุให้ใช้ **เมื่อประเมินผู้ป่วย pharyngitis** เท่านั้น "
                    "[Ref: AAFP, หน้า 4] จึง **ห้ามคำนวณ ห้ามแสดงคะแนน และห้ามเอ่ยถึงเกณฑ์ Centor ในคำตอบนี้** "
                    "(รวมถึงห้ามถามข้อมูลเพิ่มโดยอ้างว่าเป็นเกณฑ์ Centor) -- ให้ใช้เหตุผลของ laryngitis ตรงๆ แทน: "
                    "AAFP ระบุว่า laryngitis เกิดจากไวรัส หายได้เอง **ไม่ใช้ยาปฏิชีวนะ** รักษาแบบประคับประคอง")
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


def history_checklist(text: str, f: dict, *, force: bool = False) -> dict | None:
    """ตรวจข้อมูลตาม pattern ซักประวัติ -> {'missing': [{key,label,q,why}], 'dx_given', 'ped'} (None ถ้าไม่ใช่คำบรรยายเคส)
    force=True: ใช้กับข้อความสั้นที่ 'ขอยา' พร้อมอาการ (เช่น "ไอมีเสมหะ 3 วัน ขอยาหน่อย") ซึ่งไม่ผ่านเกณฑ์
    คำบรรยายเคส แต่เป็นการขอการรักษาจริง -> ต้องซักประวัติก่อนเหมือนกัน"""
    if not (force or is_case_description(text, f)):
        return None
    text = text or ""
    ped = f.get("group") == "pediatric"
    age = f.get("age")
    child12 = ped and (age is None or age <= 12)
    # throat = "เคสที่ใช้เกณฑ์ Centor ได้จริง" (pharyngitis) -- เคส laryngitis/เด็ก <3 ปี ไม่เข้าเกณฑ์
    throat = centor_scope(f)[0]
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
            + (" รวมถึงให้คะแนน Modified Centor ข้ออายุ" if throat else ""))
    if child12 and f.get("weight") is None:
        add("weight", "น้ำหนักตัว", "น้ำหนักตัวปัจจุบัน (กก.)",
            "ในเด็กจำเป็นต้องใช้คำนวณขนาดยาตามน้ำหนัก (mg/kg) ทั้งยาลดไข้และยาอื่นๆ ให้ถูกต้องและปลอดภัย")
    if not (f.get("fever") is not None or _TEMP_RE.search(text) or re.search(r"ไข้(?!หวัด)|fever", text, re.IGNORECASE)):
        add("fever", "ไข้", "มีไข้หรือไม่ ถ้ามีวัดได้กี่องศา",
            "เพื่อประเมินความรุนแรงของโรค" + (" และเป็นเกณฑ์ Modified Centor (ไข้ ≥38°C)" if throat else "")
            + (" และใช้ประกอบเกณฑ์ไซนัสอักเสบจากแบคทีเรีย" if sinus else ""))
    if f.get("runny") and not f.get("runny_char"):
        add("runny_char", "ลักษณะน้ำมูก", "ลักษณะน้ำมูก: ใส/เหลว หรือ ข้นเหนียว มีสีเหลือง-เขียว",
            "เพื่อเลือกยาลดน้ำมูกให้ตรงลักษณะ: น้ำมูกใสเหลว/ไหลเป็นสาย กับน้ำมูกข้นเหนียว ใช้ยาคนละแบบกัน "
            "และถ้าน้ำมูกข้นเหนียวต้องเน้นการล้างจมูกด้วยน้ำเกลือร่วมด้วย")
    if f.get("cough") and not f.get("cough_type"):
        add("cough_type", "ลักษณะการไอ", "ลักษณะการไอ: ไอแห้ง/ไม่มีเสมหะ หรือ ไอมีเสมหะ",
            "เพื่อเลือกกลุ่มยาให้ตรง: ไอแห้งใช้ยาบรรเทาอาการไอ (antitussive) ส่วนไอมีเสมหะใช้ยาละลาย/ขับเสมหะ (mucolytic) "
            "-- ใช้แทนกันไม่ได้")
    if not throat and f.get("cough") is None and (f.get("sore_throat") or f.get("hoarse")):
        # เคสคอ/เสียงแหบที่ไม่เข้าเกณฑ์ Centor -- ยังต้องรู้ว่ามีไอไหม แต่เหตุผลไม่ใช่ Centor
        add("cough_presence", "มีไอหรือไม่", "มีอาการไอร่วมด้วยหรือไม่",
            "เพื่อประเมินอาการร่วมและเลือกยาบรรเทาอาการให้ตรง (ถ้ามีไอ ต้องแยกว่าไอแห้งหรือไอมีเสมหะ)")
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
        # เหตุผลของคำถามซักประวัติ: บอก "ทำไมต้องถาม" พอ -- ห้ามยกตัวอย่างชื่อยา/ชื่อกลุ่มยาที่ยังไม่จำเป็น
        # (feedback อาจารย์: การยกตัวอย่างยาในขั้นซักประวัติทำให้เกิด bias ไปที่ยาตัวนั้น ทั้งที่ยังมีทางเลือกอื่นอีกมาก)
        add("meds", "ยาที่ใช้มาก่อน", "ใช้ยาอะไรมาก่อนมาร้านยาหรือยัง (ชื่อยา และได้ผลไหม)",
            "เพื่อป้องกันการใช้ยาซ้ำซ้อน/เกินขนาด (ยาลดไข้มักผสมอยู่ในยาสูตรผสมหลายตัว) และประเมินการตอบสนองต่อยาเดิม"
            + (" -- หูชั้นกลางอักเสบ: ยาปฏิชีวนะที่เคยได้ภายใน 30 วันมีผลต่อการเลือกยาตัวแรก" if ear else ""))
    if not _ALLERGY_RE.search(text):
        add("allergy", "ประวัติแพ้ยา", "ประวัติแพ้ยา (แพ้ยาอะไร และอาการแพ้เป็นแบบใด เช่น ผื่นแดง ลมพิษ หน้าบวม หายใจลำบาก)",
            "เพื่อเลือกยาที่ปลอดภัย โดยเฉพาะหากต้องใช้ยาปฏิชีวนะ -- ชนิดและความรุนแรงของอาการแพ้เป็นตัวกำหนดว่าจะเลือก"
            "ยาปฏิชีวนะทางเลือกกลุ่มใดได้บ้าง")
    if not _COMORBID_RE.search(text):
        if ped:
            add("comorbid", "โรคประจำตัว", "โรคประจำตัว (เช่น หอบหืด ภูมิแพ้ G6PD โรคหัวใจ)",
                "เพื่อตรวจข้อห้าม/ข้อควรระวังของยาในเด็ก ซึ่งโรคประจำตัวบางอย่างเป็นข้อห้ามของยาบางกลุ่ม")
        else:
            fem = bool(_FEMALE_RE.search(text))
            add("comorbid", "โรคประจำตัว", "โรคประจำตัว/ภาวะพิเศษ (เช่น ความดันโลหิตสูง โรคหัวใจ โรคไต โรคตับ แผลในกระเพาะอาหาร หอบหืด"
                + (" ตั้งครรภ์/ให้นมบุตร" if fem else "") + ")",
                "เพื่อตรวจข้อห้าม/ข้อควรระวังของยาก่อนเลือกยาให้ผู้ป่วย เพราะโรคประจำตัวบางอย่างเป็นข้อห้ามหรือต้องปรับขนาดยา"
                + (" รวมถึงความปลอดภัยของยาในหญิงตั้งครรภ์/ให้นมบุตร" if fem else ""))
    return {"missing": missing, "dx_given": bool(_DIAGNOSIS_GIVEN_RE.search(text)), "ped": ped}


# ข้อความ "ขอยา/ขอคำแนะนำการรักษา" ที่มีอาการ URI แต่สั้นเกินกว่าจะนับเป็นคำบรรยายเคส
# (เช่น "ไอมีเสมหะมา 3 วัน ขอยาหน่อย") -> ยังต้องซักประวัติก่อนจ่ายยา
_CARE_REQUEST_RE = re.compile(
    r"ขอยา|ขอคำแนะนำ|แนะนำยา|จ่ายยา|ยาอะไร|ยาตัวไหน|รักษา(?:ยังไง|อย่างไร)|ทำ(?:ยังไง|อย่างไร)|จัดการอย่างไร|ควรให้ยา",
    re.IGNORECASE)


# ─── ยังไม่มีอาการ + ภาวะนอกขอบเขต URI -> ตอบด้วยความรู้นอกคู่มือ ห้ามเดาโรค ────────
# feedback หลัง deploy: ผู้ใช้พิมพ์ "เป็นเบาหวาน" แล้วถามต่อ "กินยาอะไร" -> ระบบตอบว่า
# "สำหรับเคสโรคหวัด (Common cold) ในผู้ป่วยรายนี้..." = แต่งโรคขึ้นมาเอง (hallucination)
# และพอถามต่อเนื่อง บริบท "เบาหวาน" ก็หาย กลายเป็นเคสหวัดเต็มตัว (conversation diff)
# สาเหตุ: Context ทั้งคลังเป็นเรื่อง URI + ไม่มีสัญญาณบอกโมเดลว่า "ผู้ใช้ยังไม่ได้บอกอาการ"
# -> แนบบันทึกบอกโมเดลให้ชัด แต่ **ยังให้ตอบตามปกติ** (ประเภท 6 + ความรู้นอกคู่มือ)
#    ไม่ตัดไปเส้นทางซักประวัติ เพราะผู้ใช้ต้องการคำตอบ ไม่ใช่คำถามกลับ
_PATIENT_CONTEXT_RE = re.compile(
    r"เบาหวาน|ความดัน|โรคไต|ไตวาย|โรคตับ|โรคหัวใจ|หอบหืด|ไทรอยด์|G6PD|แผลในกระเพาะ|โรคกระเพาะ|"
    r"ตั้งครรภ์|ให้นม|โรคประจำตัว|ประจำตัว|แพ้ยา|อายุ\s*\d+|\d+\s*(?:ปี|ขวบ|เดือน)|น้ำหนัก\s*\d+|"
    r"ผู้ป่วย|คนไข้|ผู้สูงอายุ|ทารก|ตั้งท้อง", re.IGNORECASE)
# ภาวะ/โรคนอกขอบเขตคู่มือ URI -- ตรงกับ "ประเภท 6 (นอกขอบเขต)" ใน SYSTEM PROMPT
# (รายชื่อที่พบบ่อยหน้าร้าน -- ส่วนโรคที่ไม่อยู่ในลิสต์ ใช้ตัวจับแบบทั่วไป _CONDITION_DECL_RE ด้านล่าง)
_OUT_OF_SCOPE_RE = re.compile(
    r"(เบาหวาน|ความดัน(?:โลหิต)?สูง|ความดัน|โรคไต|ไตวาย|โรคตับ|ตับแข็ง|โรคหัวใจ|หัวใจล้มเหลว|"
    r"ไทรอยด์|ไขมันในเลือด|ไขมันสูง|เกาต์|เก๊าท์|รูมาตอยด์|ข้อเสื่อม|กระดูกพรุน|"
    r"ปวดหลัง|ปวดเอว|ปวดกล้ามเนื้อ|ปวดเข่า|ปวดข้อ|ปวดท้อง|ท้องเสีย|ท้องผูก|ริดสีดวง|"
    r"โรคผิวหนัง|ผื่นผิวหนัง|ผื่นคัน|สะเก็ดเงิน|ลมพิษเรื้อรัง|G6PD|"
    r"แผลในกระเพาะ|โรคกระเพาะ|กรดไหลย้อน|ต้อหิน|ต้อกระจก|ต่อมลูกหมาก|"
    r"มะเร็ง|ไมเกรน|ซึมเศร้า|วิตกกังวล|ลมชัก|พาร์กินสัน|อัลไซเมอร์|เอสแอลอี|\bSLE\b|"
    r"หลอดเลือดสมอง|อัมพฤกษ์|อัมพาต|โลหิตจาง|ธาลัสซีเมีย|เอดส์|\bHIV\b|วัณโรค)", re.IGNORECASE)
# ตัวจับแบบทั่วไป: ผู้ใช้ "ประกาศโรค" ด้วยคำว่า 'โรค' ตรงๆ (เช่น "เป็นโรคลูปัส", "ป่วยเป็นโรคเก๊าท์")
# -> ครอบโรคที่ไม่ได้อยู่ในรายชื่อข้างบน โดยไม่ต้องไล่เขียนทุกโรค
# ต้องมีคำว่า "โรค" เป็นสัญญาณ เพื่อกันจับผิดจากคำว่า "เป็น" ลอยๆ ("เป็นมา 3 วัน", "เป็นๆ หายๆ")
_CONDITION_DECL_RE = re.compile(
    r"(?:ป่วยเป็น|เป็น|มี|รักษา)\s*โรค([ก-๙A-Za-z][ก-๙A-Za-z]{1,14})")
# โรค/อาการที่ "อยู่ในขอบเขต URI" -> ถ้าตรงกับพวกนี้ ห้ามนับเป็นนอกขอบเขต
_URI_CONDITION_RE = re.compile(
    r"หวัด|ไข้|เจ็บคอ|คออักเสบ|ทอนซิล|ไซนัส|จมูกอักเสบ|กล่องเสียง|หลอดลม|หูชั้นกลาง|หูน้ำหนวก|"
    r"ภูมิแพ้|ทางเดินหายใจ|ติดเชื้อ|"
    # "โรคประจำตัว" เป็นคำเรียกรวม ไม่ใช่ชื่อโรค -> ไม่ต้องยกมาเป็นชื่อภาวะนอกขอบเขต
    r"ประจำตัว|อะไร|ไหน|นี้|นั้น", re.IGNORECASE)


def no_symptom_yet(text: str, f: dict) -> bool:
    """ข้อความ/เคสนี้ "ยังไม่ได้บอกอาการ URI เลย" แต่กำลังพูดถึงผู้ป่วยหรือขอยาอยู่"""
    text = text or ""
    if has_uri_symptom(f):
        return False
    # เอ่ยถึงภาวะนอกขอบเขต (เช่น "ปวดหลัง", "เป็นเกาต์") ก็ถือว่ากำลังพูดถึงผู้ป่วยเหมือนกัน
    # -- ไม่งั้นโรคที่ไม่ได้อยู่ใน _PATIENT_CONTEXT_RE จะหลุดไปไม่มีชั้นกันเดาโรค
    return bool(_PATIENT_CONTEXT_RE.search(text) or _CARE_REQUEST_RE.search(text)
                or out_of_scope_condition(text))


def out_of_scope_condition(text: str) -> str:
    """ชื่อภาวะนอกขอบเขต URI ที่ผู้ใช้เอ่ยถึง ("" = ไม่มี)

    รวมสองทาง: รายชื่อที่พบบ่อย + รูปแบบ "เป็นโรค___" ทั่วไป (ครอบโรคที่ไม่ได้เขียนไว้ล่วงหน้า)
    """
    text = text or ""
    found = list(dict.fromkeys(m.group(1) for m in _OUT_OF_SCOPE_RE.finditer(text)))
    for m in _CONDITION_DECL_RE.finditer(text):
        name = m.group(1).strip()
        # ต้องไม่ใช่โรคในขอบเขต URI และไม่ซ้ำกับที่จับได้จากรายชื่อแล้ว
        if _URI_CONDITION_RE.search(name) or any(name in f or f in name for f in found):
            continue
        found.append("โรค" + name)
    return ", ".join(found)


def out_of_scope_note(text: str, f: dict) -> str:
    """บันทึกกันการเดาโรคเมื่อผู้ใช้ยังไม่ได้บอกอาการ ("" = ไม่เข้าเงื่อนไข)

    เจตนา: **ให้ตอบ ไม่ใช่ถามกลับ** -- ผู้ใช้ถามอะไรมาก็ตอบสิ่งนั้น เพียงแต่ห้ามสมมติว่าเป็นโรคใด
    """
    if not no_symptom_yet(text, f):
        return ""
    oos = out_of_scope_condition(text)
    lines = [
        "**บริบทที่ระบบตรวจพบ: ผู้ใช้ยัง \"ไม่ได้ให้อาการของผู้ป่วย\" เลยในบทสนทนานี้"
        + (f" และเอ่ยถึง **{oos}** ซึ่งอยู่นอกขอบเขตคู่มือ URI ในระบบ**" if oos else "**"),
        "- **ห้ามสมมติว่าผู้ป่วยเป็นหวัด/ไข้หวัด/คออักเสบ/ไซนัสอักเสบ หรือโรคทางเดินหายใจใดๆ เด็ดขาด** "
        "และ **ห้ามเขียนหัวข้อ \"การวินิจฉัยเบื้องต้น\" ของโรคที่ผู้ใช้ไม่ได้บอก** "
        "-- ข้อห้ามนี้มีผลกับ **ทุกเทิร์นถัดไป** ของบทสนทนานี้จนกว่าผู้ใช้จะบอกอาการจริง",
        "- **ให้ตอบคำถามที่ผู้ใช้ถาม ไม่ใช่ตอบกลับด้วยรายการคำถามซักประวัติ** "
        "-- ห้ามขึ้นหัวข้อ \"ข้อมูลที่ต้องซักเพิ่มเติม\" เป็นคำตอบหลักในเคสแบบนี้",
    ]
    if oos:
        lines += [
            "- จัดเป็น **ประเภท 6 (นอกขอบเขต)** ให้ชัดเจนก่อน แล้ว **ให้ความรู้ทั่วไปที่ถูกต้องได้เลย** "
            "โดยกำกับว่าเป็น \"ความรู้นอกคู่มือ\" ไม่ใช่ข้อมูลจาก Guideline ในระบบ "
            "พร้อมแนบ URL อ้างอิงภายนอกที่ชี้ถึงเอกสารจริง (ตามกฎการอ้างอิงภายนอก)",
            f"- **ถ้าผู้ใช้ถามเรื่องยา ให้ตอบเป็นหลักการที่ใช้ได้จริง** เช่น ข้อควรระวังของยากลุ่มต่างๆ "
            f"ในผู้ป่วย{oos} และยกแนวทางจาก Guideline ใน Context ที่ใกล้เคียงมาประกอบได้ "
            f"แต่ต้องระบุว่าเป็น **แนวทางทั่วไป ไม่ใช่การวินิจฉัย/สั่งยาให้ผู้ป่วยรายนี้**",
            f"- ปิดท้ายด้วยข้อจำกัด: เรื่อง{oos}โดยตรงควรปรึกษา **แพทย์ผู้ดูแลโรคประจำตัวหรือเภสัชกรที่ร้าน** "
            "และ **เชิญชวน** (ไม่ใช่บังคับ) ให้บอกอาการเพิ่มถ้าต้องการคำแนะนำที่เจาะจงกว่านี้",
        ]
    else:
        lines.append("- ตอบเท่าที่ข้อมูลมีจริง และ **เชิญชวน** (ไม่ใช่บังคับ) ให้บอกอาการเพิ่ม "
                     "ถ้าต้องการคำแนะนำที่เจาะจงกว่านี้")
    return chr(10).join(lines)


def is_bare_care_request(text: str, f: dict) -> bool:
    """มีอาการ URI + ขอการรักษา แต่ข้อความสั้นจนไม่เข้าเกณฑ์คำบรรยายเคส"""
    return bool(has_uri_symptom(f) and _CARE_REQUEST_RE.search(text or "") and not is_case_description(text, f))


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
    if centor_scope(f)[0]:
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


_FORM_LABEL_WORDS = ("ยาอม", "ยาพ่น", "ยากลั้วคอ")


def may_become_form_label(tail: str) -> bool:
    r"""(streaming) บรรทัดท้ายที่ยังไม่จบนี้ "มีโอกาสกลายเป็นหัวข้อหมวดยาเฉพาะที่คอ" ไหม

    ต้องกันไว้ตั้งแต่ยังพิมพ์ชื่อหมวดไม่จบ: ถ้าปล่อยครึ่งคำ ("...\nยาพ") ออกไปก่อน
    บรรทัดหัวข้อจะถูกหั่นคนละ chunk แล้ว fix_form_labels มองไม่เห็นหัวข้อเต็มบรรทัด
    -> แก้ชื่อหมวด/ตัดชื่อซ้ำไม่ได้ และถ้าชิ้นหลังไปเข้าเงื่อนไขเองจะเขียนชื่อหมวดทับซ้อนกัน
    """
    if not tail:
        return False
    if any(w in tail for w in _FORM_LABEL_WORDS):
        return True
    # ท้ายบรรทัดเป็น "ครึ่งคำ" ของชื่อหมวด (เช่น "ยาพ" ของ "ยาพ่น")
    return any(tail.endswith(w[:k]) for w in _FORM_LABEL_WORDS for k in range(1, len(w)))


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


def _canonical_form_label(forms: set[str], label: str) -> str:
    """ชื่อหัวข้อหมวดมาตรฐานของชุดรูปแบบยา (เรียงตาม _FORM_WORDS และไม่มีชื่อรูปแบบซ้ำ)"""
    suffix = "บรรเทาอาการเจ็บคอ" if "เจ็บคอ" in label else ""
    names = {"spray": "ยาพ่น" if suffix else "ยาพ่นคอ", "lozenge": "ยาอม", "gargle": "ยากลั้วคอ"}
    return "/".join(names[fm] for fm, _ in _FORM_WORDS if fm in forms) + suffix


# ─── Example-completeness guard (ยก "เช่น <ยาตัวเดียว>" ทั้งที่กลุ่มนั้นมีหลายตัว) ──────────
# feedback อาจารย์: "ยาแก้แพ้รุ่นที่ 1 (เช่น Chlorpheniramine)" -> ผู้อ่าน bias คิดว่ามีแค่ตัวนั้น
# ทั้งที่ในตาราง Dose มีรุ่นที่ 1 อีกหลายตัวที่ไม่เหมาะกับเคสเดียวกัน
# ทำเฉพาะ "บริบทเชิงลบ" (ไม่เหมาะ/ไม่แนะนำ/หลีกเลี่ยง) เท่านั้น -- การเติมชื่อยาที่ "ถูกกัน"
# ให้ครบปลอดภัยเสมอ ส่วนฝั่ง "แนะนำให้ใช้" ห้ามเติมเอง (ต้องผ่านการคัดตัวเลือกของ Catalog)
_G1_LABEL_RE = re.compile(
    r"(?:ยาแก้แพ้|ยาลดน้ำมูก|antihistamine)[^\n]{0,20}?(?:รุ่นที่\s*1|generation\s*1|1st[\s-]?gen|first[\s-]?gen)",
    re.IGNORECASE)
_NEGATIVE_RE = re.compile(r"ไม่เหมาะ|ไม่แนะนำ|หลีกเลี่ยง|ควรเลี่ยง|ไม่ควรใช้|ห้ามใช้|งดใช้")
_EG_PAREN_RE = re.compile(r"[\(（]\s*(เช่น|ได้แก่|ตัวอย่างเช่น)?\s*([^()（）\n]{3,160}?)\s*[\)）]")
_EG_PLAIN_RE = re.compile(r"(เช่น|ได้แก่)\s+([A-Za-z][A-Za-z\s\-,/]{3,80})")


def may_need_group_expand(seg: str) -> bool:
    """บรรทัดที่ยังมาไม่ครบนี้อาจต้องเติมชื่อยาให้ครบ -> ตอน streaming ให้กันไว้จนจบบรรทัดก่อน
    (ไม่งั้นชื่อกลุ่มกับช่วง 'เช่น ...' ถูกหั่นคนละ chunk แล้วตัวเติมจะมองไม่เห็น)"""
    return bool(seg) and bool(_G1_LABEL_RE.search(seg))


def expand_group_examples(text: str) -> str:
    """บรรทัดที่บอกว่า 'ยาแก้แพ้รุ่นที่ 1 ไม่เหมาะ' แล้วยกตัวอย่างมาแค่ตัวเดียว -> เติมชื่อให้ครบทุกตัวในตาราง

    แก้เฉพาะช่วง "ตัวอย่าง" ที่อยู่ติดหลังชื่อกลุ่ม และเฉพาะเมื่อยกมา **ตัวเดียว** เท่านั้น
    (ถ้าโมเดลยกมา 2 ตัวขึ้นไปแล้ว = หลากหลายพอ -> ไม่แตะ คงถ้อยคำเดิมของโมเดล)
    """
    if not text or not _G1_LABEL_RE.search(text):
        return text
    members = class_drug_names("antihistamine", first_gen=True)
    if len(members) < 2:
        return text
    full = ", ".join(members)
    # ตัวย่อที่ใช้กันหน้าร้าน (เขียน "CPM" แทน Chlorpheniramine) -> ต้องจับได้ด้วย ไม่งั้นตัวเติมไม่ทำงาน
    alias = {"chlorpheniramine": ("cpm",)}
    keys = [(m, (m.lower(),) + alias.get(m.lower(), ())) for m in members]

    def _named_in(s: str) -> list[str]:
        low_s = s.lower()
        return [m for m, ks in keys if any(re.search(rf"\b{re.escape(k)}\b", low_s) for k in ks)]

    def _fix_span(span: str) -> str | None:
        """คืนข้อความใหม่ถ้าช่วงนี้ยกชื่อยาของกลุ่มมาแค่ตัวเดียว (None = ไม่ต้องแก้)"""
        hit = _named_in(span)
        if len(hit) != 1:
            return None
        # มีเนื้อความอื่นปนนอกจากชื่อยา (เช่น คำอธิบายยาว) -> ไม่แตะ กันแก้ผิดที่
        rest = re.sub("|".join(re.escape(k) for k in dict(keys)[hit[0]]), "", span, flags=re.IGNORECASE)
        if re.search(r"[ก-๙]{4,}", rest):
            return None
        return full

    out = []
    for line in text.split("\n"):
        m = _G1_LABEL_RE.search(line)
        # ทั้งบรรทัดเอ่ยชื่อยาของกลุ่มไปแล้ว >= 2 ตัว = หลากหลายพอ -> ไม่แตะ
        # (ต้องดูทั้งบรรทัด ไม่ใช่เฉพาะในวงเล็บ เช่น "ได้แก่ CPM (Chlorpheniramine), Brompheniramine")
        if not m or not _NEGATIVE_RE.search(line) or len(_named_in(line)) >= 2:
            out.append(line)
            continue
        tail = line[m.end():]
        new_tail, done = tail, False
        for rx in (_EG_PAREN_RE, _EG_PLAIN_RE):
            mm = rx.search(tail[:180])          # ต้องอยู่ติดหลังชื่อกลุ่ม ไม่ใช่ที่อื่นในบรรทัด
            if not mm or _fix_span(mm.group(2)) is None:
                continue
            s, e = mm.start(2), mm.start(2) + len(mm.group(2).rstrip())
            seg = tail[mm.start(): s] + full + tail[e: mm.end()]
            if mm.group(1) == "เช่น":
                seg = seg.replace("เช่น", "ได้แก่", 1)   # ตอนนี้ครบทุกตัวแล้ว ไม่ใช่ "ตัวอย่าง"
            new_tail = tail[: mm.start()] + seg + tail[mm.end():]
            done = True
            break
        out.append(line[: m.end()] + new_tail if done else line)
    return "\n".join(out)


def fix_form_labels(text: str) -> str:
    """หัวข้อหมวดยาเฉพาะที่คอ -> ให้ตรง "รูปแบบยาที่อยู่ใต้หัวข้อจริง" และไม่มีชื่อรูปแบบซ้ำ

    สองอาการที่เจอจริงจาก feedback:
      (1) เรียกผิดรูปแบบ -- "ยาอม:" แต่รายการใต้หัวข้อเป็นยาพ่น/ยากลั้วคอ
          (เคสที่เจอ: ยากลั้วคอถูกเรียกว่า "ยาอม" เพราะวิธีใช้เขียนว่า "อมกลั้วคอ ... แล้วบ้วนทิ้ง")
      (2) ชื่อรูปแบบซ้ำในหัวข้อเดียว -- "ยาพ่นคอ/ยาพ่นคอ/ยาอม:" หรือ
          "ยาพ่นคอ/ยาพ่น/ยาอม/ยากลั้วคอบรรเทาอาการเจ็บคอ:" (ยาพ่นออกมาซ้ำ)
          เคสนี้ "ชุดรูปแบบ" ตรงอยู่แล้วจึงหลุดการตรวจเดิมที่เทียบเฉพาะ set -> ต้อง dedupe แยก
    ยึด "รูปแบบยาจากตาราง Dose" เป็นตัวตัดสินเสมอ (ไม่เชื่อถ้อยคำที่โมเดลเขียน)
    """
    if not text or not re.search(r"ยาอม|ยาพ่น|ยากลั้วคอ", text):
        return text
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = _LABEL_LINE_RE.match(ln)
        if not m:
            continue
        end = _label_block_end(lines, i)
        block = ln[m.end():] + "\n" + "\n".join(lines[i + 1: end if end is not None else len(lines)])
        label = m.group(2)
        have = {fm for fm, pat in _FORM_WORDS if re.search(pat, label)}
        forms = _forms_in(block)
        dup = any(len(re.findall(pat, label)) > 1 for _, pat in _FORM_WORDS)
        if forms:
            if have == forms and not dup:
                continue          # ตรงรูปแบบแล้วและไม่ซ้ำ -> ไม่แตะ (คงถ้อยคำเดิมของโมเดล)
        elif dup and have:
            forms = have          # ยังไม่รู้จักยาในบล็อก (เช่นตอนสตรีมยังไม่ครบ) -> แก้แค่ชื่อซ้ำ
        else:
            continue
        new_label = _canonical_form_label(forms, label)
        if new_label and new_label != label.strip():
            lines[i] = ln[: m.start(2)] + new_label + ln[m.end(2):]
    return "\n".join(lines)


# ─── Expert Opinion block integrity gateway (Phase2 opt4) ────────────────────
# feedback หลัง deploy: "บล็อก Expert Opinion บางเคสมีแต่กรอบสีเขียวกับหัวข้อ เนื้อหาหายไป"
# สาเหตุ: ฝั่งเว็บห่อบล็อกนี้โดยไล่เก็บ element ถัดจากหัวข้อ แล้ว "หยุดทันที" เมื่อเจอหัวข้อย่อยตัวหนา
# ที่ขึ้นต้นด้วยคำว่า "ยา" (เช่น `**ยาทางเลือกแรก (First-line):**`) เพราะถือว่าเป็นหัวข้อยาของส่วนปกติ
# -> ถ้าโมเดลเขียนหัวข้อย่อยนั้นเป็น "บรรทัดแรก" ใต้หัวข้อบล็อก ก็ไม่มีอะไรถูกเก็บเข้ากรอบเลย = กรอบว่าง
# (อีกกรณี: โมเดลขึ้นหัวข้อบล็อกแล้วไม่เขียนเนื้อหาต่อเลย -> กรอบว่างเช่นกัน)
#
# แก้ที่ต้นทาง: ประกันว่าใต้หัวข้อบล็อกต้องมีเนื้อหาให้เก็บเข้ากรอบเสมอ
#
# **ที่มาของเนื้อหา Expert Opinion (ห้ามเปลี่ยน -- ระบุไว้ตั้งแต่โจทย์ Phase2_optimize_1.md):**
# Expert Opinion ทั้งหมด **มาจาก AAFP** ไม่ใช่ข้อความที่ระบบแต่งขึ้นเอง มีสองแหล่งเท่านั้น
#   - ไซนัสอักเสบ: "Note on Thai Clinical Practice" **ถูก ingest เข้า embedding แล้ว**
#     (chunk AAFP_0021 = AAFP หน้า 5) -> ปกติโมเดลดึงจาก Context เองพร้อม [Ref: AAFP, หน้า 5]
#   - เจ็บคอ/คออักเสบ: บล็อก "RDU Practice" อยู่ติด TABLE 2 Modified Centor ใน AAFP หน้า 4
#     แต่ **ไม่ได้ ingest** (โจทย์ให้ใส่เป็น prompt แทน เพราะ ingest แล้ว embedding อาจเพี้ยน)
#     -> อยู่ในหัวข้อ EXPERT OPINION ของ SYSTEM PROMPT พร้อม [Ref: AAFP, หน้า 4]
#
# ดังนั้นประโยคที่ gateway นี้เติมได้ มีเพียง **ถ้อยคำต้นฉบับของสองแหล่งนี้** เท่านั้น
# ถ้าเคสไม่เข้าสองหัวข้อนี้ = ไม่มีฐาน Expert Opinion ใน AAFP -> **ตัดหัวข้อที่ค้างทิ้ง**
# ห้ามแต่งแนวปฏิบัติขึ้นมาใหม่เด็ดขาด (ตรงกับกฎใน SYSTEM PROMPT: "ห้ามแต่งแนวปฏิบัติไทยเกิน
# จากที่ระบุในหัวข้อนี้/ใน Context")
_EXPERT_HEAD_RE = re.compile(r"^[ \t]{0,3}(?:#{1,4}[ \t]*)?(?:\*\*)?[^\n*]*ปฏิบัติจริง[^\n*]*(?:\*\*)?[ \t]*:?[ \t]*$")
_BOLD_ONLY_LINE_RE = re.compile(r"^[ \t]{0,3}\*\*([^\n*]+)\*\*[ \t]*$")
# บรรทัดที่ทำให้ฝั่งเว็บ "ปิดกรอบ" ทั้งที่ยังไม่ได้เก็บอะไรเข้าไป = กรอบว่างจริง
# (หัวข้อจริง `###` / หัวข้อยาแบบ "3a. ยา..." / "■ ...")
# ส่วนหัวข้อย่อยตัวหนาที่ขึ้นต้นด้วย "ยา" ฝั่งเว็บรับไปแล้วด้วยตัวนับ absorbed ใน applyExpertBlocks()
# จึง **ไม่นับว่าว่าง** -> ไม่ต้องเติมอะไรทับข้อความของโมเดล
_EXPERT_STOPPER_RE = re.compile(r"^[ \t]{0,3}(?:#{1,4}[ \t]|\d+[a-zA-Z]\.[ \t]*ยา|■)")
_TREATMENT_SUBLABEL_RE = re.compile(r"^ยา\S")          # ตรงกับ TREATMENT_SUBLABEL_PATTERN ฝั่งเว็บ
# (ถ้อยคำต้นฉบับจาก AAFP -- คัดมาตรงตัว ไม่เรียบเรียงใหม่, คู่กับหน้าที่ต้องอ้างตาม SYSTEM PROMPT)
_EXPERT_LEADS: dict[str, tuple[str, str]] = {
    # RDU Practice ข้าง TABLE 2 Modified Centor (AAFP หน้า 4) -- ไม่ได้ ingest, อยู่ใน SYSTEM PROMPT
    "pharyngitis": ("**จ่ายยาปฏิชีวนะในผู้ป่วยโรคเจ็บคอ/คออักเสบ เมื่อมีคะแนนจาก Centor criteria หรือ "
                    "McIsaac score เท่ากับ 3 หรือ 4 แต้ม หรือ 5 แต้ม และหลีกเลี่ยงการจ่ายยาปฏิชีวนะ"
                    "ในผู้ที่ได้คะแนนน้อยกว่า 3 แต้ม**", "4"),
    # Note on Thai Clinical Practice (AAFP หน้า 5) -- ingest แล้ว: chunk AAFP_0021
    "sinusitis": ("**ไซนัสอักเสบ แม้มีอาการติดต่อกันไม่ถึง 10 วัน สามารถพิจารณาจ่ายยาปฏิชีวนะได้เลย "
                  "ถ้ามีอาการและอาการแสดงเข้าได้กับไซนัสอักเสบชัดเจน ขึ้นกับดุลพินิจของเภสัชกร**", "5"),
}
# เคสที่ไม่เข้าสองหัวข้อข้างบน = ไม่มีแนวปฏิบัติไทยเฉพาะทางใน AAFP ให้อ้าง
# -> ใส่ได้เฉพาะ "ประโยคกรอบความคิด" (Guideline เป็นหลัก + หน้าร้านใช้ดุลพินิจ) ซึ่ง **ไม่ใช่คำแนะนำ
# ทางคลินิกใหม่** ไม่มีชื่อยา ไม่มีขนาดยา ไม่มีเกณฑ์ตัดสิน จึงไม่กระทบเนื้อหาหลัก และไม่มี [Ref]
# เพราะไม่ได้ยกข้อความจาก AAFP มาอ้าง
_EXPERT_LEAD_GENERIC = ("ให้ยึดคำแนะนำตาม Guideline ข้างต้นเป็นหลัก ส่วนการตัดสินใจหน้าร้าน "
                        "ให้พิจารณาตามดุลพินิจของเภสัชกรร่วมกับข้อมูลของผู้ป่วยรายนี้")


def _expert_lead(f: dict | None, ctx_pages: dict | None = None) -> str:
    """ประโยคนำของบล็อก Expert Opinion ตามชนิดเคส -- "" = เคสนี้ไม่มีฐาน Expert Opinion ใน AAFP

    ใส่ [Ref: AAFP, หน้า N] **เฉพาะเมื่อหน้านั้นอยู่ใน Context จริง** (ctx_pages ชุดเดียวกับตัวตรวจ
    citation) -- ถ้าไม่อยู่ ให้เขียนประโยคเปล่าๆ ดีกว่าอ้างหน้าที่ Context ไม่ได้ให้มา
    """
    f = f or {}
    key = ""
    if f.get("sinus") and f.get("group") != "pediatric":
        key = "sinusitis"
    else:
        try:
            if centor_scope(f)[0]:
                key = "pharyngitis"
        except Exception:  # noqa: BLE001
            return ""
    if not key:
        return _EXPERT_LEAD_GENERIC
    lead, page = _EXPERT_LEADS[key]
    if page in ((ctx_pages or {}).get("AAFP") or []):
        lead += f" [Ref: AAFP, หน้า {page}]"
    return lead


def _expert_body_missing(lines: list[str], i: int) -> bool:
    """ใต้หัวข้อบล็อก Expert Opinion (บรรทัดที่ i) "ไม่มีเนื้อหาให้เก็บเข้ากรอบเลย" หรือไม่"""
    for ln in lines[i + 1:]:
        if not ln.strip():
            continue
        if _EXPERT_STOPPER_RE.match(ln):
            return True
        m = _BOLD_ONLY_LINE_RE.match(ln)
        if not m:
            return False        # มีประโยค/รายการ -> ฝั่งเว็บเก็บเข้ากรอบได้ ไม่ต้องแตะ
        # ตัวหนาที่ขึ้นต้นด้วย "ยา" -> ฝั่งเว็บเก็บเข้ากรอบแล้ว (absorbed) = ไม่ว่าง
        # ตัวหนาแบบอื่นจะถูกเลื่อนเป็นหัวข้อ (###) -> ปิดกรอบตั้งแต่ยังไม่เก็บอะไร = ว่างจริง
        return not _TREATMENT_SUBLABEL_RE.match(m.group(1).strip())
    return True                 # ไม่มีบรรทัดถัดไปเลย


def ensure_expert_block(text: str, f: dict | None = None, ctx_pages: dict | None = None) -> str:
    """กันบล็อก "ในทางปฏิบัติจริง (บริบทร้านยาไทย)" ไม่ให้กลายเป็นกรอบเขียวเปล่า

    เคสที่เข้าหัวข้อ Expert Opinion ของ AAFP (เจ็บคอ / ไซนัสผู้ใหญ่) -> เติม "ถ้อยคำต้นฉบับจาก AAFP"
    เคสอื่น -> เติม "ประโยคกรอบความคิด" (Guideline เป็นหลัก + หน้าร้านใช้ดุลพินิจ) ซึ่งไม่มีชื่อยา
    ไม่มีขนาดยา ไม่มีเกณฑ์ตัดสิน จึงไม่กระทบเนื้อหาหลักของคำตอบ
    ทำงานเฉพาะตอนที่บล็อกจะว่างจริงๆ เท่านั้น -- คำตอบที่เขียนถูกอยู่แล้วจะไม่ถูกแตะเลย
    """
    if not text or "ปฏิบัติจริง" not in text:
        return text
    lines = text.split(chr(10))
    out: list[str] = []
    lead = None
    for i, ln in enumerate(lines):
        is_head = bool(_EXPERT_HEAD_RE.match(ln)) and "ปฏิบัติจริง" in ln and (
            # ต้องเป็น "บรรทัดหัวข้อของบล็อก" จริงๆ ไม่ใช่ประโยคที่บังเอิญมีคำนี้
            ln.lstrip().startswith("#") or bool(_BOLD_ONLY_LINE_RE.match(ln)))
        if not is_head or not _expert_body_missing(lines, i):
            out.append(ln)
            continue
        if lead is None:
            lead = _expert_lead(f, ctx_pages)
        if not lead:
            continue            # ตัดหัวข้อที่ค้างทิ้ง (ไม่เติมเนื้อหาที่ไม่มีที่มาใน AAFP)
        out += [ln, "", lead]
    return chr(10).join(out)


def open_expert_head_start(text: str) -> int | None:
    """(streaming) ถ้าท้ายข้อความเป็นหัวข้อบล็อก Expert Opinion ที่ยังไม่เห็นบรรทัดเนื้อหาถัดไป
    -> คืนตำแหน่งเริ่มบรรทัดนั้น เพื่อกันไว้ก่อน (ต้องเห็นบรรทัดถัดไปจึงตัดสินได้ว่ากรอบจะว่างหรือไม่)"""
    if not text or "ปฏิบัติจริง" not in text:
        return None
    lines = text.split(chr(10))
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        if "ปฏิบัติจริง" not in ln or not _EXPERT_HEAD_RE.match(ln):
            continue
        if not (ln.lstrip().startswith("#") or _BOLD_ONLY_LINE_RE.match(ln)):
            return None
        # ต้องเห็น "บรรทัดเนื้อหาที่จบแล้ว" (มี \n ปิดท้าย) จึงตัดสินได้ -- บรรทัดสุดท้ายยังไม่จบ ไม่นับ
        if any(x.strip() for x in lines[i + 1:-1]):
            return None
        return sum(len(x) + 1 for x in lines[:i])
    return None


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


# ─── Dual-guideline references (เด็ก: URI เด็ก 2562 + AAFP มีข้อมูลเดียวกัน ต้องอ้างทั้งคู่) ─────
# AAFP หน้า 6 (TABLE 4) มีคอลัมน์ "Children" ของ ABRS / AOM / GABHS pharyngitis ครบ
# -> เคสเด็กที่อ้างเฉพาะ URI เด็ก 2562 (หรือเฉพาะ AAFP) ทั้งที่อีกเล่มก็มีขนาดยาตัวเดียวกันอยู่ใน Context
#    ให้เติม [Ref] ของอีกเล่มให้ เพื่อให้เภสัชกรกดดูได้ทั้งสองแหล่ง (ตาม feedback "ให้ดึงมาทั้ง 2 แหล่ง")
_ATB_NAMES = (
    "amoxicillin/clavulanate", "amoxicillin", "clavulanate", "augmentin", "penicillin v", "penicillin g",
    "penicillin", "cefdinir", "cefpodoxime", "cefixime", "cephalexin", "cefuroxime", "ceftriaxone",
    "azithromycin", "clarithromycin", "erythromycin", "clindamycin", "doxycycline",
)
_GUIDELINE_LABELS = {"AAFP": "AAFP", "URI": "URI เด็ก 2562"}
_ATB_DOSE_TOKEN_RE = re.compile(r"\d\s*(?:mg|มก|กรัม|g\b)", re.IGNORECASE)
_LINE_REF_RE = re.compile(r"\[Ref:\s*([^\],]+?)\s*(?:,[^\]]*)?\]")


def _atb_in(text: str) -> set[str]:
    """ชื่อยาปฏิชีวนะที่ "ถูกกล่าวถึงจริง" ในข้อความ

    ต้องดูเป็นรายตำแหน่ง ไม่ใช่รายชื่อ: ตาราง AAFP หน้า 6 มีทั้ง "Amoxicillin/clavulanate" (ABRS)
    และ "Amoxicillin" เดี่ยว (AOM first-line) อยู่คนละแถว -- ถ้าตัดชื่อสั้นทิ้งเพราะเป็นสตริงย่อยของชื่อยาว
    จะทำให้หา "amoxicillin" ในตารางไม่เจอ แล้วเคสเด็กจะอ้างอิงได้เล่มเดียว
    """
    low = (text or "").lower()
    spans: list[tuple[int, int]] = []
    found: set[str] = set()
    for name in sorted(_ATB_NAMES, key=len, reverse=True):   # ชื่อยาวจับจองตำแหน่งก่อน
        start = 0
        while True:
            i = low.find(name, start)
            if i < 0:
                break
            end = i + len(name)
            if not any(s <= i and end <= e for s, e in spans):   # ไม่ได้อยู่ในชื่อยาวที่จับไปแล้ว
                spans.append((i, end))
                found.add(name)
            start = i + 1
    return found


def guideline_pages_by_drug(chunks: list[dict]) -> dict[str, dict[str, str]]:
    """{ชื่อยาปฏิชีวนะ: {'AAFP': 'เลขหน้า', 'URI': 'เลขหน้า'}} จาก chunk ที่อยู่ใน Context จริง
    นับเฉพาะ chunk ที่มี 'ทั้งชื่อยาและตัวเลขขนาดยา' (กันหน้าที่เอ่ยชื่อยาลอยๆ แล้วถูกอ้างผิดหน้า)"""
    out: dict[str, dict[str, str]] = {}
    for c in chunks or []:
        src = c.get("source")
        if src not in _GUIDELINE_LABELS:
            continue
        content = c.get("content") or ""
        if not _ATB_DOSE_TOKEN_RE.search(content):
            continue
        page = str(c.get("page", "")).strip()
        if not page.isdigit():
            continue
        for name in _atb_in(content):
            out.setdefault(name, {}).setdefault(src, page)
    return out


def dual_guideline_refs(answer: str, drug_pages: dict[str, dict[str, str]]) -> str:
    """เติม [Ref] ของอีกเล่มให้บรรทัดขนาดยาปฏิชีวนะที่อ้างเล่มเดียว (เฉพาะยาที่ Context มีครบทั้งสองเล่ม)

    มองเป็น "บล็อก" ไม่ใช่บรรทัดเดียว เพราะโมเดลมักเขียนชื่อยาไว้บรรทัดหัวข้อ แล้วขนาดยา/บรรทัดคำนวณ
    อยู่บรรทัดย่อยถัดไป -> ต้องจับคู่ชื่อยากับบรรทัดที่มี [Ref] ให้ถูก
    """
    if not answer or not drug_pages:
        return answer
    both = {n: p for n, p in drug_pages.items() if "URI" in p and "AAFP" in p}
    if not both:
        return answer
    lines = answer.split(chr(10))
    out: list[str] = []
    for i, line in enumerate(lines):
        refs = {m.group(1).strip() for m in _LINE_REF_RE.finditer(line)}
        cited = {s for s, label in _GUIDELINE_LABELS.items() if label in refs or s in refs}
        if len(cited) != 1:
            out.append(line)
            continue
        names = _atb_in(line)
        has_dose = bool(_ATB_DOSE_TOKEN_RE.search(line))
        if not names:
            # ชื่อยาอาจอยู่บรรทัดหัวข้อด้านบน (ย้อนดูไม่เกิน 2 บรรทัดที่มีเนื้อหา) -- บรรทัดนี้ต้องมีขนาดยาเอง
            if has_dose:
                for prev in (l for l in reversed(lines[max(0, i - 2): i]) if l.strip()):
                    names = _atb_in(prev)
                    if names:
                        break
        elif not has_dose:
            # ชื่อยาอยู่บรรทัดนี้ แต่ขนาดยาอยู่บรรทัดถัดไป (บรรทัดคำนวณ)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            has_dose = bool(_ATB_DOSE_TOKEN_RE.search(nxt))
        if not names or not has_dose:
            out.append(line)
            continue
        have = next(iter(cited))
        missing_src = "URI" if have == "AAFP" else "AAFP"
        page = next((both[n][missing_src] for n in names if n in both), None)
        if not page:
            out.append(line)
            continue
        add = "[Ref: " + _GUIDELINE_LABELS[missing_src] + ", หน้า " + page + "]"
        last = line.rfind("]")
        out.append(line[: last + 1] + " " + add + line[last + 1:])
    return chr(10).join(out)


def mentions_antibiotic(text: str) -> bool:
    """(streaming) บรรทัดที่ยังไม่จบซึ่งมีชื่อยาปฏิชีวนะ ต้องรอให้จบบรรทัดก่อน จึงเติม [Ref] อีกเล่มได้"""
    return bool(_atb_in(text or ""))


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


# เด็กที่เพิ่งได้ amoxicillin มาภายใน 30 วัน -> ตาม AAFP TABLE 4 / URI เด็ก 2562 ต้องข้ามไปยาทางเลือกที่สอง
# (amoxicillin/clavulanate) ไม่ใช่ amoxicillin เดี่ยว -- เป็นจุดตัดสินใจที่พลาดแล้วผู้ป่วยได้ยาไม่ครอบคลุมเชื้อดื้อยา
_PRIOR_AMOX_RE = re.compile(
    r"(?:เคย(?:ได้|ใช้|กิน|ทาน|รับ)|ได้รับ|ใช้).{0,40}(?:amoxi|อะม็อก|อะมอก)"
    r"|(?:amoxi|อะม็อก).{0,40}(?:เดือนก่อน|ที่ผ่านมา|ภายใน\s*30\s*วัน|เมื่อเดือน)", re.IGNORECASE)


def prior_antibiotic_note(text: str, f: dict) -> str:
    """บันทึกเตือนเมื่อเคสเด็กมีประวัติได้ amoxicillin มาก่อนไม่นาน (เปลี่ยนยาตัวแรก)"""
    if not (f.get("ear") and _PRIOR_AMOX_RE.search(text or "")):
        return ""
    return ("**ผู้ป่วยมีประวัติได้รับ amoxicillin มาก่อนหน้านี้ (ภายใน ~30 วัน):** ตามตารางใน Context "
            "(AAFP TABLE 4 คอลัมน์ Children / URI เด็ก 2562) เคส AOM กลุ่มนี้ **ต้องข้ามไปใช้ยาทางเลือกที่สอง "
            "คือ Amoxicillin/clavulanate ไม่ใช่ Amoxicillin เดี่ยว** และต้องระบุขนาดเป็น mg/kg/day พร้อมคำนวณตามน้ำหนักจริง "
            "+ ระยะเวลา ให้ครบ (แม้จะเสนอแนวทางเฝ้าระวังอาการควบคู่ไปด้วยก็ต้องมีตัวเลขขนาดยา)")


def practice_flags(f: dict) -> list[str]:
    """เคสที่เข้าข่าย Expert Opinion (แนวปฏิบัติจริงไทย) -- ให้ LLM แสดงบล็อก 'ในทางปฏิบัติจริง' ต่อจากคำแนะนำ Guideline"""
    flags: list[str] = []
    if centor_scope(f)[0]:
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
    # สูตรผสม 3 ตัวยา -- ตารางใหม่ไม่ได้ระบุตัวยาไว้ในชื่อ แต่ต้องบอกให้ครบตาม feedback
    # (ห้ามเขียนว่ามีแค่ CPM + Phenylephrine เพราะมี Paracetamol ด้วย)
    (r"Decolgen(?:\s*prin)?|TIFFY(?:\s*DEY)?|ดีคอลเจน|ทิฟฟี่", "Paracetamol + Chlorpheniramine + Phenylephrine",
     ["paracetamol", "พาราเซตามอล"]),
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


def _is_synonym_paren(head: str, inner: str) -> bool:
    """วงเล็บนั้นเป็น 'ชื่อพ้องของยาตัวเดียวกัน' ไม่ใช่การบอกตัวยา (เช่น Acetylcysteine (N-Acetylcysteine))"""
    h, i = _nz(head), _nz(inner)
    return bool(h and i and (h in i or i in h))


def _clean_ingredient(text: str) -> str:
    ingr = re.sub(r"มีตัวยา", "", text or "")
    ingr = re.sub(r"\s*\d[\d.,-]*\s*(?:mg|มิลลิกรัม|ml|มิลลิลิตร)\b", "", ingr, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", ingr).strip(" +")


def _brand_entries() -> list[tuple["re.Pattern", str, list[str], bool]]:
    global _BRAND_ENTRIES
    if _BRAND_ENTRIES is not None:
        return _BRAND_ENTRIES
    derived: list[tuple[str, str]] = []   # (head, ingredient label)
    for d in load_formulary():
        # ตารางใหม่เขียนตัวยาไว้ในวงเล็บท้ายชื่อ (ไม่มีคำว่า "มีตัวยา" แล้ว) และหนึ่งแถวอาจมีหลายสูตร
        # เช่น "Strepsils chesty cough (Ambroxol) / Strepsils dry cough (Dextromethorphan)" -> เก็บทั้งสองสูตร
        for m in re.finditer(r"([A-Za-z][A-Za-z0-9 .\-+]*?)\s*\(([^)]*)\)", d["name"]):
            head, inner = m.group(1).strip(), m.group(2)
            if not head or _is_synonym_paren(head, inner):
                continue
            ingr = _clean_ingredient(inner)
            if ingr:
                derived.append((head, ingr))
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
            tail = new[m.end():]
            # ถ้าตามหลังชื่อการค้าเป็นวงเล็บ "รายการตัวยาที่ไม่ครบ" ให้แทนที่ทั้งวงเล็บ (ไม่ใช่ต่อท้ายซ้อน)
            # แต่ต้องไม่แตะวงเล็บที่เป็นข้อมูลอื่น เช่น "(สำหรับเด็ก 3 ปีขึ้นไป)"
            pm = re.match(r"\s*\(([^)]{0,120})\)", tail)
            label_words = [w.lower() for w in re.split(r"[+\s]+", label) if len(w) >= 5]
            if pm and ("+" in pm.group(1) or any(w in pm.group(1).lower() for w in label_words)):
                tail = tail[pm.end():]
            new = new[: m.end()] + f" (ตัวยา: {label})" + tail
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


def dose_drug_hits(text: str) -> list[tuple[int, int, str]]:
    """[(เริ่ม, จบ, เลขหน้า Dose)] ของยาที่พบในข้อความ (คีย์ยาวก่อน ไม่ซ้อนทับ) เรียงตามตำแหน่ง

    คืนตำแหน่งจบ (end) ด้วย -- ไม่ใช่แค่ตำแหน่งเริ่ม -- เพื่อให้ผู้เรียก (_last_drug_hit) รู้ความยาว
    ของคำที่ตรงแต่ละคำ และเลือกคำที่ "เจาะจงที่สุด" (ยาวสุด) ได้ ไม่ใช่แค่เรียงตามตำแหน่ง
    """
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, int, str]] = []
    for rx, page in _page_keys():
        for m in rx.finditer(text or ""):
            if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), m.end(), page))
    return sorted(hits)
