# -*- coding: utf-8 -*-
"""
Opt-8 validation suite: conversation drift / dual-guideline refs / mL calculation /
cold start / dose scaling — plus 12 newly generated flexible cases.

Usage (from project root):
    set PYTHONPATH=.
    python Jack_prompt/eval/opt8_suite.py
    python Jack_prompt/eval/opt8_suite.py --only D1,D2 --verbose
"""
import sys, io, time, argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from backend.rag_engine import generate_answer

# ── history fixtures ─────────────────────────────────────────────────────────
H_SINUS50 = [
    {"role": "user", "content": "อายุ 50 ปี ปวดหน้าผาก ปวดโหนกแก้มซ้าย น้ำมูกเหลืองข้น ไข้ 37.9 องศา มาได้ 11 วัน ไม่ได้ดีขึ้นเลยตั้งแต่ต้น ไม่มีประวัติแพ้ยา อยากได้ยา Penicillin V"},
    {"role": "assistant", "content": "เคสนี้เข้าได้กับ **Acute Bacterial Rhinosinusitis (ABRS)** (อาการ >10 วันไม่ดีขึ้น) แนะนำ first-line Amoxicillin/clavulanate 875/125 mg วันละ 2 ครั้ง 5-7 วัน [Ref: AAFP, หน้า 5]"},
]
H_AOM15 = [
    {"role": "user", "content": "เด็ก น้ำหนัก 15 kg ปวดหูข้างเดียว มีไข้ 38 องศา ไม่มีหนองไหลจากหู เคยได้ amoxicillin เดือนก่อน ไม่มีแพ้ยา"},
    {"role": "assistant", "content": "เข้าได้กับ AOM ครับ เนื่องจากได้ amoxicillin ภายใน 30 วัน แนะนำ Amoxicillin/clavulanate 80-90 mg/kg/day แบ่ง 2 ครั้ง -> น้ำหนัก 15 kg = 1,200-1,350 mg/day = ครั้งละ 600-675 mg [Ref: URI เด็ก 2562, หน้า 54] [Ref: AAFP, หน้า 6]"},
]
H_PHAR_CHILD = [
    {"role": "user", "content": "เด็ก 8 ขวบ 25 kg เจ็บคอมาก ไข้ 39 ไม่ไอ ทอนซิลมีหนอง ต่อมน้ำเหลืองโต ไม่แพ้ยา"},
    {"role": "assistant", "content": "Centor สูง เข้าได้ GABHS pharyngitis แนะนำ Amoxicillin 50 mg/kg/day (max 1 g) -> 25 kg = 1,000 mg/day นาน 10 วัน [Ref: URI เด็ก 2562, หน้า 24]"},
]

CASES = [
    # ── D: เคสปัญหาจาก feedback opt-8 โดยตรง ──────────────────────────────
    dict(id="D1",  # drift: follow-up ไม่ระบุโรค ต้องอยู่กับ ABRS ผู้ใหญ่ ไม่หลุดไป AOM เด็ก
         history=H_SINUS50,
         q="ปัจจัยเสี่ยงที่ทำให้เกิดโรคมีอะไรบ้าง",
         any_of=["ไซนัส", "rhinosinusitis", "ABRS"],
         must_not_contain=["หูชั้นกลาง", "AOM", "ฝีหลังคอหอย", "Retropharyngeal", "จุกนม", "สถานเลี้ยงเด็ก"]),
    dict(id="D2",  # dual-guideline: AOM เด็ก ต้องอ้างทั้ง URI เด็ก และ AAFP
         q="น้ำหนัก 15 kg ปวดหูข้างเดียว มีไข้ 38 องศา ไม่มีหนองไหลจากหู เคยได้ amoxicillin เดือนก่อน ไม่มีแพ้ยา ไม่สะดวกสังเกตอาการ",
         must_contain=["URI เด็ก", "AAFP", "80", "90"]),
    dict(id="D3",  # mL step-by-step พร้อมที่มาความแรง
         q="เด็ก 5 ขวบ น้ำหนัก 18 kg แพทย์ยืนยัน GABHS pharyngitis ไม่แพ้ยา ที่ร้านมียาน้ำ amoxicillin 250 mg/5 mL ช่วยคำนวณปริมาณยาต่อครั้งเป็น mL ให้หน่อย",
         must_contain=["50", "900"], any_of=["mL", "มล"]),
    dict(id="D4", intent="noise", lat_max=1.0,  # cold start: พิมพ์มั่ว ต้องตอบทันที
         q="sdfdsf",
         must_not_contain=["Ref:", "การวินิจฉัย"]),
    dict(id="D4b", intent="noise", lat_max=1.0,
         q="ทดสอบๆ",
         must_not_contain=["Ref:"]),
    dict(id="D5",  # dose scaling follow-up: 15 kg -> 20 kg ต้องคำนวณใหม่
         history=H_AOM15,
         q="ถ้าน้ำหนักเด็ก 20 kg ล่ะ",
         must_contain=["1,600", "1,800"]),
    dict(id="D6", intent="smalltalk", lat_max=1.0,  # cold start: ทักทายแชทใหม่ ต้องตอบทันที
         q="ดีจ้า",
         must_not_contain=["Ref:"]),
    dict(id="D7",  # drift: follow-up เรื่องภาวะแทรกซ้อน ต้องคงโรคเดิม (pharyngitis เด็ก)
         history=H_PHAR_CHILD,
         q="ภาวะแทรกซ้อนที่ต้องระวังมีอะไรบ้าง",
         any_of=["rheumatic", "รูมาติก", "ฝีรอบทอนซิล", "peritonsillar", "glomerulonephritis", "ไต", "คออักเสบ", "ทอนซิล"],
         must_not_contain=["ไซนัสอักเสบเฉียบพลันในผู้ใหญ่"]),

    # ── G: 12 เคสใหม่ (generate เพิ่ม ยืดหยุ่นกว่าชุดเดิม) ─────────────────
    dict(id="G1",  # AOM เด็กใหม่ ไม่เคยได้ยา -> first-line amoxicillin + dual ref
         q="เด็กชาย 6 ขวบ น้ำหนัก 20 kg ปวดหูขวา ไข้ 38.5 มา 2 วัน ร้องกวนตอนกลางคืน ไม่เคยได้ยาปฏิชีวนะมาก่อน ไม่แพ้ยา",
         must_contain=["Amoxicillin"], any_of=["URI เด็ก", "AAFP"]),
    dict(id="G2",  # pharyngitis ผู้ใหญ่ครบเกณฑ์
         q="ผู้หญิง 28 ปี เจ็บคอมาก ไข้ 38.9 ไม่ไอ ต่อมน้ำเหลืองที่คอโตกดเจ็บ ทอนซิลมีหนอง มา 2 วัน ไม่แพ้ยา",
         must_contain=["Centor", "Penicillin V", "10 วัน"]),
    dict(id="G3",  # ABRS ผู้ใหญ่ แพ้ penicillin type 1
         q="ชาย 60 ปี น้ำมูกข้นเขียว ปวดโหนกแก้ม 12 วันไม่ดีขึ้น ไข้ต่ำๆ แพ้ penicillin เป็นผื่นลมพิษทั้งตัว",
         any_of=["Doxycycline", "Levofloxacin", "Moxifloxacin"],
         must_not_contain=["จ่าย Amoxicillin/clavulanate ได้เลย"]),
    dict(id="G4",  # AOM เด็กเล็ก <2 ปี -> 10 วัน
         q="เด็ก 18 เดือน น้ำหนัก 11 kg ไข้ 39 งอแง ดึงหูทั้งสองข้าง มา 1 วัน ไม่แพ้ยา",
         must_contain=["10 วัน"]),
    dict(id="G5",  # เด็กเล็ก <4 ปี ขอยาแก้ไอ -> ปฏิเสธ
         q="เด็ก 3 ขวบครึ่ง ไอแห้งๆ น้ำมูกใส 2 วัน แม่จะซื้อยาแก้ไอน้ำเชื่อมให้",
         any_of=["4 ปี", "ต่ำกว่า 4", "ไม่แนะนำ"]),
    dict(id="G6",  # laryngitis ผู้ใหญ่ -> ไม่ต้อง ATB
         q="ผู้ชาย 40 ปี เสียงแหบมา 5 วันหลังไปเชียร์บอลตะโกนเยอะ ไม่มีไข้ ไม่เจ็บคอมาก กลืนได้ปกติ",
         any_of=["ไม่จ่าย", "ไม่แนะนำ", "ไม่จำเป็น", "พักเสียง", "ใช้เสียง", "ไวรัส"]),
    dict(id="G7",  # ขอ azithromycin ไม่มีข้อบ่งชี้ -> stewardship (Centor optional: ไอ+น้ำมูกเด่น
         # = viral-dominant ตามนโยบาย 6.1 ใช้เหตุผลไวรัสแทนได้)
         q="หญิง 33 ปี เจ็บคอเล็กน้อย ไอ มีน้ำมูก 2 วัน ขอ azithromycin ชุดละ 3 เม็ดที่เคยกินแล้วหาย",
         must_contain=["ไวรัส"], any_of=["ไม่ควรจ่าย", "ไม่แนะนำ", "ไม่จ่าย"]),
    dict(id="G8",  # pharyngitis เด็ก แพ้ amoxicillin anaphylaxis -> non-beta-lactam
         q="เด็กหญิง 10 ขวบ 30 kg เจ็บคอ ทอนซิลมีหนอง ไข้ 38.6 ไม่ไอ ต่อมน้ำเหลืองโต เคยแพ้ amoxicillin แบบ anaphylaxis",
         any_of=["Clindamycin", "Azithromycin"],
         must_not_contain=["แนะนำ Cephalexin"]),
    dict(id="G9",  # mL calculator + สรุปตาราง
         q="เด็ก AOM น้ำหนัก 12 kg ไม่แพ้ยา ไม่เคยได้ยาปฏิชีวนะ ร้านมี amoxicillin ยาน้ำ 125 mg/5 mL กับ 250 mg/5 mL ต้องให้กี่ mL ต่อครั้ง",
         must_contain=["mL"], any_of=["960", "1,080", "480", "540"]),
    dict(id="G10",  # scale เด็ก -> ผู้ใหญ่
         history=H_PHAR_CHILD,
         q="ถ้าเป็นผู้ใหญ่อายุ 30 ปีล่ะ ใช้ขนาดเท่าไร",
         any_of=["500", "1,000"], must_contain=["10 วัน"]),
    dict(id="G11",  # drift ซ้อน 2 ชั้น: ถามความรู้ต่อจากเคสหวัดเด็ก
         history=[
             {"role": "user", "content": "เด็กหญิง 8 ปี น้ำหนัก 27 kg น้ำมูกเหลือง ไอ คัดจมูก มา 4 วัน ไม่มีไข้สูง"},
             {"role": "assistant", "content": "เข้าได้กับหวัด (common cold) จากไวรัส ยังไม่เข้าเกณฑ์ ABRS แนะนำรักษาตามอาการ [Ref: URI เด็ก 2562, หน้า 16]"},
         ],
         q="โรคนี้ติดต่อกันได้ทางไหน",
         any_of=["หวัด", "ไวรัส", "สัมผัส", "ละออง"],
         must_not_contain=["หูชั้นกลาง", "ฝีหลังคอหอย"]),
    dict(id="G12",  # นอกขอบเขต
         q="ผู้ป่วยปวดแสบลิ้นปี่หลังกินอาหาร สงสัยกรดไหลย้อน แนะนำยาอะไรดี",
         must_contain=["นอกขอบเขต"]),
]


def _norm(s: str) -> str:
    return "".join(str(s).lower().split()).replace(",", "")


def run_case(c: dict, verbose: bool) -> tuple[bool, float, list[str]]:
    t0 = time.time()
    r = generate_answer(c["q"], history=c.get("history"))
    dt = time.time() - t0
    ans = r.get("answer", "")
    na = _norm(ans)
    fails: list[str] = []

    if c.get("intent") and r.get("intent", "clinical") != c["intent"]:
        fails.append(f"intent={r.get('intent','clinical')}!={c['intent']}")
    if c.get("lat_max") and dt > c["lat_max"]:
        fails.append(f"latency:{dt:.2f}s>{c['lat_max']}s")
    for kw in c.get("must_contain", []):
        if _norm(kw) not in na:
            fails.append(f"missing:{kw}")
    for kw in c.get("must_not_contain", []):
        if _norm(kw) in na:
            fails.append(f"forbidden:{kw}")
    if c.get("any_of"):
        if not any(_norm(k) in na for k in c["any_of"]):
            fails.append("any_of:none-of[" + "/".join(c["any_of"]) + "]")

    if verbose:
        print("\n" + "=" * 80 + f"\n[{c['id']}] {c['q'][:70]}\n" + "-" * 80)
        print(ans)
    return (not fails), dt, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    cases = [c for c in CASES if not only or c["id"] in only]

    passed, lat = 0, []
    print(f"Running {len(cases)} opt-8 cases...\n")
    for c in cases:
        ok, dt, fails = run_case(c, args.verbose)
        lat.append(dt)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {c['id']:5s} {dt:5.1f}s  {'' if ok else '<- ' + ', '.join(fails)}")
        if ok:
            passed += 1

    n = len(cases)
    print("\n" + "=" * 60)
    print(f"ACCURACY: {passed}/{n} ({100*passed//max(n,1)}%)")
    if lat:
        print(f"LATENCY : avg {sum(lat)/len(lat):.1f}s | max {max(lat):.1f}s | min {min(lat):.1f}s")
    sys.exit(0 if passed == n else 1)


if __name__ == "__main__":
    main()
