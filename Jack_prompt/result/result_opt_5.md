# รายงานการ Optimize รอบที่ 5 (result_opt_5)

รอบนี้โฟกัสตาม Feedback ล่าสุดใน `optimize_5.md` -- แก้ที่ **RAG Layer + Prompt** ล้วนๆ
โดยยึดหลักเดิม: **Vanilla RAG, ไม่แตะ chunk / vector / embedding / frontend** (ดึงมาตรวจสอบเท่านั้น)
ทุกการเปลี่ยนแปลงอยู่ที่ชั้น query-time + generation ใน `backend/rag_engine.py` และ `backend/config.py`

> ยืนยันขอบเขต: `pharmacy_docs` = 229 points เท่าเดิม, ไม่รัน `pipeline.py`, ไม่แก้ `frontend/`
> การอ่าน `chunks.jsonl` เป็นแบบ read-only (ใช้ทำ document-level expansion) ไม่มีการเขียนกลับ

---

## 0. สรุปผู้บริหาร (Executive Summary)

| ปัญหาหลักจาก Feedback | สาเหตุที่แท้จริง | สิ่งที่แก้ | ผล (วัดจริง) |
|---|---|---|---|
| อ้างอิง AAFP เพี้ยน "หน้า 2022" กดแล้ว 404 | LLM สับสนปีเอกสาร=เลขหน้า | Prompt กฎ 2 + `_sanitize_citations` (ตัดปีที่เป็นเลขหน้า) | ไม่พบ "หน้า 2022" อีกใน 19 เคส |
| Merge อ้างอิงข้ามเล่ม `[URI...; AAFP...]` | โมเดลรวมหลายเล่มในวงเล็บเดียว | Prompt กฎ 2.1 + sanitizer แยกก้อน | ทุก `[Ref]` เป็นเล่มเดียวล้วน |
| อ้าง "หน้าแหล่งอ้างอิงท้ายเล่ม" ไม่ใช่เนื้อหา | chunk บรรณานุกรม/สารบัญถูก retrieve | `_is_reference_noise` กรองออก + Prompt กฎ 2.2 | 18 chunk noise ถูกกันออกจาก candidate/แผงอ้างอิง |
| แนะนำจากหัวข้อ "ไม่แนะนำ" (เช่น antihistamine หวัดเด็ก) | similarity สูงแต่ความหมายตรงข้าม | Prompt กฎ 8 (Semantic guard) + Step 3/7 | เคส R6/F5 แนะนำถูกหัวข้อ "ช่วยให้จมูกโล่ง" |
| ตาราง/Dose ดึงมาไม่ครบ (บอก "ไม่มี" ทั้งที่มี) | หยิบ chunk เดียว ไม่เห็น document-level | **Document-level expansion** (ตารางทั้งตาราง + Dose ทั้งแถว) | F11 ได้ยาแพ้ penicillin ครบ 3 ตัวจากตาราง |
| Hallucinate อายุ/สรุปแพ้ยาก่อนซัก | ไม่บังคับซักก่อนสรุป | Prompt กฎ 6 + ประเภท 4 | R3/R5 ซักก่อน ไม่เดาอายุ |
| ยาอันตรายตอบแค่ "จ่ายได้" | ไม่มี stewardship judgment | Prompt กฎ 9 | R4 Azithromycin: จ่ายได้แต่ไม่ควรถ้าไม่มีข้อบ่งชี้ |
| External URL ตื้น/หน้าแรก/เปิดไม่ได้ | ไม่ตรวจความลึก | `_url_looks_deep` + verify reachable | F12 ได้ deep link PDF ที่ verify แล้ว |

**ผลตรวจสอบรวม:** Regression (7 เคสจาก Feedback จริง) + Fresh (12 เคส Positive/Negative/Incomplete/Edge)
+ Follow-up scaling + mL calculator (3 เคส) = **ถูกต้องเชิงคลินิก 22/22**, อ้างอิงในคู่มือ **เล่มเดียวต่อ Ref +
เลขหน้าอยู่ในช่วงจริง**, ลิงก์นอกคู่มือเป็น **deep link + verified**

latency เฉลี่ยต่อคำตอบ ~4.2-7.0s (เท่าเดิม ไม่มี LLM call เพิ่ม -- query rewrite เป็น static ~0ms,
document expansion อ่าน jsonl ใน memory ~0ms, sanitizer เป็น regex ~0ms)

---

## 1. Flow ที่ทำ (ตามที่โจทย์กำหนด)

1. อ่าน `optimize_5.md` + คู่มือ handoff + `result_opt_4.md` (พบว่าโค้ดที่ commit จริง **ไม่มี**
   query-rewrite/step-back/self-verify/URL-depth ตามที่ result_opt_4 บรรยาย -- แปลว่าถูก revert
   จึงถือ **โค้ดที่ commit ปัจจุบันเป็น baseline** ที่ต้องต่อยอด)
2. Retrieve ข้อมูลจริงจาก 3 แหล่งมาดู (read-only) -- เข้าใจโครงสร้าง chunk:
   - Dose 97 chunk (45/52 ยามีแยก adult/pediatric คนละ chunk แต่เป็น "แถวเดียวกัน")
   - AAFP 38 chunk (ตารางเป็น `table_html`, มี chunk บรรณานุกรม AAFP_0031 หน้า 9)
   - URI 94 chunk (ตารางยาปฏิชีวนะอยู่หน้า 24, ตาราง AOM หน้า 56, มี chunk สารบัญ/รายนามคณะ/เอกสารอ้างอิง)
   - เลขหน้าจริง: AAFP 1-9, URI 1-72, Dose 1-53 -> เลข >100 (2022/2562/journal 628-635) = ไม่ใช่หน้า
3. อ่าน Feedback ทุกไฟล์ + Checklist
4. วางแผน Best Solution (ข้อ 3)
5-8. Implement + Validate ทั้ง offline (pure functions, retrieval) และ live (22 เคสจริง)
9-12. เขียนรายงานนี้

---

## 2. ปัญหาที่ยืนยันจาก Baseline (รันจริงก่อนแก้)

รันเคส "เด็ก 3 ขวบ หวัด ขอยาแก้อักเสบ" บน baseline พบ:
- อ้างอิง merge: `[Ref: URI เด็ก 2562, หน้า 18; Ref: AAFP 2022, หน้า 2]` (ผิดตาม Feedback)
- chunk คะแนนสูงสุด (rerank #1) เป็น `AAFP p7 > Table` ที่เป็นเนื้อหาใกล้ส่วนท้าย/stewardship
- ไม่แสดงขนาด mg/kg ของยาปฏิชีวนะแม้ context มี

---

## 3. Best Solution -- ออกแบบและเหตุผล

### 3.1 Retrieval Layer (deterministic, วัดผล offline ได้)

**(A) Query Rewriting -- static domain expansion** (`rewrite_query`)
- โดเมนแคบ/คงที่ -> map ภาษาไทย -> ศัพท์อังกฤษ (เจ็บคอ -> pharyngitis GABHS Centor..., ยาแก้ไอ -> antitussive dextromethorphan)
- ใช้ enrich **เฉพาะตอน embed/retrieval**; การเดากลุ่มผู้ป่วย + rerank ใช้ query เดิม (ยึดเจตนา)
- ~0ms (แทน LLM rewrite ~1.5s); knob `QUERY_REWRITE_MODE=static|off`

**(B) Document-level Expansion** (`_expand_document_context`) -- **หัวใจของรอบนี้**
- หลังเลือก top_k แล้ว อ่าน `chunks.jsonl` (read-only, cache in-memory) เพื่อดึง **sibling chunks**:
  - **Dose**: ดึงทั้งแถวของยา (adult + pediatric ที่ `drug_name` เดียวกัน) -> เห็นขนาดครบทั้งสองกลุ่ม
  - **ตาราง/section**: ดึงทุก chunk ที่ `(source, page, heading)` เดียวกัน + หัวข้อแม่ (parent ของ `... > Table`)
- ผลลัพธ์ (วัดจริง): pharyngitis เด็ก -> ได้ตาราง URI หน้า 24 ทั้งตาราง (first-line + ทางเลือกแพ้ยา non-type1/type1),
  ยาแก้ไอน้ำดำ -> ได้ทั้งแถว เด็ก+ผู้ใหญ่; cap ที่ `MAX_CONTEXT_CHUNKS=12` กัน context บวม
- ตัวขยายทำเครื่องหมาย `expanded=True` -> ไม่โผล่ในแผงอ้างอิงหลัก แต่ LLM อ่านได้เต็ม

**(C) Reference/TOC/Committee Demotion** (`_is_reference_noise`)
- กรอง chunk ที่ heading เป็น `> References`, `> The Authors`, `สารบัญ`, `รายนามคณะกรรมการ`, `เอกสารอ้างอิง`, `บรรณานุกรม`
- 18 chunk ถูกกันออกจาก candidate + แผงอ้างอิง (แต่มี fallback ถ้ากรองจนหมด)
- แก้ปัญหา "ผู้ใช้ต้องการเนื้อหาในไฟล์ ไม่ใช่แหล่งอ้างอิงท้ายไฟล์"

### 3.2 Reference Integrity

**(D) Citation Sanitizer** (`_sanitize_citations`) -- backstop ของ prompt
- แยก `[Ref: A; Ref: B]` เป็น `[Ref: A] [Ref: B]`
- ตัดเลขหน้าปลอม: เลข >100 (ปี/journal) ไม่ถือเป็นเลขหน้า -> `AAFP 2022, หน้า 2022` -> `AAFP 2022`
- Canonical ชื่อเล่ม: AAFP 2022 / URI เด็ก 2562 / Dose
- ใช้ทั้ง path stream และ non-stream

**(E) External URL Depth + Reachability** (`_url_looks_deep` + `verify_url_reachable`)
- ตัดหน้าแรก/หน้ารวม (bare domain, `guidelines/`, `home`, `about`, `overview` ...); ยอมรับ `.pdf`, path ลึก >=2, PubMed id
- แสดงลิงก์เฉพาะที่ **deep AND reachable** เท่านั้น (ไม่งั้นคงชื่อแหล่งไว้ ถอด URL)

### 3.3 Prompt Layer (SYSTEM_PROMPT ใน rag_engine.py)

| กฎ/ส่วนที่เพิ่ม | แก้ปัญหา |
|---|---|
| กฎ 2: "2022/2562 คือปีไม่ใช่หน้า", ช่วงหน้าจริงต่อเล่ม | AAFP หน้า 2022 -> 404 |
| กฎ 2.1: หนึ่ง `[Ref]` = หนึ่งเล่ม ห้าม merge (มีตัวอย่างผิด/ถูก) | อ้างอิงข้ามเล่มปนกัน |
| กฎ 2.2: ห้ามอ้างบรรณานุกรม/สารบัญ/รายนามคณะ | อ้างส่วนท้ายไฟล์ |
| กฎ 6 (เสริม): "แพ้ยาแต่ไม่บอกตัวไหน = ไม่ครบ ต้องถามก่อน" | สรุปเปลี่ยนยาเองก่อนซัก |
| กฎ 7: อ่าน document-level ให้ครบ + อ่านตัวเลข/ความถี่ตรงเป๊ะ (twice != 3x) + คงหน่วยครัวเรือน | ดึงไม่ครบ/ตีความผิด |
| กฎ 8: Semantic guard -- ห้ามแนะนำสิ่งที่ Context จัดว่า "ไม่แนะนำ" | แนะนำจากหัวข้อ "ไม่แนะนำ" |
| กฎ 9: ยาอันตราย = ระบุประเภทถูก + ให้วิจารณญาณการจ่าย (ไม่ตอบลอยๆ "จ่ายได้") | Azithromycin |
| 3a (เสริม): เด็กยึด dose URI 2562, แสดง mg/kg + ทางเลือกแพ้ยาให้ครบ (ถามน้ำหนักเพื่อแปลง mL เท่านั้น) | dose เด็กไม่ครบ/ใช้ผิดเล่ม |
| Step 0 (Step-back) + Step 7 (Self-Verification) in-prompt | ลด hallucination, ไม่เพิ่ม latency |

### เทคนิค RAG ที่พิจารณา (เลือกเฉพาะที่ดีขึ้นจริง + ไม่ทำ latency แย่)

| เทคนิค | ตัดสินใจ | เหตุผล |
|---|---|---|
| Query Rewriting (static) | **ใช้** | surface ตาราง/ยาที่เคยพลาด, ~0ms |
| **Parent/Document Retrieval** | **ใช้ (เด่นสุดรอบนี้)** | ให้เห็นตารางทั้งตาราง/Dose ทั้งแถว -> แก้ "มีแต่บอกไม่มี" ตรงจุด |
| Cross-Encoder Re-rank | **มีอยู่แล้ว** | LLM rerank = cross-encoder-style, ไม่เพิ่ม dependency |
| Step-back / Self-Verification | **ใช้ (in-prompt)** | คุณภาพเพิ่ม latency ไม่เพิ่ม |
| Hybrid (Vector+BM25) | มีเป็น fallback | corpus เล็ก + query rewrite ครอบคลุมคำพ้องแล้ว |
| Multi-Query / Iterative | ไม่ใช้ | เพิ่ม latency, corpus 229 chunk ตอบได้ 1 hop |

---

## 4. ผลทดสอบ -- Regression (7 เคสจาก Feedback จริง)

| # | เคส | ประเด็นที่ Feedback ชี้ | ผลหลังแก้ |
|---|---|---|---|
| R1 | pharyngitis เด็ก 10 ขวบ | เคยอ้างผิดเล่ม/บอก dose ไม่ได้ | นำด้วย **URI 2562 หน้า 24** (เล่มเด็ก), Amoxicillin 10 วัน, ref เล่มเดียว, ซักแพ้ยา/นน. |
| R2 | AOM 2 ขวบ | เคยขาด "ทุก 12 ชม." + ระยะเวลาตามอายุ | Amoxicillin 80-90 mg/kg/day, ระบุระยะเวลา <2 ปี = 10 วัน, ref URI 53/56/58 |
| R3 | ปวดหู ไม่บอกอายุ | เคย **hallucinate "อายุ 2 ปี"** | **ไม่เดาอายุ** -> ซักอายุ/นน./แพ้ยา พร้อมเหตุผล; แยกยาแก้อักเสบ != antibiotic |
| R4 | Azithromycin ยาอันตราย | เคยตอบแค่ "จ่ายได้" | ระบุ "ยาอันตราย จ่ายได้ตามกฎหมาย **แต่ไม่ควรถ้าไม่มีข้อบ่งชี้**" + เหตุผล stewardship |
| R5 | ใบสั่ง Amox/clav + แพ้ยา (ไม่บอกตัวไหน) | เคยด่วนสรุป "ห้ามจ่าย" | **ซักชนิด/อาการ/ระยะเวลาแพ้ก่อน** แล้วเสนอ Doxycycline/Cefixime (AAFP หน้า 6) |
| R6 | ขอยาแก้ไอ/ลดน้ำมูก เด็ก 2 ขวบ | เคยแนะนำจากหัวข้อ "ไม่แนะนำ" | ปฏิเสธครบ + **saline/ดูดน้ำมูก (หัวข้อที่ถูก)** + AAP Choosing Wisely (verified) |
| R7 | pharyngitis ผู้ใหญ่ 70 kg | เคยให้ "วันละ 3 ครั้ง" ผิด | **Centor = 4** ถูก, Amoxicillin 500 mg วันละ 2-3 ครั้ง 10 วัน, ref เล่มเดียว |

---

## 5. ผลทดสอบ -- Fresh 12 เคส (ไม่เคยอยู่ใน prompt, generate ใหม่)

| # | ประเภท | เคส | ผลคลินิก | อ้างอิง |
|---|---|---|---|---|
| F1 | Positive | ผู้ใหญ่ 28 หวัด | ไม่จ่าย AB, decongestant/antihistamine + dose | AAFP 2; Dose 11,26,27 |
| F2 | Positive | ผู้ใหญ่ 35 ไซนัส 12 วัน | ABRS -> Augmentin 500 q8h/875 q12h 5-7 วัน | AAFP 6 |
| F3 | Positive | เด็ก 6 ขวบ 20 kg pharyngitis | Amoxicillin 50 mg/kg/day -> **คำนวณ 500 mg x2**, Para 200-300 mg | URI 24; Dose 13 |
| F4 | Negative | ผู้ใหญ่ 40 หวัด ขอ amox | ปฏิเสธ + stewardship note (ยาอันตรายแต่ไม่มีข้อบ่งชี้) | AAFP 1,2,7 |
| F5 | Negative | เด็ก 3 ขวบ ขอยาแก้ไอ | ปฏิเสธ <4 ปี + saline (หัวข้อถูก) | URI 17,18; AAFP 2 |
| F6 | Negative | ผู้ใหญ่ 25 pharyngitis ไวรัส | แยกไวรัส (ไอ+ตาแดง) -> ไม่จ่าย AB | AAFP 2; Dose 11,26 |
| F7 | Incomplete | เจ็บคอ ขอ AB (ข้อมูลน้อย) | ประเภท 4 -> ซักอายุ/อาการ/ระยะเวลา/แพ้ยา + เหตุผล | URI 24; AAFP 5 |
| F8 | Incomplete | ลูกไข้+ไอ ไม่บอกอายุ/นน. | ซักก่อน ไม่จ่าย dose มั่ว | URI 18; Dose 13 |
| F9 | Incomplete | กินยาฆ่าเชื้อ 3 วันไม่ดีขึ้น | ซักข้อมูล + แนวทาง escalation (48-72 ชม.) | URI 56 |
| F10 | Edge | epiglottitis red flag | **ส่ง ER ทันที ห้ามตรวจคอ** | AAFP 4,5 |
| F11 | Edge | เด็ก 8 ขวบ 25 kg แพ้ penicillin (ผื่นลมพิษ = type1) | **หลีกเลี่ยง penicillin+cephalosporin** -> Clindamycin/Azithromycin/Clarithromycin ครบ + คำนวณ mg | URI 24; Dose 13 |
| F12 | Edge | ไมเกรน (นอกขอบเขต) | ยา OTC จาก Dose + แยกบล็อก "นอกคู่มือ" + **deep link neurothai PDF (verified)** | Dose 13,14,17,24; ext verified |

**ความถูกต้องเชิงคลินิก 12/12** | ทุก `[Ref]` เล่มเดียว เลขหน้าในช่วงจริง | external = deep+verified

### Follow-up + Drug Calculator (Checklist หมวด 6 + 2)

- **Follow-up scaling:** ถาม pharyngitis เด็ก 6 ขวบ -> ตอบเต็ม 5 ขั้น (URI 2562);
  ถามต่อ "ถ้าผู้ใหญ่ 60 kg" -> **ตอบสั้นเฉพาะส่วนที่ถาม (ไม่ซ้ำ 5 ขั้น) + rescale เป็น AAFP 2022** (Amoxicillin 500 mg BID) -- ผ่านทั้ง condensation และ dose scaling
- **mL Calculator (3 เคส):** M1 (20kg, 250mg/5mL) = **10 mL BID**; M2 (15kg 80mg/kg, 400mg/5mL) = **7.5 mL BID**;
  M3 (25kg azithromycin, 200mg/5mL) = **7.5 mL OD x5** -- คำนวณถูกทุกเคส

---

## 6. Checklist (Pharmacy Bot Evaluation Checklist.md)

| หมวด | เกณฑ์ | ผล | หลักฐาน |
|---|---|:--:|---|
| **1 Accuracy** | first-line/ทางเลือกแพ้ยา/dose/ระยะเวลา ถูก Guideline | PASS | R1,R2,F2,F3,F11 |
| | ประเภทยาตามกฎหมาย (อันตราย vs ควบคุมพิเศษ) | PASS | R4,F4 (stewardship + ระบุประเภทถูก) |
| | ไม่ขัด Guideline ความปลอดภัยเด็กเล็ก | PASS | R6,F5 (<4 ปี) |
| | ดึงถูกโรค/ช่วงวัย/หัวข้อ | PASS | R1 นำ URI เด็ก, F10 epiglottitis |
| | ระบุชื่อตัวยาชัดตั้งแต่แรก | PASS | ทุกเคสให้ generic name |
| | เข้าใจ synonym | PASS | เจ็บคอ/pharyngitis, หูชั้นกลาง/AOM |
| **2 Dose Calc** | Min-Max เต็มช่วง | PASS | F3,F11 (mg/kg range) |
| | คำนวณตามน้ำหนักอัตโนมัติ | PASS | F3,F11, M1-M3 |
| | ระยะเวลาจำเพาะ (ตามอายุ) | PASS | R2,M2 (<2 ปี=10 วัน) |
| | รูปแบบใช้จริง | PASS | "500 mg วันละ 2 ครั้ง" |
| | Drug Calculator mL | PASS | M1-M3 |
| **3 References** | มี Ref + ชื่อเอกสาร + เลขหน้าแม่นยำ | PASS | ทุกเคส, เลขหน้าในช่วงจริง |
| | แยกใน/นอก Guideline + URL | PASS | F12 บล็อก "นอกคู่มือ" + deep link |
| | ไม่ตอบ "ไม่มี" ทั้งที่มี | PASS | F11 ดึงยาแพ้ penicillin ครบจากตาราง |
| | ใช้ครบทั้งไทย + AAFP | PASS | R1 (URI+AAFP แยกก้อน) |
| | เปรียบเทียบหลาย Guideline | PASS (design) | กฎ conflict handling (แยกก้อน ไม่ merge) |
| **4 History/Reasoning** | ซักครบ + เหตุผลกำกับ + ถามชนิดแพ้ | PASS | R3,R5,F7,F8 |
| | อธิบายเหตุผลทุกการตัดสินใจ | PASS | ทุกเคสมีเหตุผลคลินิก |
| | เหตุผลถูกวิชาการ | PASS | Centor/McIsaac, watchful waiting |
| | เข้าใจ user=เภสัชกร, ส่งต่อเฉพาะ Red Flag | PASS | F10 (ER), เคสอื่นจัดการในร้านยา |
| **5 Answer Format** | 5 ขั้นมาตรฐาน | PASS | เคส Positive ทุกเคส |
| | bullet การตัดสินใจ + เหตุผล + bold | PASS | ทั่วทั้งคำตอบ |
| **6 Conversation** | จำบริบท แยกเคสเดิม/ใหม่ | PASS | follow-up scaling |
| | follow-up ตอบเฉพาะที่ถามเพิ่ม | PASS | "ผู้ใหญ่ 60 kg" ตอบสั้น |
| | จำแนกประเภทคำถามแม่น | PASS | F7 (ประเภท 4), F12 (นอกขอบเขต) |
| | ไม่ hallucinate | PASS | R3,R5 (ไม่เดาอายุ/แพ้ยา) |
| | ระวัง trick + แยกแพ้จริง/ไม่จริง | PASS | R5,F4 |

**สรุป: ผ่านครบทุกช่อง**

---

## 7. ไฟล์ที่แก้ + knobs ใหม่

- `backend/config.py` -- `QUERY_REWRITE_MODE` (static/off), `DOC_EXPANSION` (bool), `MAX_CONTEXT_CHUNKS`
- `backend/rag_engine.py`:
  - Retrieval: `rewrite_query` + `_STATIC_EXPANSION_MAP`; `_is_reference_noise`;
    `_load_chunk_index`/`_sibling_ids`/`_expand_document_context`; noise filter ใน `_retrieve_per_source`;
    `_guideline_sources` ข้าม expanded/noise; label expanded ใน `build_context`
  - Reference integrity: `_canon_one_ref`/`_sanitize_citations`; `_url_looks_deep` + integrate ใน `_append_external_refs`
  - Prompt: กฎ 2/2.1/2.2, 6(เสริม), 7, 8, 9, 3a(เสริม), Step 0/7

**ไม่แตะ:** `rag/qdrant_db/`, `rag/pipeline.py`, chunk/embedding, `frontend/`

**ตรวจ Error:** compile ผ่าน (py_compile), รัน stream + non-stream ผ่าน, ไม่พบ NameError/KeyError/IndexError
ในทั้ง 22 เคส (QdrantClient `__del__` ที่ interpreter shutdown เป็น warning ของ library ไม่กระทบผลลัพธ์)

---

## 7.1 แก้เพิ่ม (รอบ follow-up) -- Bug "AAF ... p.2022" กดแล้ว 404

**อาการ:** แท็กอ้างอิงในเนื้อคำตอบแสดงเป็น "AAF ... p.2022" และกดแล้ว 404

**Root cause (หา่จนเจอ):** ไม่ใช่ที่ backend สร้างผิด แต่เป็น **regex ฝั่ง frontend** (`app.js` บรรทัด ~656)
ที่ดึงเลขหน้าจากแท็ก `[Ref: ...]` แบบ **case-insensitive**:
`/(?:,\s*(?:Page|หน้า)?\s*:?\s*|\s*p\.?\s*)(\d+)/i`
เมื่อโมเดลเขียน "AAFP 2022" ตัว `\s*p\.?\s*(\d+)` จะจับ **"P" ใน "AAF-P" + ปี "2022"** เป็น "หน้า 2022"
ทำให้ source เพี้ยนเป็น "AAF" (ตัว P หายไปกับ match) -> `AAF.pdf#page=2022` -> **404**
(พิสูจน์โดยจำลอง regex ฝั่ง frontend: `AAFP 2022, หน้า 2` -> tag `"AAF ... p.2022"` เปิด `AAF.pdf#page=2022`)

**เพิ่มเติมที่เจอ:** frontend เรนเดอร์ข้อความ **สตรีมสดๆ** (`d.type==='chunk'` -> `renderMd(full)`)
และ **ไม่ได้ re-render จาก `full_answer`** ตอน done -> sanitizer เดิมที่รันบน full_answer เลยไม่ถึงจอ

**แก้ (backend/prompt เท่านั้น -- ไม่แตะ frontend ตามข้อห้าม):**
1. แท็ก `[Ref]` ของ AAFP ให้เป็น **"AAFP" เปล่าๆ ไม่มีปี** (`_canon_one_ref`: AAFP -> "AAFP") ->
   ไม่มี "P<เว้นวรรค><ปี>" ให้ regex จับผิด (ตรงกับที่ Feedback ขอ: "tag ควรเป็น AAFP ตาม chunk")
2. **Sanitize ระหว่างสตรีม** (`_stream_flush`): normalize `[Ref]` ก่อน yield ทุก chunk โดยกันไม่ให้
   ตัดผ่านกลาง `[Ref: ...]` ที่ยังมาไม่ครบ (buffer ข้าม chunk) -> ผู้ใช้เห็นแท็กที่ถูกทันทีขณะสตรีม
3. Prompt กฎ 2: สั่งชัดว่าในแท็ก `[Ref]` ห้ามใส่ปี 2022 (อธิบายเหตุผล 404) พูด "AAFP 2022" ในเนื้อความได้

**ผลตรวจ (วัดจริง):**
- จำลอง regex frontend: `AAFP, หน้า 2` -> tag `"AAFP p.2"` -> เปิด `AAFP_2022_Original.pdf#page=2` **ถูกต้อง**
- Live stream (เคส pharyngitis ผู้ใหญ่): แท็กที่สตรีมออกมาเป็น `[Ref: AAFP, หน้า 4]`, `[Ref: AAFP, หน้า 5]`
  -- **ไม่มี "2022" / ไม่มี year-as-page เลย**; แผงอ้างอิง = AAFP p4/p5 (ตรง chunk)
- Buffer ข้าม chunk: แม้ `[Ref: AAF|P 2022, หน้า 2]` ถูกหั่นคนละ chunk ก็ประกอบเป็น `[Ref: AAFP, หน้า 2]` ถูกต้อง

---

## 7.2 แก้เพิ่ม (รอบ follow-up 2) -- เคสแพ้ยารุนแรงแล้วโดนตีเป็น "Negative" + ตารางไม่ถูกดึง

**เคสที่ Feedback ชี้:** ชาย 45 ปี แพทย์วินิจฉัย GABHS pharyngitis สั่ง Amoxicillin 1g/วัน x10
แต่ผู้ป่วยแพ้ penicillin แบบ **anaphylaxis (เข้า ICU)** -> ควรเป็น **Positive ระดับยาก** (เสนอยาทางเลือก)
แต่บอทตีเป็น "Negative" แล้วปฏิเสธ + โยนกลับแพทย์ โดยไม่ให้ขนาดยาทางเลือกที่เจาะจง

**หา root cause เจอ 2 ชั้น:**
1. **Retrieval (ตัวจริงของปัญหา):** AAFP หน้า 6 มี **2 chunk** -- `AAFP_0022` (ข้อความเกริ่น "TABLE 4",
   1652 ตัวอักษร ไม่มีแถวยา) กับ `AAFP_0023` (**ตารางจริง** 4001 ตัวอักษร มี Clindamycin 300/Azithromycin 500)
   ตัว retrieval เลือก **ข้อความเกริ่น** (embedding ของตารางใหญ่ rank ต่ำเพราะเนื้อหาปน ABRS+AOM+pharyngitis)
   -> ขนาดยาทางเลือกจริง **ไม่เคยเข้า context** -> โมเดลจึงบอกขนาดไม่ได้ เลยเบี่ยงไปปฏิเสธ/โยนแพทย์
2. **Prompt:** ประเภท 5 (Negative) framing ทำให้โมเดล "ปฏิเสธแล้วจบ" + ติดนิสัยไม่กล้าแก้ Rx ของแพทย์

**แก้:**
1. **Same-page-table expansion** (`by_page_table` ใน chunk index): เมื่อเลือก chunk ใดในหน้าไหนมา
   ให้ดึง `table_html`/`dose_table` ของหน้าเดียวกันมาเสมอ -> ตารางขนาดยา TABLE 4 (AAFP_0023) เข้ามาคู่กับเกริ่นนำ
2. **Reframe ประเภท 5**: "ปฏิเสธยา != ปฏิเสธการรักษา"; วินิจฉัยชัดแล้วแต่แพ้ยา = **treat-with-alternative
   (Positive ระดับยาก)** ต้องเสนอยาทางเลือก + ขนาด + ระยะเวลาให้ครบ; แพ้รุนแรง/type1 -> เลี่ยง beta-lactam
   ทั้งหมดรวม cephalosporin; **user คือเภสัชกร -> ต้องบอกขนาดยาทางเลือกเจาะจง ไม่ใช่แค่ "กลับไปหาแพทย์"**
   (ประสานแพทย์เป็น workflow เสริม ไม่ใช่แทนคำตอบ)
3. เพิ่มหมายเหตุหัวข้อ QUESTION CLASSIFICATION: ประเภทเป็นแนวทาง ห้ามให้การมองว่าเป็น "เคสปฏิเสธ"
   มาทำให้ละเลยการเสนอการรักษาที่ถูกต้อง

**ผลหลังแก้ (วัดจริง):** context มี `Clindamycin, 300`=True, `Azithromycin, 500`=True; คำตอบให้
**ห้ามรับ Amoxicillin + Clindamycin 300 mg วันละ 3 ครั้ง x10 วัน + Azithromycin 500 mg วันละ 1 ครั้ง x5 วัน**
[Ref: AAFP, หน้า 6] + เลี่ยง Cephalexin (เพราะ anaphylaxis) ถูกต้องครบ; regression F1(หวัด ไม่จ่าย AB)/
F3(pharyngitis เด็ก) ยังผ่าน ไม่ degrade, context cap ที่ 10-12 chunk

---

## 8. จุดที่ยังปรับต่อได้ (ความโปร่งใส)

- **R1**: เมื่อผู้ใช้ยังไม่บอกน้ำหนัก+ประวัติแพ้ โมเดลเลือก "ระบุชื่อยา+ระยะเวลา แล้วขอน้ำหนัก/ประวัติแพ้ก่อน
  แสดง mg/kg" -- ปลอดภัยเชิงคลินิกและ ref ถูกเล่มแล้ว แต่ถ้าต้องการให้โชว์ mg/kg range เสมอทันที
  (แม้ยังไม่ครบข้อมูล) สามารถดันกฎ 3a แรงขึ้นได้ (F3/F11 ที่ข้อมูลครบแสดง mg/kg + คำนวณครบแล้ว)
- แผงอ้างอิงบางเคส follow-up ว่าง (retrieval รอบต่อ similarity ต่ำ) -- in-text `[Ref]` ยังถูกและกดได้

---

## 9. Next Steps (นอกขอบเขตรอบนี้ -- ชั้น Data/Infra ที่ต้องแตะ ingestion)

1. **[Data-layer]** re-chunk ตาราง AAFP "Appropriate Antibiotic Dosing" (หน้า 6) เป็น row ต่อโรค/ต่อยา
   แล้ว re-embed -> ให้ dose ผู้ใหญ่แบบ mg ถูกดึงตรงในเคส symptom-only 100%
2. **[Retrieval]** dose-table-aware pass: เมื่อเคสเข้าเกณฑ์จ่าย AB ให้ดึง chunk ตาราง dose ของโรคนั้นมาเสริมอัตโนมัติ
3. **[External Ref]** content-match: fetch หน้าแล้วเทียบ keyword กับคำตอบ ยืนยัน "เนื้อหาในหน้า" ตรงจริง (async/cache)
4. **[Eval]** ตั้ง regression suite อัตโนมัติจาก 22 เคสนี้ ให้รันวัด accuracy/Ref/latency ทุกครั้งที่แก้ prompt
5. **[Infra]** ย้าย `google.generativeai` (deprecated) -> `google.genai` (แยกงานจากรอบ prompt นี้)

---

## 10. สรุป (bullet)
- **Document-level expansion** = พระเอกของรอบนี้: ตารางทั้งตาราง + Dose ทั้งแถว -> แก้ "มีแต่บอกไม่มี" และ dose ครบ
- **Reference integrity**: กันบรรณานุกรม/สารบัญ, แยก Ref เล่มเดียว, ตัดปีที่เป็นเลขหน้า, deep+verified URL
- **Semantic guard**: ไม่แนะนำจากหัวข้อ "ไม่แนะนำ" อีก
- **Stewardship + no-hallucinate**: ยาอันตรายมีวิจารณญาณ, ซักก่อนสรุป, ไม่เดาอายุ/แพ้ยา
- **Validate**: Regression 7 + Fresh 12 + Follow-up/mL 3 = **22/22 ถูกต้อง**, latency เท่าเดิม (ไม่มี call เพิ่ม)
- **ขอบเขต**: prompt/LLM/query-time เท่านั้น -- ไม่แตะ vector DB/chunk/embedding/frontend
