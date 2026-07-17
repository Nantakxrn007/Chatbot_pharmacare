# -*- coding: utf-8 -*-
"""
Regression suite for PharmaCare RAG optimization (opt6/opt7).

Runs a fixed set of cases through generate_answer and asserts keyword-level
expectations (drug names / dose numbers / question-type / no context-bleeding),
then reports accuracy + latency. Run this after ANY prompt/retrieval change.

Usage:
    set PYTHONPATH=<project root>   (or run from project root)
    python Jack_prompt/eval/regression_suite.py
    python Jack_prompt/eval/regression_suite.py --only R2,N4     # subset
    python Jack_prompt/eval/regression_suite.py --verbose        # print answers

A case passes when:
  - every string in `must_contain`     appears in the answer, AND
  - no string in `must_not_contain`    appears in the answer, AND
  - if `any_of` given, at least ONE of its strings appears (OR), AND
  - intent (smalltalk/meta/clinical) matches `intent` if given.
Matching is case-insensitive and ignores spaces/commas so "500 mg" == "500mg".
"""
import sys, io, time, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from backend.rag_engine import generate_answer

# ── prior-case history fixtures (for bleeding / follow-up tests) ──────────────
H_ADULT_PHARYNGITIS = [
    {"role": "user", "content": "ผู้ป่วยอายุ 20 ปี น้ำหนัก 70 kg ไข้สูง 39.8 ไม่ไอ ทอนซิลบวมแดง pharyngitis ไม่แพ้ยา"},
    {"role": "assistant", "content": "เคสผู้ป่วยใหม่ (ประเภท 2) GABHS จ่าย Amoxicillin 10 วัน [Ref: AAFP, หน้า 5]"},
]
H_SINUS = [
    {"role": "user", "content": "ผู้ใหญ่ 40 ปี น้ำมูกเหลืองข้น ปวดโหนกแก้ม มา 4 วัน สงสัยไซนัส"},
    {"role": "assistant", "content": "เคสผู้ป่วยใหม่ (ประเภท 2) viral rhinosinusitis <10 วัน ยังไม่ต้อง ATB [Ref: AAFP, หน้า 5]"},
]

# ── cases ────────────────────────────────────────────────────────────────────
CASES = [
    # id, input, [assertions...]
    dict(id="C1", intent="smalltalk", history=H_ADULT_PHARYNGITIS,
         q="ดีมากตอบได้ดี",
         must_not_contain=["Amoxicillin", "การวินิจฉัย", "Ref:"]),
    dict(id="C2",
         q="ผู้ป่วยอายุ 20 ปี น้ำหนัก 70 kg ไข้สูง 39.8 ไม่ไอ ต่อมทอนซิลบวมแดง ต่อมน้ำเหลืองโต หมอวินิจฉัย pharyngitis ไม่แพ้ยา",
         must_contain=["Penicillin V", "250", "Amoxicillin", "1,000", "10 วัน", "Centor"]),
    dict(id="C3",
         q="เด็กหญิงอายุ 4 ขวบ มาพร้อมอาการไอ เจ็บคอ",
         must_contain=["ประเภท 4"], must_not_contain=["จ่าย Amoxicillin", "Azithromycin"]),
    dict(id="C4", history=H_ADULT_PHARYNGITIS,
         q="อีกเคส ไข้ 39 ไม่ไอ คอบวมแดงมาก เจ็บคอมา 3 วัน แพ้ยา pencillin",
         must_not_contain=["อายุ 20"]),
    dict(id="C4b", intent="smalltalk", history=H_ADULT_PHARYNGITIS,
         q="เดี๋ยวมีอีกเคสแป๊ป",
         must_not_contain=["ฝีหลังคอหอย", "Retropharyngeal", "การวินิจฉัย", "คอแข็ง"]),
    dict(id="C6",
         q="เด็ก น้ำหนัก 15 kg ปวดหูข้างเดียว มีไข้ 38 ไม่มีหนองไหลจากหู เคยได้ amoxicillin เดือนก่อน",
         # daily total 1,200-1,350 is split-independent (BID->600/675, TID->400/450); assert that + range
         must_contain=["80", "90", "1,200", "1,350"], must_not_contain=["ขอบบน", "ขอบล่าง"]),
    dict(id="C7",
         q="เด็ก 3 ขวบ ไข้ น้ำมูกใส แม่ขอยาแก้อักเสบ",
         must_contain=["ปฏิชีวนะ", "ไวรัส"],
         any_of=["ไม่จ่าย", "ไม่แนะนำ", "ไม่ใช่ยาปฏิชีวนะ", "ยาแก้อักเสบ"]),
    dict(id="C8",
         q="อายุ 50 ปี ปวดหน้าผาก ปวดโหนกแก้มซ้าย น้ำมูกเหลืองข้น ไข้ 37.9 มา 11 วันไม่ดีขึ้น ไม่แพ้ยา อยากได้ Penicillin V แนะนำยาพ่นจมูก steroid ด้วย",
         must_contain=["Augmentin", "110 mcg"]),
    dict(id="C9",
         q="ผู้ป่วยต้องการซื้อยา ciprofloxacin เพราะอ่านมาว่าใช้รักษาแล้วหาย",
         must_contain=["ประเภท 4"], any_of=["ciprofloxacin", "fluoroquinolone"]),
    dict(id="F1", history=H_SINUS,
         q="แล้วเมื่อไหร่ต้องเริ่มให้ยาปฏิชีวนะ",
         must_contain=["10 วัน"], must_not_contain=["หูชั้นกลาง", "AOM"]),
    dict(id="N2",
         q="เด็ก 8 ขวบ 25 kg เจ็บคอมาก ไข้ 39 ไม่ไอ ทอนซิลมีหนอง ต่อมน้ำเหลืองโต ไม่แพ้ยา",
         must_contain=["Amoxicillin", "50", "Centor"]),
    dict(id="N4",
         q="ผู้ชาย 35 ปี เจ็บคอ ไข้สูง ไม่ไอ ทอนซิลบวมแดง ต่อมน้ำเหลืองโต แพ้ penicillin แบบ anaphylaxis",
         must_contain=["Clindamycin", "300", "Azithromycin", "500", "beta-lactam"]),
    dict(id="N5",
         q="เด็ก 5 ขวบ ไข้สูง เจ็บคอมาก น้ำลายไหล พูดเสียงอู้อี้ นั่งเอนตัวไปข้างหน้า หายใจลำบาก",
         any_of=["ER", "ฉุกเฉิน", "โรงพยาบาล", "พบแพทย์ทันที", "ส่งต่อ"]),
    dict(id="N7",
         q="ผู้ป่วยเบาหวาน น้ำตาล 250 ควรปรับยาอย่างไร",
         must_contain=["นอกขอบเขต"]),
    dict(id="N8",
         q="ลูก 2 ขวบ ไอมีเสมหะ ขอยาแก้ไอละลายเสมหะ",
         any_of=["4 ปี", "ต่ำกว่า 4"]),
    dict(id="N11",
         q="เด็ก 7 ขวบ 22 kg เจ็บคอ ไข้ ไม่ไอ ทอนซิลบวม ต่อมน้ำเหลืองโต เคยแพ้ amoxicillin เป็นผื่นแดงเล็กน้อยไม่บวม",
         must_contain=["Cephalexin"]),
    dict(id="N12",
         q="ผู้หญิง 45 ปี ไซนัสอักเสบ 12 วันไม่ดีขึ้น น้ำมูกเขียว ปวดหน้า แพ้ penicillin เป็นผื่นลมพิษ",
         any_of=["Doxycycline", "Cefixime", "Levofloxacin"]),
    dict(id="O1",  # opt7: sore throat with ATB request, missing age/cough -> must ask history + Centor
         q="คนไข้เจ็บคอมาก กลืนน้ำลายเหมือนมีหนามแทง ไข้สูง 38.8 ทอนซิลโตบวมแดงมีจุดขาว แนะนำยาปฏิชีวนะให้หน่อย",
         must_contain=["Centor"], any_of=["อายุ", "มีไอ", "ซักประวัติ", "ข้อมูลเพิ่มเติม", "ต่อมน้ำเหลือง"]),
    dict(id="O2",  # opt7: cold + ATB request adult -> Centor <2, viral, OTC + antihistamine
         q="ชายอายุ 32 ปี น้ำมูกใส ไอ จาม คัดจมูก 2 วัน ไม่มีไข้สูง ไม่เจ็บคอ ขอยาแก้หวัดและขอยาปฏิชีวนะด้วย",
         must_contain=["Centor"], any_of=["ไม่จ่าย", "ไวรัส", "หวัด"]),
    dict(id="O3",  # opt7: out-of-scope back pain
         history=H_ADULT_PHARYNGITIS,
         q="เคสลุงป๊อด เจ็บหลังส่วนล่างนานกว่า 7 วัน คิดว่าไง",
         must_contain=["นอกขอบเขต"]),
    dict(id="O4",  # opt7: child 8yo cold-like -> Centor shown + 7-day sinusitis watch
         q="เด็กหญิงอายุ 8 ปี น้ำหนัก 27 kg น้ำมูกเหลือง ไอ คัดจมูก มา 4 วัน ไม่มีกดเจ็บที่ใบหน้า ไม่มีไข้สูง",
         must_contain=["Centor"], any_of=["7 วัน", "ไซนัส"]),
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
    print(f"Running {len(cases)} cases...\n")
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
