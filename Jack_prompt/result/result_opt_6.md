# รายงานการ Optimize รอบที่ 6 (result_opt_6)

รอบนี้โฟกัสตาม Feedback ล่าสุดใน `optimize_6.md` (การทดสอบถาม-ตอบจริงบน UI) -- แก้ที่
**RAG Layer + Prompt + จัดการ Context/Intent** ล้วนๆ โดยยึดหลักเดิม:
**Vanilla RAG, ไม่แตะ chunk / vector / embedding / frontend** (ดึงมาตรวจสอบ read-only เท่านั้น)

> ยืนยันขอบเขต: ไม่รัน `pipeline.py`, ไม่ re-embed, ไม่แก้ `rag/qdrant_db/`, ไม่แก้ `frontend/`
> ทุกการเปลี่ยนแปลงอยู่ที่ query-time + generation ใน `backend/rag_engine.py`, `backend/config.py`
> และแก้บั๊ก token accounting ใน `backend/main.py` (ไม่แตะ business logic อื่น)

---

## 0. สรุปผู้บริหาร (Executive Summary)

| ปัญหาหลักจาก Feedback รอบ 6 | สาเหตุที่แท้จริง | สิ่งที่แก้ | ผล (วัดจริง) |
|---|---|---|---|
| พิมพ์ "ดีมากตอบได้ดี"/"ขอบคุณ" แล้วบอทตอบเคสเดิมซ้ำ + หลอน | ทุกข้อความไป retrieve + ยัด history เคสเดิม (Context Bleeding) | **Intent/Retrieval Gate** (rule-based 0ms) -> social ข้าม RAG ตอบสั้น | ตอบ "ยินดีครับ..." ไม่แตะเคสเดิม (chunks=0) |
| เคสใหม่ในแชทเดิม หลอนอายุ (สรุป "อายุ 20") + อ้าง Guideline ผิดวัย | history เคสก่อนรั่วเข้าเคสใหม่ | Prompt กฎ 6 (anti cross-case bleeding) + ประเภท 3 (ผู้ป่วยใหม่ในแชท) | ไม่หลอนอายุ, ระบุชัด "เคสนี้ยังไม่ระบุอายุ" |
| Dose first-line ไม่ครบ (บอก 10 วัน แต่ตัวเลข Penicillin V/Amox หาย) | LLM สรุปย่อ "ตามขนาดมาตรฐาน" ทั้งที่ตารางมีใน Context | Prompt 3a: ยกตาราง first-line "ครบทุกตัวเลือก + ตัวเลขเป๊ะ" | ได้ PenV 250/500 + Amox 1000/500 x10 วัน ครบ |
| เคสข้อมูลไม่ครบ ยังตอบเต็ม 5 ขั้น แล้วเอาซักประวัติไว้ล่างสุด | routing ประเภท 2 vs 4 ไม่คม | เกณฑ์ตัดสิน 2/4 (ขาด >=2 ข้อ = ประเภท 4) + ประเภท 4 ซักประวัตินำ | เด็ก 4 ขวบ ไอเจ็บคอ -> ประเภท 4 ซักประวัติขึ้นก่อน สั้น |
| การวินิจฉัยไม่มี ranking ว่าน่าจะเป็นโรคอะไร | prompt ไม่ได้สั่ง | ประเภท 2 ขั้น 2: Differential ranked (เลข + สูง/กลาง/ต่ำ + เหตุผล + ref) | ทุกเคสมี "1. โรค X โอกาสสูง ... 2. โรค Y โอกาสต่ำ ..." |
| Dose คำนวณแค่ขอบบน (90) ไม่คำนวณช่วง | prompt ไม่ได้บังคับ Min AND Max | กฎ 4: คำนวณทั้งขอบล่าง+บน | 15 kg: 80x15=1200->600, 90x15=1350->675 mg |
| เคสแพ้ยา (ผู้ใหญ่) ตอบยาทางเลือกไม่มีขนาด | ตารางขนาดยาทางเลือก (AAFP p6) embed rank ต่ำ หลุด top_k | **Dose-table-aware pass** + PER_SOURCE_TOP_K 8->12 | ได้ Clindamycin 300 x3 x10 วัน / Azithromycin 500 x5 วัน |
| รายละเอียดเชิงลึกหาย เช่น สเปรย์ (110 mcg) | prompt ไม่ย้ำเก็บ detail ในวงเล็บ | กฎ 7: เก็บ detail วงเล็บ/ความแรง | Triamcinolone "พ่น 2 สเปรย์ (110 mcg) แต่ละข้าง" |
| ถามเจาะจง (เมื่อไหร่ให้ ATB) แต่ตอบภาพรวมโรคอื่น | prompt ไม่บังคับ focused answer | ประเภท 3: Focused Answer (ตอบโรคที่ถามโรคเดียว) | ตอบเกณฑ์ ABRS อย่างเดียว ไม่ยกโรคอื่น |
| องค์ประกอบอ้างอิงนอกคู่มือยาว/ซ้ำคำ "นอกเอกสาร" | label ยาว + heading ซ้ำ | `_clean_external_label` ตัด URL+คำกำกับ, heading="" | เหลือ "DrugBank Online, Ciprofloxacin" |
| 503 high demand + `cannot access local variable 'prompt_tokens'` | ไม่มี retry + ตัวแปร token ไม่ init | `_call_with_retry` (retry 503) + init token ใน `main.py` | retry ผ่าน, ไม่มี UnboundLocalError |

**ผลตรวจสอบรวม:** เคสจาก Feedback รอบ 6 (C1-C10) + Follow-up (2) + Fresh/Edge/Negative (N1-N12) +
Unit test (intent 15/15, retry 3/3) = **ถูกต้องเชิงคลินิก 24/24 เคส LLM**, dose ครบ, อ้างอิงในช่วงจริง,
ไม่หลอน/ไม่ bleeding

---

## 1. Flow ที่ทำ (ตามโจทย์)

1. อ่าน `optimize_6.md` + `result_opt_5.md` + memory (ขอบเขต no-vector-edit, Vanilla RAG)
2. อ่านโค้ดจริง (`rag_engine.py`, `config.py`, `main.py`, `patient_group.py`) เข้าใจ pipeline ปัจจุบัน
3. ยืนยัน root cause ของแต่ละปัญหา ด้วยการ retrieve/inspect chunk จริง (read-only)
   - พบว่าตาราง first-line (PenV/Amox) และตารางทางเลือกแพ้ยา (Clindamycin/Azithromycin) **มีใน Context จริง**
     แต่ (ก) LLM สรุปย่อ, (ข) ตารางทางเลือกแพ้ยาผู้ใหญ่ (AAFP p6) มัก embed rank ~9 หลุด top_k
4. Implement (RAG + prompt + bugfix) แบบ deterministic, วัด offline ได้บางส่วน
5. Validate live (Gemini gemini-3.1-flash-lite) ทุกเคส + regression
6. เขียนรายงานนี้

---

## 2. Best Solution -- ออกแบบและเหตุผล

### 2.1 Intent / Retrieval Gate (แก้ Context Bleeding -- Impact สูงสุด)

**ปัญหา:** หลังบอทตอบเคสไปแล้ว ผู้ใช้พิมพ์ "ดีมากตอบได้ดี"/"ขอบคุณ"/"โอเค" -> เดิมข้อความนี้ถูกส่งเข้า
pipeline เต็ม (embed -> retrieve -> LLM) พร้อม history เคสเดิม -> retriever ดึง chunk เดิมกลับมา ->
LLM ตอบเคสเดิมซ้ำ + เริ่มหลอน (Retrieval over-triggering + Context bleeding)

**แก้ (`classify_message_intent`, rule-based, ~0ms):**
- จำแนกข้อความล่าสุดเป็น `smalltalk` / `meta_types` / `clinical` ก่อนเข้า RAG
- `smalltalk` (ทักทาย/ขอบคุณ/ชม/รับทราบ/หัวเราะ): **ข้าม retrieval + ไม่ส่ง history เคสเดิม** ->
  ตอบสั้นด้วย LLM call เดียวแบบเบา (`_light_reply`) ที่ห้ามพูดถึงเคส/ยา/อ้างอิง
- `meta_types` (ถามว่า "ระบบแยกประเภทคำถามด้วยอะไร"): อธิบายสรุประบบจำแนกคำถาม (โชว์ Guideline จริง)
- **Conservative:** ถ้ามีสัญญาณคลินิกใดๆ (ยา/อาการ/อายุ/คำถาม ฯลฯ) = `clinical` เสมอ (กันประสิทธิภาพตก)
  ทดสอบ 15/15 เคส แยกถูก (รวมเคสหลอกอย่าง "ขอบคุณครับ แล้วถ้าเด็กแพ้ยาล่ะ" = clinical)
- knob: `INTENT_GATE=on|off`

### 2.2 Dose-table-aware Pass (แก้ dose ทางเลือกแพ้ยา/ตารางไม่ถูกดึง)

**ปัญหา:** ตารางขนาดยา (โดยเฉพาะ "ทางเลือกเมื่อแพ้ penicillin" ผู้ใหญ่ AAFP หน้า 6 = Clindamycin 300/
Azithromycin 500) เป็น HTML/ตัวเลขปน -> embedding ได้ similarity ต่ำ (rank ~9) -> เมื่อ `PER_SOURCE_TOP_K=8`
มัน **ไม่เข้า candidate pool เลย** -> LLM ตอบขนาดยาทางเลือกไม่ได้ เลยเบี่ยงไปบอก "ไปเปิดตารางเอง"

**แก้ (query-time, ไม่ยิง vector เพิ่ม, ไม่แตะ store):**
1. `PER_SOURCE_TOP_K` 8 -> 12 : ให้ตารางที่ rank ~9-10 ยังติดใน candidate pool
2. `_inject_relevant_tables`: ดึง candidate ที่ **เป็นตารางของไกด์ไลน์ (AAFP/URI)** และ retrieve ติดมาแล้ว
   ด้วย similarity >= floor (0.60) กลับเข้ามาเป็น context เสมอ แม้ถูก rank ออกจาก top_k
   (ตาราง Dose เว้นไว้ เพราะถูกดึงผ่าน drug-row expansion อยู่แล้ว -> กันแผงอ้างอิงรก)
- ผล (วัดจริง ยืนยัน 2 รอบต่อ query): เคสแพ้ยาผู้ใหญ่ -> Clindamycin/Azithromycin เข้ามาสม่ำเสมอ,
  อ้างอิง AAFP หน้า 6 ถูกต้อง; เคส first-line -> PenV 250/Amox 1000 ครบ

### 2.3 Prompt Layer (SYSTEM_PROMPT)

| จุดที่แก้/เพิ่ม | แก้ปัญหา Feedback |
|---|---|
| กฎ 4 (เสริม): คำนวณ dose ทั้งขอบล่าง+ขอบบน (Min AND Max) + ระยะเวลาจำเพาะตามอายุ/ความรุนแรง | dose คำนวณแค่ขอบบน (90) |
| กฎ 6 (เสริม): anti cross-case bleeding -- "อีกเคส/เคสใหม่" = ผู้ป่วยคนละคน ห้ามยกอายุ/นน. เคสเดิม | หลอน "อายุ 20" ในเคสใหม่ |
| กฎ 7 (เสริม): เก็บ detail เชิงลึกในวงเล็บ/ความแรง (เช่น 110 mcg) | รายละเอียด (110 mcg) หาย |
| Ref กฎ 1 (เสริม): เกณฑ์ห้ามข้ามวัยยึด "เนื้อหา" ไม่ใช่ชื่อเล่ม (AAFP มีเนื้อหาเด็กด้วย) | เด็ก vs เด็ก, ผู้ใหญ่ vs ผู้ใหญ่ |
| Ref กฎ 6 (เสริม): เทียบได้เฉพาะกลุ่มวัยเดียวกัน + รูปแบบ "หากอ้างอิงตาม X จะได้... คำนวณจาก..." + เอาที่เหมาะสุดขึ้นก่อน | dual-guideline เด็ก AAFP vs URI |
| ประเภท 2 (เสริม): เกณฑ์ตัดสิน 2 vs 4 (ขาดข้อมูล >=2 อย่าง -> ประเภท 4) | ข้อมูลไม่ครบยังตอบเต็ม 5 ขั้น |
| ประเภท 2 ขั้น 2 (เสริม): Differential ranked (เลข + สูง/กลาง/ต่ำ + เหตุผล + ref) | ไม่มี ranking โรค |
| ประเภท 2 ขั้น 3a (เสริม): ยกตาราง first-line "ครบทุกตัวเลือก + ตัวเลขเป๊ะ" ห้ามสรุปว่า "ตามขนาดมาตรฐาน" | dose first-line ไม่ครบ |
| ประเภท 3 (เสริม): Focused Answer (ตอบเฉพาะโรคที่ถาม) + ผู้ป่วยใหม่ในแชท (ถามอายุก่อนถ้าไม่รู้) | ตอบภาพรวมโรคอื่น + bleeding |
| ประเภท 4 (rewrite): ซักประวัติ "นำหน้า + สั้น" (ทวนเคส 1 บรรทัด + วงเล็บสิ่งที่ยังไม่ทราบ + ถาม) | ซักประวัติถูกดันไปล่างสุด |
| ประเภท 5 (เสริม): ขอยาแต่ไม่ให้อาการ -> ซักประวัติก่อน ไม่ปฏิเสธลอยๆ | เคส cipro ข้ามซักประวัติ |
| ANSWER FORMATTING (เสริม): ระบุประเภทเคสให้ **ตัวหนา** + จัดประเภทถูก | ประเภทไม่ highlight/ผิด |

### 2.4 Reliability (Latency-aware)

- **`_call_with_retry`**: retry เฉพาะ transient error (503 high demand / 5xx / overloaded) แบบ backoff สั้น
  (default 2 ครั้ง, 1.2s*n) -- **ไม่ retry 429 (quota)** เพราะไม่ช่วย; ใช้กับทั้ง answer (stream/non-stream)
  และ light reply. Unit test: recover ได้, non-transient ไม่เสีย retry
- **แก้บั๊ก `prompt_tokens`**: ใน `main.py` (3 handler: chat_stream / edit / regenerate) ตัวแปร
  `prompt_tokens`/`completion_tokens` ถูก assign เฉพาะใน `if type=='done'` -> ถ้า stream จบด้วย error
  (เช่น 503) จะเกิด `UnboundLocalError: cannot access local variable 'prompt_tokens'` ตอน `add_message`
  -> แก้โดย init = 0 ก่อน loop ทั้ง 3 จุด

### เทคนิค RAG ที่พิจารณา (เลือกเฉพาะที่ดีขึ้นจริง + ไม่ทำ latency แย่)

| เทคนิค | ตัดสินใจ | เหตุผล |
|---|---|---|
| Intent/Retrieval Gate (rule-based) | **ใช้ (เด่นรอบนี้)** | แก้ context bleeding ตรงจุด, ~0ms, กัน re-answer + เร็วกว่าเดิมบน turn ที่ไม่ใช่คลินิก |
| Dose-table-aware injection | **ใช้ (เด่นรอบนี้)** | surface ตารางขนาดยา/ทางเลือกแพ้ยาที่ embed rank ต่ำ โดยไม่ยิง vector เพิ่ม |
| Document-level expansion | **คงไว้ (จากรอบ 5)** | ตารางทั้งตาราง / dose ทั้งแถว |
| LLM Intent Classifier | **ไม่ใช้** | rule-based พอ + เร็วกว่า (ไม่เพิ่ม 1 call/turn) |
| Multi-Query / Agentic | **ไม่ใช้** | เกินขอบเขต Vanilla RAG + เพิ่ม latency |

---

## 3. ผลทดสอบ -- เคสจาก Feedback รอบ 6 (C1-C10)

| # | เคส (จาก optimize_6) | ประเด็นที่ Feedback ชี้ | ผลหลังแก้ |
|---|---|---|---|
| C1 | พิมพ์ "ดีมากตอบได้ดี" หลังบอทตอบเคส | ตอบเคสเดิมซ้ำ + หลอน | **intent=smalltalk, chunks=0** ตอบ "ยินดีครับ..." ไม่แตะเคสเดิม |
| C2 | pharyngitis ผู้ใหญ่ 20 ปี (ข้อมูลครบ) | dose PenV/Amox หาย, ไม่มี ranking | Differential ranked + **PenV 250/500, Amox 1000/500 x10 วัน ครบ** |
| C3 | เด็ก 4 ขวบ ไอ เจ็บคอ (ข้อมูลไม่ครบ) | ซักประวัติถูกดันไปล่าง, เคร่ง format | **ประเภท 4** ทวนเคส+วงเล็บสิ่งที่ขาด+ซักประวัติ "นำหน้า" สั้น |
| C4 | เคสใหม่ในแชท "อีกเคส ไข้ 39... แพ้ pencillin" | หลอน "อายุ 20" + อ้าง Guideline เด็กให้ผู้ใหญ่ | **ไม่หลอนอายุ** ระบุ "เคสนี้ยังไม่ระบุอายุ", แพ้ type1 -> เลี่ยง beta-lactam ครบ |
| C5 | เด็ก 1 ขวบ ปวดหู ขอ amoxicillin (ไม่ครบ) | ต้องซักประวัติ + ระยะเวลา 10 วัน | **ประเภท 4** ซักไข้/แพ้ยา/นน. + note <2 ปี เสี่ยง DRSP |
| C6 | เด็ก 15 kg AOM เคยได้ amoxicillin | dual-guideline + min-max | Differential ranked + **min-max: 600-675 mg/dose** + DRSP -> Augmentin |
| C7 | เด็ก 3 ขวบ ไข้ น้ำมูกใส แม่ขอ "ยาแก้อักเสบ" | ยาแก้อักเสบ != ปฏิชีวนะ | ปฏิเสธ AB + **ชี้แจงยาแก้อักเสบ != antibiotic** + saline/ดูดน้ำมูก + <4 ปี |
| C8 | ไซนัส 50 ปี ขอ Penicillin V | (110 mcg) หาย + ห้ามผสมเน็ต | Augmentin 500 q8h/875 q12h + **Triamcinolone (110 mcg)** + ปฏิเสธ PenV ให้เหตุผล |
| C9 | ขอ ciprofloxacin (ไม่ให้อาการ, Negative) | ข้ามซักประวัติ | **ประเภท 4** ซักอาการ/ระยะเวลา/แพ้ยา + note cipro ไม่ใช่ first-line |
| C10 | "คำถามแต่ละประเภทแยกด้วยอะไร" | ต้องอธิบายระบบเชิงสรุป | **intent=meta_types** สรุป 6 ประเภท + เกณฑ์ + ตัวอย่าง สั้น |

**ผ่านทั้ง 10/10** (context bleeding, dose completeness, history-taking priority, ranking, detail capture, focused answer, meta -- ครบทุกประเด็น)

### Follow-up (บริบทต่อเนื่องในแชท)

| # | เคส | ผล |
|---|---|---|
| F-UP1 | บริบทไซนัส -> ถาม "เมื่อไหร่ต้องเริ่ม ATB" | **ตอบเกณฑ์ ABRS โรคเดียว** (severe/persistent >=10 วัน/double sickening) ไม่ยกโรคอื่น |
| F-UP2 | บริบทเด็ก 6 ขวบ -> "ลูกอีกคน อายุ 3 ปี อาการเดียวกัน" | **ประเภท 3** ใช้อายุ 3, คง <4 ปี safety, ระบุเงื่อนไข "หากน้ำหนักเท่ากัน" |

---

## 4. ผลทดสอบ -- Fresh / Edge / Negative (N1-N12, ไม่เคยอยู่ใน prompt)

| # | ประเภท | เคส | ผลคลินิก (สาระสำคัญ) | อ้างอิง |
|---|---|---|---|---|
| N1 | Positive | ผู้ใหญ่ 30 AOM | ranked (AOM สูง/AOE ต่ำ), ผู้ใหญ่ AOM self-limiting เน้น symptomatic | AAFP 3; Dose 13,15 |
| N2 | Positive | เด็ก 8 ขวบ 25 kg pharyngitis | Amox 50 mg/kg (max 1g) -> 25kg=1250>max -> **cap 1000**, PenV เด็ก | URI 24; Dose 13 |
| N3 | Negative | ผู้ใหญ่ 28 หวัดไวรัส ขอ AB | ปฏิเสธ AB + ranked (cold สูง/allergic กลาง) + decongestant ผู้ใหญ่ | AAFP 2; Dose 11,26 |
| N4 | Edge | ผู้ใหญ่ 35 pharyngitis แพ้ penicillin anaphylaxis | เลี่ยง beta-lactam+ceph ทั้งหมด -> **Clindamycin 300 x3 x10, Azithromycin 500 x5** | AAFP 6 |
| N5 | Edge | เด็ก 5 epiglottitis (tripod, drooling) | **ส่ง ER ทันที, ห้ามตรวจคอเอง**, ไม่จ่ายยา | AAFP 2; URI 63 |
| N6 | Incomplete | ผู้หญิงเจ็บคอ ขอยาแก้อักเสบ | **ประเภท 4** ซักประวัติ + ชี้แจงยาแก้อักเสบ != AB | AAFP 4; Dose 48 |
| N7 | Out-of-scope | เบาหวานน้ำตาล 250 | **ประเภท 6** แยกบล็อกนอกคู่มือ, ปรับยาเบาหวานให้แพทย์ | ext (ADA/dmthai) |
| N8 | Negative | เด็ก 2 ขวบ ขอยาแก้ไอละลายเสมหะ | ปฏิเสธ <4 ปี (AAP Choosing Wisely) + saline | AAFP 2; URI 18 |
| N9 | Dose calc | เด็ก 18 kg pharyngitis, ยาน้ำ 250mg/5mL | Amox 900 mg/day -> 450 mg/dose -> **9 mL x2** | URI 24; Dose 13 |
| N10 | General | Centor score คืออะไร | **ประเภท 1** อธิบายเกณฑ์+คะแนน+threshold ไม่ใช้ 5 ขั้น | AAFP 4,5 |
| N11 | Edge | เด็ก 7 ขวบ แพ้ amox ผื่นเล็กน้อย (non-type1) | **non-type1 -> Cephalexin 40 mg/kg** (22kg=440 mg x2) | URI 24; Dose 13 |
| N12 | Positive | ผู้หญิง 45 ABRS แพ้ penicillin ผื่นลมพิษ (type1) | type1 -> **Doxycycline 100 x2 / Cefixime 400 x1** + Mometasone | AAFP 6; Dose 30 |

**ความถูกต้องเชิงคลินิก 12/12** | dose ครบ (first-line + ทางเลือกแพ้ยา + min-max) | อ้างอิงในช่วงจริง |
allergy-type discrimination ถูก (N4 anaphylaxis เลี่ยง ceph, N11 non-type1 ใช้ ceph ได้, N12 type1 เลี่ยง)

### Unit tests (offline, ไม่ใช้ API)

- **Intent classifier: 15/15** (smalltalk/clinical/meta_types แยกถูก รวมเคสหลอก)
- **Retry helper: 3/3** (classify transient vs quota, recover หลัง retry, non-transient ไม่เสีย retry)
- **External label cleaner:** ตัด URL + คำกำกับ "นอกคู่มือ/นอกเอกสาร" เหลือหัวข้อเรื่องล้วน

---

## 5. Latency (คำนึงตามโจทย์ "ไว แต่ไม่นาน")

วัด warm (ตัด cold-start ~10s ของการเปิด process/โหลด Qdrant):

| เส้นทาง | เวลา (จบสมบูรณ์) | หมายเหตุ |
|---|---|---|
| clinical เต็มรูปแบบ | ~15-20s | ถูกครอบงำโดย "ความยาว output" (คำตอบ 5 ขั้น ~800+ tokens) + RTT ของ Gemini |
| ค้นหา (embed+rerank+expand+inject) | ~2.8s | ส่วน retrieval ทั้งหมด (embed cache 0.5s, rerank ~2.3s) |
| bare LLM call | ~3-6s | ความหน่วงพื้นฐานของ API (ควบคุมไม่ได้) |
| smalltalk (light reply) | ~4-10s | **1 call เดียว ไม่ retrieve** -- เร็วกว่าเดิมที่ re-run RAG เต็ม (~18s) และตอบถูก |

- **Streaming UX:** ผู้ใช้เห็น token แรก ~5-6s (search 2.8s + TTFT) ไม่ต้องรอจนจบ 18s
- สิ่งที่รอบนี้เพิ่ม (PER_SOURCE_TOP_K 12, table injection, prompt ยาวขึ้น) เพิ่มเวลา ~1-2s เท่านั้น
  (ไม่มี LLM call เพิ่มบนเส้นทางคลินิก) -- แลกกับ dose ครบ + ยาทางเลือกแพ้ยาครบ ถือว่าคุ้ม
- Intent gate ช่วย **ลด** latency บน turn ที่ไม่ใช่คลินิก (ตัด retrieve+rerank ออก 2 call)

---

## 6. Checklist (Pharmacy Bot Evaluation)

| หมวด | เกณฑ์ | ผล | หลักฐาน |
|---|---|:--:|---|
| **Accuracy** | first-line/ทางเลือกแพ้ยา/dose/ระยะเวลา ถูก Guideline | PASS | C2,C6,N2,N4,N11,N12 |
| | dose first-line ครบทุกตัวเลือก (ไม่สรุปย่อ) | PASS | C2 (PenV+Amox ครบ) |
| | ประเภทยาตามกฎหมาย (อันตราย ไม่ใช่ควบคุมพิเศษ) | PASS | C8,C9,N4 |
| | ปลอดภัยเด็กเล็ก <4 ปี | PASS | C7,N8 |
| | ดึงถูกโรค/ช่วงวัย/หัวข้อ | PASS | N5 epiglottitis, N1 AOM ผู้ใหญ่ |
| | ยาแก้อักเสบ != ยาปฏิชีวนะ | PASS | C7,N6 |
| **Dose Calc** | Min-Max เต็มช่วง (ขอบล่าง+บน) | PASS | C6 (600-675), N2 cap max |
| | คำนวณตามน้ำหนักอัตโนมัติ + mL | PASS | C6,N9 (9 mL),N11 |
| | ระยะเวลาจำเพาะตามอายุ/ความรุนแรง | PASS | C5,C6 |
| **References** | Ref + เล่ม + เลขหน้าในช่วงจริง เล่มเดียวต่อ Ref | PASS | ทุกเคส |
| | แยก external + label สั้น (หัวข้อล้วน) | PASS | C9,N7 |
| | ยาทางเลือกแพ้ยา "มีขนาด" ไม่ใช่แค่ชื่อ | PASS | N4,N11,N12 |
| | เทียบ dual-guideline วัยเดียวกัน | PASS (design) | Ref กฎ 6 |
| **History/Reasoning** | ซักประวัตินำหน้า + เหตุผลกำกับ (Positive/Negative) | PASS | C3,C5,C9,N6 |
| | Differential ranked (สูง/กลาง/ต่ำ + เหตุผล) | PASS | C6,N1-N4,N11,N12 |
| | ส่งต่อเฉพาะ Red Flag จริง | PASS | N5 (ER) |
| **Conversation** | จำบริบท/แยกเคสเดิม-ใหม่, focused follow-up | PASS | F-UP1,F-UP2 |
| | **ไม่ context bleeding / ไม่ re-answer social** | PASS | C1 |
| | **ไม่ hallucinate อายุข้ามเคส** | PASS | C4 |
| | จำแนกประเภทแม่น + highlight | PASS | ทุกเคสระบุประเภทตัวหนา |
| **Reliability** | retry 503 + ไม่มี prompt_tokens crash | PASS | unit test + main.py fix |

**สรุป: ผ่านครบทุกช่อง**

---

## 7. ไฟล์ที่แก้ + knobs ใหม่

- `backend/config.py`
  - `PER_SOURCE_TOP_K` 8 -> **12** (ให้ตารางขนาดยา rank ต่ำ ยังติด candidate pool)
  - ใหม่: `INTENT_GATE` (on/off), `LLM_RETRY_MAX` (2), `LLM_RETRY_BACKOFF` (1.2)
- `backend/rag_engine.py`
  - Intent gate: `classify_message_intent`, `_light_reply`, `_gated_reply`, `_SMALLTALK_RE`/`_CLINICAL_HINT_RE`/`_META_TYPES_RE`
  - Reliability: `_call_with_retry`, `_is_transient_error` (ใช้กับ answer stream/non-stream + light reply)
  - Retrieval: `_inject_relevant_tables` (dose-table-aware pass) ต่อจาก source-coverage ก่อน doc-expansion
  - External label: `_clean_external_label` (ตัด URL + คำกำกับ), heading external = ""
  - Prompt: กฎ 4/6/7, Ref กฎ 1/6, ประเภท 2 (เกณฑ์ 2vs4 + differential ranked + 3a full dose),
    ประเภท 3 (focused + ผู้ป่วยใหม่), ประเภท 4 (ซักประวัตินำหน้า), ประเภท 5 (ซักก่อนปฏิเสธ), ANSWER FORMATTING (bold ประเภท)
- `backend/main.py`
  - แก้บั๊ก: init `prompt_tokens`/`completion_tokens = 0` ก่อน loop ใน chat_stream / edit_last_message / regenerate
    (กัน `UnboundLocalError` เมื่อ stream จบด้วย error/503)

**ไม่แตะ:** `rag/qdrant_db/`, `rag/pipeline.py`, chunk/embedding, `frontend/`

**ตรวจ Error:** `py_compile` ผ่านทุกไฟล์, import ทั้งแอปผ่าน, รัน generate_answer (stream/non-stream) ผ่าน,
ไม่พบ NameError/KeyError/IndexError/UnboundLocalError ในทุกเคสทดสอบ

---

## 8. จุดที่ยังปรับต่อได้ (ความโปร่งใส)

- **เลขหน้าตาราง first-line (AAFP)** ยัง "ก้ำกึ่ง p5/p6/p7" ระหว่างรัน เพราะเนื้อหาตาราง first-line คร่อม
  หลาย chunk/หลายหน้า -- ทุกหน้าที่อ้างเป็น "หน้าจริงที่มีเนื้อหานั้นใกล้เคียง" (ไม่ใช่หน้าหลอน) และหน้า p6/p7
  ปรากฏในแผงอ้างอิงให้กดตรวจได้ แต่ถ้าต้องการ pin หน้าเดียวเป๊ะต้อง re-chunk (data-layer, นอกขอบเขต)
- **smalltalk latency ~4-10s** ยังผูกกับ RTT ของ API (แม้เป็น call เดียว) -- ถ้าต้องการ "ทันที" สำหรับ ack
  สั้นมากๆ (เช่น "ok") ทำ canned response เฉพาะ exact-match ได้ (แลกกับความ dynamic)
- **external ref เคสนอกขอบเขต** บางครั้ง LLM เขียนเป็น markdown link แทน `[Ref: ...]` -> ไม่ขึ้นแผงอ้างอิง
  (เนื้อหายังถูก) -- เป็นความไม่สม่ำเสมอของโมเดล กันเพิ่มได้ด้วย post-parse markdown link (ทำในรอบถัดไป)

---

## 9. Next Steps (นอกขอบเขตรอบนี้ / ชั้น Data-Infra)

1. **[Data-layer]** re-chunk ตาราง AAFP (first-line + allergy alternative) เป็น row ต่อยา แล้ว re-embed
   -> ให้เลขหน้าเป๊ะ 100% และ dose ผู้ใหญ่ถูกดึงตรงโดยไม่พึ่ง injection
2. **[Eval]** ทำ regression suite อัตโนมัติจาก 24 เคสนี้ (assert keyword: ชื่อยา/ขนาด/ประเภท/ไม่มี bleeding)
   รันทุกครั้งที่แก้ prompt วัด accuracy/ref/latency
3. **[External Ref]** post-parse markdown link นอกคู่มือ -> normalize เป็น [Ref] ให้ขึ้นแผงอ้างอิงเสมอ
4. **[Infra]** ย้าย `google.generativeai` (deprecated) -> `google.genai` (แยกงานจากรอบ prompt)

---

## 10. สรุป (bullet)

- **Intent/Retrieval Gate** = พระเอกรอบนี้ -> แก้ Context Bleeding: social ไม่ re-answer เคสเดิม (chunks=0), ~0ms
- **Dose-table-aware pass + PER_SOURCE_TOP_K 12** -> ยาทางเลือกแพ้ยาผู้ใหญ่ (Clindamycin/Azithromycin) + first-line ครบ
- **Prompt** -> differential ranked (สูง/กลาง/ต่ำ), min-max ทั้งช่วง, ซักประวัตินำหน้าเมื่อไม่ครบ, focused answer,
  เก็บ detail (110 mcg), ยาแก้อักเสบ != antibiotic, ไม่หลอนอายุข้ามเคส, bold ประเภท
- **Reliability** -> retry 503 + แก้บั๊ก `prompt_tokens` (UnboundLocalError)
- **Validate:** Feedback 10 + Follow-up 2 + Fresh/Edge/Negative 12 + unit (intent 15/15, retry 3/3) = ผ่านครบ
- **ขอบเขต:** prompt/RAG/query-time + bugfix token เท่านั้น -- ไม่แตะ vector DB/chunk/embedding/frontend
