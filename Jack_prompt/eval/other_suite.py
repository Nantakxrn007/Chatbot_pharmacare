# -*- coding: utf-8 -*-
"""
other_optimize validation suite.

Scope of THIS round: classification + Context Management only
(the dual-guideline DURATION change was reverted at user's request -- not tested here).
  -> combined type labeling (ประเภท 2 AND ประเภท 4),
     follow-ups bind to the newest case (no bleeding from an earlier case),
     history questions always carry a clinical reason,
     flexibility to return to the earlier case on explicit request.

Usage (from project root):
    set PYTHONPATH=.
    python Jack_prompt/eval/other_suite.py
    python Jack_prompt/eval/other_suite.py --only T4 --verbose
"""
import sys, io, time, argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from backend.rag_engine import generate_answer

# ── history fixtures ─────────────────────────────────────────────────────────
# Case 1 = adult sinusitis (complete). Case 2 = a NEW child ear-pain case.
Q1_SINUS = "ผู้ใหญ่ 50 ปี น้ำมูกเหลืองข้น ปวดโหนกแก้มซ้าย ไข้ 37.9 มา 11 วันไม่ดีขึ้น ไม่แพ้ยา"
A1_SINUS = ("เข้าได้กับ **Acute Bacterial Rhinosinusitis (ABRS)** (อาการ >10 วันไม่ดีขึ้น) "
            "first-line Amoxicillin/clavulanate (Augmentin) 875/125 mg วันละ 2 ครั้ง 5-7 วัน [Ref: AAFP, หน้า 5]")

# case 2 whose symptoms MAP to a disease (ปวดหู -> AOM)
Q2_EAR = "เด็กปวดหูข้างเดียว มีไข้"
A2_EAR = ("เด็กหญิง/ชาย มีอาการปวดหูข้างเดียวและมีไข้ (ยังไม่ทราบ: ระยะเวลา, ประวัติแพ้ยา, อายุ, น้ำหนัก) "
          "เพื่อการประเมินที่ปลอดภัย รบกวนขอข้อมูลเพิ่มเติมครับ")

# case 2 whose symptoms do NOT map to any anchor disease (เจ็บคอ/ไข้ ไม่อยู่ใน anchor map)
Q2_NONMAP = "เด็ก 5 ขวบ เจ็บคอ มีไข้ มา 2 วัน"
A2_NONMAP = ("เด็กชาย/หญิง 5 ขวบ เจ็บคอและมีไข้ (ยังไม่ทราบ: ประวัติแพ้ยา, น้ำหนัก, ลักษณะทอนซิล) "
             "รบกวนขอข้อมูลเพิ่มเติมครับ")

H_CASE1 = [{"role": "user", "content": Q1_SINUS}, {"role": "assistant", "content": A1_SINUS}]
H_C1_C2_EAR = H_CASE1 + [{"role": "user", "content": Q2_EAR}, {"role": "assistant", "content": A2_EAR}]
H_C1_C2_NONMAP = H_CASE1 + [{"role": "user", "content": Q2_NONMAP}, {"role": "assistant", "content": A2_NONMAP}]

CASES = [
    # ── (2a) history-taking reasons on multiple trigger types ───────────────
    dict(id="T2",  # pure type 4 (incomplete) -> questions must carry reasons
         q="เด็กหญิงอายุ 4 ขวบ มาพร้อมอาการไอ เจ็บคอ",
         must_contain=["ประเภท 4", "เพื่อ"],
         any_of=["กี่วัน", "ระยะเวลา", "มีไข้"]),
    dict(id="T3",  # type 5 drug request, no case info -> ask history WITH reasons
         q="ผู้ป่วยต้องการซื้อยา ciprofloxacin เพราะอ่านมาว่าใช้รักษาแล้วหาย",
         must_contain=["เพื่อ"],
         any_of=["ciprofloxacin", "fluoroquinolone", "อาการ"]),

    # ── (2b) combined multi-type label + context management ─────────────────
    dict(id="T4",  # new patient in existing chat, still incomplete -> label 2 AND 4
         history=H_CASE1,
         q="อีกเคส เด็กปวดหูข้างเดียว มีไข้",
         must_contain=["ประเภท 2", "ประเภท 4"],
         must_not_contain=["Augmentin", "โหนกแก้ม"]),
    dict(id="T5",  # follow-up on newest case (mappable) -> no bleeding to case 1
         history=H_C1_C2_EAR,
         q="แล้วถ้าต้องให้ยาปฏิชีวนะ ควรกินนานกี่วัน",
         must_not_contain=["ไซนัส", "Augmentin", "โหนกแก้ม", "50 ปี"],
         any_of=["หู", "AOM", "Amoxicillin"]),
    dict(id="T6",  # follow-up on newest case (NON-mappable symptoms) -> no bleeding
         history=H_C1_C2_NONMAP,
         q="แล้วต้องกินยานานแค่ไหน",
         must_not_contain=["ไซนัส", "Augmentin", "โหนกแก้ม", "50 ปี"]),
    dict(id="T7",  # flexibility: explicit return to case 1 still answerable
         history=H_C1_C2_EAR,
         q="กลับมาที่เคสผู้ใหญ่ 50 ปี ไซนัสอักเสบ ขนาด Augmentin เท่าไร",
         any_of=["Augmentin", "875", "clavulanate"],
         must_not_contain=["ปวดหู"]),
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
    print(f"Running {len(cases)} other_optimize cases...\n")
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
