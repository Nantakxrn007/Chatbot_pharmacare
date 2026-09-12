# Phase 2 Optimize ครั้งที่ 1 -- ผลลัพธ์ (Result Report)

> ขอบเขต: ปรับเฉพาะ **RAG / Performance ของ chatbot** (query-time + prompt) -- **ไม่แตะ Ingestion** (ไม่แก้ chunk, ไม่ re-embed, ไม่เพิ่ม/ลบข้อมูลใน Qdrant) ดึง embedding/chunk มาดูเพื่ออ้างอิงเท่านั้น
> ไฟล์ที่แก้: `backend/rag_engine.py` (แก้ต่อยอดของเดิม) + `backend/symptomatic_gateway.py` (โมดูลใหม่ แยกชั้น "ยาตามอาการ")

---

## 1. ปัญหาเดิมและเป้าหมาย

| # | ปัญหาจากการทดสอบบน business domain | เป้าหมาย |
|---|---|---|
| 1 | ตอบตามไกด์ไลน์ AAFP (ต่างประเทศ) อย่างเดียว ไม่สอดคล้องหน้างานร้านยาไทย | ตอบ **ทั้ง Guideline และแนวปฏิบัติจริง (Expert Opinion)** -- ควรจ่ายยาเลยไหม จ่ายอะไร ปฏิบัติตัวอย่างไร |
| 2 | ยาตามอาการ **เจาะจงเกินไป/ผิดอาการ** (สูตรผสม CPM+Phenylephrine ในเคสไม่มีน้ำมูก, Decolgen ในไซนัส, Betadine gargle แทนน้ำเกลือ) + **เสี่ยงเชิงโฆษณา** (ตอบชื่อการค้า) | เลือกยาให้ตรงลักษณะอาการ, ขึ้นต้นด้วยชื่อสามัญ/ตัวยาสำคัญ, มี Gateway ตรวจซ้ำ |
| 3 | ยาตามอาการ **ได้ตัวเดิมซ้ำๆ** ไม่มี choice | เสนอเป็น **ตัวเลือก 2-4 ตัวต่อกลุ่มอาการ** พร้อมเหตุผลว่าเหมาะกับใคร + ขนาดยา |
| 4 | **ไม่ถามกลับ/ซักประวัติไม่ครบ** เพราะ prompt ไม่มี pattern ชัด | ใช้ pattern ซักประวัติ Who-Age-(Weight)-What-Severity-When-Treated-Allergy-Comorbidity อย่างเป็นระบบ |

---

## 2. ตรวจข้อมูล (File & Embedding) -- อ่านอย่างเดียว

- `rag/data/chunks.jsonl` = **234 chunks** (AAFP 38 + URI 94 + Dose 102) ตรงกับ Qdrant Cloud `pharmacy_docs` 234 docs
- **Dose table ใหม่**: 102 chunks = **53 ยา** (แถวผู้ใหญ่ + เด็ก) มีคอลัมน์ ข้อบ่งใช้ / Dose ผู้ใหญ่ / Dose เด็ก / ปรับตามไต-ตับ / ข้อห้าม
  - ข้อบ่งใช้ในตารางใหม่ **เขียนเงื่อนไขทางคลินิกไว้แล้ว** เช่น Chlorpheniramine "เหมาะกับน้ำมูกใสและเหลว", Phenylephrine+CPM "ใช้เมื่อมีน้ำมูกไหลและคัดจมูกร่วมด้วย ไม่เหมาะกับน้ำมูกข้นเหนียว", Decolgen "เมื่อมีไข้ ปวดหัว น้ำมูกไหล และคัดจมูก" -> ใช้เป็นฐานของ Gateway ได้แบบ data-driven
  - มี URL อ้างอิงแนบบางยา (Kamilosan, Propoliz, Brompheniramine) อยู่ในเนื้อ chunk แล้ว
- **Expert Opinion ใน embedding**: `AAFP_0021` (หน้า 5) มี "Note on Thai Clinical Practice" เรื่องไซนัส <10 วัน
- **RDU Practice (Centor)** อยู่ใน `AAFP 2022_new.pdf` หน้า 4 เป็น highlight/annotation **ไม่มี text layer** -> ไม่อยู่ใน embedding -> ใส่ผ่าน prompt ตามที่กำหนด
- **Probe retrieval (cosine) กับเคส feedback** -- พบ root cause ของปัญหา 2-3 ชัดเจน:

| เคส | Dose chunk ที่ cosine ดึงได้ |
|---|---|
| A1 (เสียงแหบ คัดจมูก ไม่มีน้ำมูก) | Phenylephrine + Brompheniramine (ตัวเดียว) |
| A4 (ไข้หวัดใหญ่: ไข้ ไอ น้ำมูก) | Decolgen/TIFFY (ตัวเดียว) |
| B1 (ไซนัส น้ำมูกข้นเขียว) | PE+BPM, PE+CPM, Diphenhydramine, Mometasone -- **ตรงกับยาที่อาจารย์บอกว่าไม่ควรจ่ายทั้งหมด** |

---

## 3. Root Cause Analysis

1. **Cosine bias ต่อยาสูตรผสม**: chunk สูตรผสมพูดถึงหลายอาการพร้อมกัน (ไข้+น้ำมูก+คัดจมูก) -> similarity สูงกว่ายาเดี่ยวเสมอ -> LLM เห็นยาในตารางแค่ 1-3 ตัว และมักเป็นสูตรผสม -> ตอบตัวเดิมซ้ำ/ผิดอาการ
2. **Query expansion เดิมพาไปทาง decongestant**: map `น้ำมูก -> "... congestion decongestant"`, `ยาลดน้ำมูก -> "antihistamine decongestant pseudoephedrine"` ดันสูตรผสมขึ้นอีกชั้น
3. **ตัวอย่างใน SYSTEM_PROMPT เองชี้นำ**: ข้อ 3b ยกตัวอย่าง "ยาลดน้ำมูก (Antihistamine/Decongestant): **Chlorpheniramine + Phenylephrine**" + "ชื่อยาจริง 1-2 ตัว" -> โมเดลเลียนแบบ
4. **Prompt ไม่มีหลักเภสัชวิทยาการเลือกยาตามลักษณะอาการ** (น้ำมูกใส vs ข้น, ไอแห้ง vs มีเสมหะ, ยาอม vs ยาพ่น vs ยากลั้วคอ, decongestant = ยาแก้คัดจมูก)
5. **ไม่มี pattern ซักประวัติที่ตรวจได้**: LLM ต้องเดาเองว่าขาดอะไร -> มักข้ามไปตอบเลย และเอาคำถามไปซ่อนท้ายคำตอบ
6. **Expert Opinion ไทยไม่อยู่ใน Context** (RDU ไม่ถูก ingest) -> ตอบแต่ AAFP
7. เลขหน้า Dose ใน prompt/คำตอบยังอิง **ตารางเก่า** (เช่น Paracetamol หน้า 11/27) และ prompt บอกช่วงหน้า Dose 1-53 (ตารางใหม่มี 45 หน้า)

---

## 4. ทางเลือกที่พิจารณา -> Best Solution

| ทางเลือก | ข้อดี | ข้อเสีย | ตัดสิน |
|---|---|---|---|
| A. แก้ prompt อย่างเดียว (ใส่กฎ + รายชื่อยา) | ง่าย | Context ยังมียาแค่ 1-3 ตัว (สูตรผสม) -> ไม่มีตัวเลือกให้เลือกจริง; hard-code รายชื่อยาใน prompt ไม่ scale | ไม่พอ |
| B. Re-chunk / re-embed / ingest Expert Opinion | แก้ที่ต้นทาง | **ขัด constraint** (ห้ามแตะ Ingestion) + เสี่ยง embedding เพี้ยน | ห้ามทำ |
| C. ยิง vector search เพิ่มรายกลุ่มยา / LLM rerank | recall ดีขึ้น | +latency ต่อคำถาม (API call เพิ่ม), ยังขึ้นกับ cosine | ไม่คุ้ม |
| **D. Query-time Formulary Catalog + Symptomatic Gateway + Prompt pattern** (เลือก) | **0 API call เพิ่ม** (อ่าน chunks.jsonl ในหน่วยความจำ), data-driven จากข้อบ่งใช้ใน Dose table เอง, ครอบคลุมยาทั้ง 53 ตัว, ตัด chunk ที่ไม่เหมาะออกจาก Context ได้ | Context ยาวขึ้น ~3-4k tokens (latency แทบไม่เปลี่ยน -- ดูข้อ 6) | **Best / Optimal** |

---

## 5. Solution ที่ทำ (รายละเอียด)

### 5.1 Symptomatic Gateway + Formulary Catalog (`backend/symptomatic_gateway.py` -- ใหม่)
1. **Formulary**: โหลด Dose chunks จาก `chunks.jsonl` (read-only, lazy cache) รวมแถวผู้ใหญ่+เด็ก -> 53 ยา แล้ว **จัดกลุ่มยาจาก "ข้อบ่งใช้" ในตารางเอง** (ไม่ hard-code รายชื่อยา): ยาแก้ปวด/ลดไข้, antihistamine เดี่ยว, ยาแก้คัดจมูกเดี่ยว, สูตรผสม decongestant+antihistamine, สูตรผสม 3 ตัวยา, INCS, ยาแก้ไอแห้ง, ยาละลายเสมหะ, ยาพ่นคอ, ยาอม, ยากลั้วคอ + ตรวจ **อายุขั้นต่ำ** จากข้อความในตาราง (เช่น Propoliz ≥3 ปี, Strepsils ≥6 ปี)
2. **Case features (รองรับคำปฏิเสธภาษาไทย)**: ไข้, ปวด, น้ำมูก (ใส/ข้น), คัดจมูก, ไอ (แห้ง/มีเสมหะ), เจ็บคอ, เสียงแหบ, ภูมิแพ้, ไซนัส, หู, โรคร่วม (ไต ตับ ตั้งครรภ์ แผลกระเพาะ ความดัน หอบหืด), อายุ/น้ำหนัก -- เช่น "ไม่มีน้ำมูก", "ไม่ไอ" = ไม่มี, "ไม่มีไข้สูง" = ไม่ทราบ (ไม่ใช่ไม่มีไข้)
3. **Gateway ตัดสินรายกลุ่ม fit / conditional / avoid** ตามหลักเภสัชวิทยา (สอดคล้องข้อบ่งใช้ใน Dose table):
   - สูตรผสม decongestant+antihistamine ต้องมี **ทั้งน้ำมูกไหลและคัดจมูก**; สูตร 3 ตัวยา (Decolgen/Tiffy) ต้องมี **ไข้+น้ำมูกไหล+คัดจมูก** และไม่ใช้ในไซนัส
   - น้ำมูกข้นเหนียว/ไซนัส -> antihistamine รุ่นที่ 1 และสูตรผสมที่มี CPM = **avoid** -> ล้างจมูกด้วยน้ำเกลือ
   - คัดจมูกอย่างเดียว -> ยาแก้คัดจมูกชนิดเดี่ยว; ไอไม่ทราบลักษณะ -> ทั้งสองกลุ่มเป็น conditional (ต้องถาม)
   - INCS เฉพาะภูมิแพ้/เรื้อรัง/กลับเป็นซ้ำ/ผู้ใช้ขอ; เด็ก <4 ปี = ไม่ใช้ยาแก้ไอ/แก้แพ้/ลดน้ำมูก/แก้คัดจมูก
4. **DOSE CATALOG** แนบท้าย Context: ทุกยาในกลุ่มที่เหมาะ + ขนาดยา **ตัดตอนตรงจากตาราง (verbatim, ไม่ตัดกลางตัวเลข)** ตามกลุ่มอายุ + เลขหน้า + (ถ้ามีโรคร่วม) คอลัมน์ปรับขนาดตามไต/ตับและข้อห้าม + รายการ "ไม่เหมาะกับเคสนี้ + เหตุผล" + ทางเลือกที่ไม่ใช้ยา
5. **ตัด Dose chunk ที่ gateway ตัดสินว่าไม่เหมาะ** ออกจาก Context (เช่น B1 สูตรผสมทั้ง 3 + Mometasone ถูกตัด)
6. **Brand Gateway (กันเชิงโฆษณา -- ตรวจซ้ำหลังโมเดลตอบ)**: ชื่อการค้าที่ไม่มีตัวยาสำคัญในบรรทัดเดียวกัน -> เติม "(ตัวยา: ...)" อัตโนมัติ (ดึงตัวยาจากชื่อในตาราง เช่น Strepsils dry cough = Dextromethorphan, Decolgen = Paracetamol + CPM + Phenylephrine; ผลิตภัณฑ์ที่ชื่อไม่บอกตัวยายืนยันจากเนื้อหาแถวยาเดียวกัน) ทำงานทั้ง non-stream และ streaming (กันบรรทัดที่มีชื่อการค้าไว้จนจบบรรทัด)
7. **History-taking pattern (deterministic)**: สร้างบันทึก "ทราบแล้ว / ข้อมูลขั้นต่ำที่ขาด / ต้องยืนยันก่อนจ่ายยา / ข้อมูลช่วยเลือกยาที่ขาด" แนบใน user message -- ประวัติแพ้ยาที่ไม่ทราบอย่างเดียวไม่ทำให้เป็นประเภท 4 แต่ต้องถามก่อนจ่าย ATB; เด็กต้องมีอายุ+น้ำหนัก; ผู้ใช้ระบุการวินิจฉัยมาแล้ว = ไม่นับอาการที่ไม่ได้บอกเป็นข้อมูลไม่ครบ
8. **Practice flags**: เคสเจ็บคอ -> ต้องคำนวณ Centor จริง + บล็อก RDU; ไซนัสผู้ใหญ่ -> บล็อก Thai practice + ยืนยันแพ้ยา (ไม่ดันในเคสข้อมูลไม่ครบ)

### 5.2 `backend/rag_engine.py` (ต่อยอดของเดิม ไม่ refactor)
- **SYSTEM_PROMPT**: เพิ่มหัวข้อ **EXPERT OPINION / RDU PRACTICE** (RDU Centor 3-5 จ่าย / <3 เลี่ยง; Thai sinusitis note ทั้งอังกฤษ+ไทย; ต้องระบุชื่อยา+ขนาด+ระยะเวลาเมื่อบอกว่าพิจารณาจ่าย), **HISTORY-TAKING PATTERN**, เขียน **3b ใหม่** (ตัวเลือก 2-4 ตัว, หลักเลือกยาตามลักษณะอาการ, ศัพท์ decongestant = ยาแก้คัดจมูก, ไม่ผูกยี่ห้อ, Strepsils ต้องเรียกสูตรเต็ม, ทางเลือกไม่ใช้ยาไม่ต้องอ้าง Dose, ปิดท้ายเชิญถามต่อ), **รายชื่อโรค EN-TH**, Self-verify (ช)(ซ), แก้ช่วงหน้า Dose 1-45 และตัวอย่างหน้า Dose ที่ล้าสมัย, ลบตัวอย่างที่ชี้นำสูตรผสม
- **USER_MESSAGE_TEMPLATE**: เพิ่ม `{clinical_notes}` + คำสั่งข้อ 11-12 (ใช้ Catalog, บล็อกปฏิบัติจริง)
- **Query expansion**: `น้ำมูก -> antihistamine`, `คัดจมูก -> topical decongestant`, `ยาลดน้ำมูก -> antihistamine`, เพิ่ม `ยาแก้คัดจมูก`, ขยาย `ไอ`/`เสมหะ` ด้วยชื่อยาในตารางใหม่
- **Citation**: เลขหน้า [Ref: Dose] ต้องตรงยาที่อยู่ในบรรทัดนั้น (แก้เลขหน้าตารางเก่าอัตโนมัติ เช่น Paracetamol 11 -> 8; เติมหน้าให้ [Ref: Dose] เปล่า), ตัด [Ref: Dose] ที่แปะกับ "น้ำเกลือ" (ไม่ใช่ยาในตาราง), เพิ่มหน้า Dose ที่ถูกอ้างจริงเข้าแผงอ้างอิง
- **Follow-up**: คำถามต่อเนื่อง (เช่น "อยากรู้", "ถ้าเป็นโรคไตใช้ได้มั้ย", "ใช้ตัวไหนแทน") ใช้อาการของเคสล่าสุดในแชทสร้าง Catalog (ไม่หลุดเคส) + โหมดแสดงตัวเลือกทั้งหมด; คำถาม ATB ล้วนไม่แนบ Catalog
- **Streaming**: `full_answer` = ข้อความที่ผู้ใช้เห็นจริง (ผ่าน sanitize + brand gateway เหมือนกัน)
- **ความปลอดภัยของระบบ**: ชั้นเสริมทั้งหมดอยู่ใน try/except -- error ใดๆ ในชั้นนี้จะไม่ทำให้คำตอบหลักล้ม; เคสนอกขอบเขต (weak context หรือไม่มีอาการ URI เช่น ปวดท้อง/ปวดหลัง) ไม่ผ่าน gateway

---

## 6. ผลการ Validate (Gemini `gemini-3.1-flash-lite`, RERANK_MODE=vector, Qdrant Cloud 234 docs)

| ชุดทดสอบ | จำนวน | ผลรอบสุดท้าย |
|---|---|---|
| **เคส Feedback เดิม** (A1-A6, B1-B5, B7, B8, C1) + Expert Case B + บทสนทนา Ex2->Ex3->Ex4 | 18 | **18/18** |
| **เคสใหม่ที่โมเดลไม่เคยเห็น** (N1-N12: คัดจมูกอย่างเดียว, ไอแห้ง, ไอมีเสมหะ+ความดัน, เด็กมีน้ำหนัก, ตั้งครรภ์, CKD, เด็ก <4 ปี, Centor 4, ข้อมูลไม่ครบ, คัดจมูก+น้ำมูกใส, เสียงแหบ, ไซนัสแย่ลง <10 วัน) | 12 | **12/12** |
| **Follow-up: Hallucination / Conversation Drift** (3 chains 10 turns + streaming chain 3 turns) | 13 | 12/13 -> พบ FC1-T2 (ดูข้อ 6.1) -> แก้แล้ว retest **3/3** + เคสแพ้ยาทั้งหมด **8/8** |
| **Regression รอบก่อนหน้า** (regression 21 + opt8 20 + other 6) | 47 | **47/47** |
| Syntax / Static check (`pyflakes`) | 2 ไฟล์ | 0 issue |
| Robustness (input ว่าง/มั่ว/ยาว 5,000 ตัวอักษร/history ผิดรูป/[Ref] เสีย, stream) | 91 calls | 0 exception |

- **Latency**: เฉลี่ย 5.0-6.4 วินาที/คำตอบ (regression suites) เคสคลินิกเต็ม 6-9 วินาที -- **ไม่มี API call เพิ่ม** (Catalog สร้างจากหน่วยความจำ ~0 ms) Context เพิ่ม ~3-4k tokens ในเคสหลายอาการ
- **Streaming**: ข้อความที่ผู้ใช้เห็น = `full_answer` ที่บันทึก (ตรวจใน streaming chain ทุก turn)

### 6.1 รอบการทดลอง -> ปัญหาที่พบระหว่างทางและการแก้ (Iterate จนได้ Best Solution)

| รอบ | สิ่งที่พบ | การแก้ |
|---|---|---|
| 1 | Brand gateway จับ "M" ใน "Propoliz **m**outh" แทรกกลางคำ / ใส่ตัวยาให้ Augmentin ซ้ำทั้งที่มี Amoxicillin/clavulanate แล้ว | ใส่ word-boundary + คำพ้อง (ไทย/อังกฤษ) ให้นับตัวใดตัวหนึ่งพอ |
| 1 | โมเดลอ้าง `[Ref: Dose, หน้า 20/21/43]` กับ "ล้างจมูก/กลั้วคอด้วยน้ำเกลือ" (ไม่มีในตาราง) | ตัด [Ref: Dose] ที่อยู่กับน้ำเกลือและไม่มียาในตาราง + prompt ห้ามอ้าง |
| 1 | เคสข้อมูลพอ/วินิจฉัยมาแล้ว (B4, B5, C1) ถูกติดป้าย "ประเภท 4" และแทรกหัวข้อเป็นเลข 2 | แยก "ต้องยืนยันก่อนจ่ายยา" (แพ้ยา) ออกจากข้อมูลขั้นต่ำ, ตรวจ "ระบุการวินิจฉัยมาแล้ว", ขาด ≤1 = ประเภท 2 เท่านั้น, หัวข้อย่อยไม่ใส่เลข |
| 1 | "อยากรู้" (Ex3) ตอบภาพรวมโรคแทนรายการยา | โหมด "ขอดูตัวเลือกยา" สำหรับ follow-up + บังคับบรรทัดเชิญถามต่อท้าย 3b |
| 2 | G12 (กรดไหลย้อน) ถูกดันเป็นประเภท 2 เพราะ "ปวด" | Gateway/บันทึกซักประวัติทำงานเฉพาะเคสที่มีอาการ URI (ปวดอย่างเดียว = นอกขอบเขต) |
| 2 | "จาม" เฉยๆ ถูกนับเป็นภูมิแพ้ -> INCS โผล่ในหวัด | ภูมิแพ้ต้องมี คัน/จามบ่อย/ภูมิแพ้/เป็นๆหายๆ |
| 3 | C3 (เด็ก 4 ขวบ ข้อมูลไม่ครบ) โผล่ Azithromycin จากบล็อก RDU; C6 (เด็กมีน้ำหนักไม่มีอายุ) ถูกนับว่าข้อมูลไม่ครบ; O2 ไม่มี Paracetamol | บล็อก RDU เฉพาะเคสข้อมูลพอ; เด็กมีน้ำหนัก = อายุเป็นข้อมูลเสริม (ตามเกณฑ์เดิม); หวัดที่ไม่ปฏิเสธไข้/ปวด ใส่ Paracetamol prn |
| 4 | **FC1-T2 (ความปลอดภัย)**: แพ้ penicillin "ผื่นลมพิษ" ถูกตีเป็น non-type 1 แล้วแนะนำ Cephalexin | **Safety backstop** ตรวจความรุนแรงการแพ้แบบ deterministic (รองรับคำปฏิเสธ เช่น "ไม่มีลมพิษ") -> type 1 = ห้าม beta-lactam รวม cephalosporin / ผื่นเล็กน้อย = cephalosporin ได้ -> retest 3/3 + เคสแพ้ยา 8/8 |

---

## 7. การประเมินตาม Feedback (รายละเอียดเทียบเก่า-ใหม่ใน `Jack_prompt/Test_Case/case_old_1.md` / `.csv`)

| Feedback | ผลใหม่ |
|---|---|
| A1 ไม่มีน้ำมูก ไม่ควรใช้สูตรผสม | ยาแก้คัดจมูกชนิดเดี่ยว Oxymetazoline/Xylometazoline (สูตรผสมถูก gateway ตัด) |
| A2 INCS ในเด็กเฉียบพลัน | ไม่แนะนำ INCS / ยาแก้ไอ/แก้แพ้ในเด็ก <4 ปี; น้ำเกลือ + ขอน้ำหนัก |
| A3 decongestant = ยาแก้คัดจมูก | จัดหมวดถูก + ล้างจมูกน้ำเกลือ |
| A4 น้ำมูก -> antihistamine เดี่ยว, Decolgen ไม่ใช่แค่ CPM+PE | Cetirizine/CPM เป็นตัวเลือก, ไม่มี Decolgen (ไม่มีคัดจมูก), ถ้ายกตัวอย่าง Decolgen ระบบเติมตัวยาครบ 3 ตัว |
| A5 ยาแก้ปวดทางเลือก (NSAIDs) | Ibuprofen mg/kg + ข้อควรระวัง + ห้าม Aspirin ในเด็ก |
| A6 ไอต้องถามลักษณะก่อน / Strepsils เรียกสูตรเต็ม / ยาพ่นคอ | แยกไอแห้ง-มีเสมหะพร้อมถาม, ยาพ่นคอ Propoliz, ไม่มี "Strepsils (A หรือ B)" |
| B1/B7 น้ำมูกข้น ไม่ใช้ CPM/BPM สูตรผสม -> ล้างจมูก | ตัดสูตรผสม/ยาแก้แพ้รุ่นที่ 1 + ล้างจมูกน้ำเกลือเป็นหลัก |
| B2 NSAIDs + ล้างจมูก, ไม่ใช่ Decolgen ในไซนัส | Paracetamol/Ibuprofen + ล้างจมูก + ไม่มี Decolgen |
| B3 เด็กเจ็บคอ -> กลั้วน้ำเกลือ / Propoliz (≥3 ปี) | Propoliz + กลั้วคอน้ำเกลือ (ตรวจอายุขั้นต่ำจากตาราง) |
| B4 ยาอม vs ยากลั้วคอ / ยาพ่นคอ | แยกหมวดถูก + Kamilosan/Betadine throat spray + RDU Centor 3 (ตรง Expert Case A) |
| B5 INCS ไม่จำเป็นในผู้ใหญ่ | ไม่มี INCS, Cefixime/Doxycycline ตามชนิดการแพ้ |
| B8 antihistamine เดี่ยว ไม่ใช่สูตรผสม | Cetirizine/Loratadine/Fexofenadine/Bilastine |
| C1 ยาอม -> ยากลั้วคอ | หมวดถูก (ยาพ่นคอ/กลั้วคอ) + Clindamycin/Azithromycin |
| Expert Case A/B | บล็อก "ในทางปฏิบัติจริง (บริบทร้านยาไทย)" + ถามแพ้ยาก่อนจ่าย ATB |
| Ex2-Ex4 | จัดยาเป็นกลุ่ม+ตัวเลือก, "อยากรู้" -> รายการยา, "โรคไต" -> เลี่ยง NSAIDs ใช้ Paracetamol (อิงคอลัมน์ไต/ตับ) |

**ประเมินรวม**: ถูกต้อง (ยาตรงลักษณะอาการตามข้อบ่งใช้ในตาราง + safety backstop), ครบถ้วน (ทุกกลุ่มอาการ + Guideline + ปฏิบัติจริง + ซักประวัติ), หลากหลาย (ตัวเลือก 2-4 ตัวต่อกลุ่มจากยาทั้ง 53 ตัว แทนยาตัวเดียวจาก cosine) และสิ่งที่ดีอยู่แล้วไม่แย่ลง (regression 47/47)

---

## 8. ข้อจำกัด / ความเสี่ยงที่เหลือ (บันทึกตามจริง)
- LLM มีความไม่แน่นอน (stochastic) -- การตรวจใช้ keyword checks + อ่านคำตอบจริงประกอบ; ความผิดพลาดด้านความปลอดภัยที่พบ (แพ้ลมพิษ) แก้ด้วย deterministic backstop แล้ว แต่ควรให้อาจารย์ spot-check ต่อ
- ข้อมูลความปลอดภัยใน **หญิงตั้งครรภ์/ให้นมบุตร** ส่วนใหญ่ไม่อยู่ในตาราง Dose -> ปรับ prompt ให้แยกเป็น "ข้อมูลนอกคู่มือ" และห้ามอ้าง [Ref: Dose/AAFP] แล้ว แต่ยังขึ้นกับการทำตามของโมเดล
- การสกัดอาการเป็น rule-based ภาษาไทย -- วลีแปลกๆ อาจตรวจพลาด (บันทึกซักประวัติระบุให้ LLM ยึดข้อความผู้ใช้เป็นหลักเสมอ)
- เคสหลายอาการ Catalog ยาว (~9-15k ตัวอักษร) -- ไม่กระทบ latency ที่วัดได้ แต่เพิ่ม token ต่อคำถาม
- `RDU Practice` อยู่ใน `AAFP 2022_new.pdf` (highlight) แต่ frontend เปิด `AAFP_2022_Original.pdf` -> ลิงก์ [Ref: AAFP, หน้า 4] เปิดหน้า Table 2 ได้ถูก แต่ไม่เห็น highlight
- Muclear ไม่มีชื่อตัวยาในตาราง -> brand gateway เติมตัวยาให้ไม่ได้ (ไม่เดาเอง)
- โฟลเดอร์ `Jack_prompt/eval/` ถูกลบใน working tree ก่อนเริ่มงาน (ไม่ได้ลบโดยรอบนี้) -- รอบนี้รัน suite จากสำเนาใน git HEAD ชั่วคราว และไม่ได้กู้คืนเข้า repo

## 9. Next Steps
1. เปลี่ยน `PDF_FILENAMES["AAFP"]` เป็น `AAFP 2022_new.pdf` (9 หน้าเท่ากัน) เพื่อให้เห็น highlight RDU / Thai note ตอนกดอ้างอิง
2. ทีม Ingestion: เพิ่มคอลัมน์ Pregnancy/Lactation และชื่อตัวยาของผลิตภัณฑ์ (เช่น Muclear) ใน Dose table
3. Frontend: ปุ่ม/ป๊อปอัป "ดูยาในร้าน" (ไอเดีย Ex2) -- backend รองรับแล้ว (ส่ง "อยากรู้"/"มีตัวเลือกอะไรบ้าง" = โหมดแสดงตัวเลือกยา)
4. ให้อาจารย์ทบทวนกฎ Gateway (ตาราง fit/avoid) และเพิ่ม Expert Opinion ข้ออื่นในหัวข้อ EXPERT OPINION ของ prompt (โครงสร้างรองรับการเพิ่มข้อ)
5. ทดสอบกลุ่ม "เคสข้อมูลไม่ครบ" (ประเด็นที่ 3 ที่ทีมยังไม่ได้ทดสอบ) บน business domain จริง
6. ถ้าต้องการ: กู้ `Jack_prompt/eval/` กลับเข้า repo และเพิ่มชุดทดสอบรอบนี้ (old/new/follow-up) เป็น regression ถาวร

---

## 10. ไฟล์ที่เปลี่ยน
| สถานะ | ไฟล์ | สรุป |
|---|---|---|
| ใหม่ | `backend/symptomatic_gateway.py` | Formulary/Case features/Gateway/Catalog, History-taking pattern, Practice flags, Allergy backstop, Brand gateway, Dose page lookup |
| แก้ | `backend/rag_engine.py` | SYSTEM_PROMPT (Expert Opinion, History pattern, 3b ใหม่, ชื่อโรค EN-TH, Self-verify), USER template, query expansion, citation/stream post-process, integration ใน `generate_answer` + `generate_answer_stream` |
| ใหม่ | `Jack_prompt/Test_Case/case_old_1.md`, `case_old_1.csv` | เปรียบเทียบคำตอบเก่า-ใหม่ + Diff ของเคส Feedback |
| ใหม่ | `Jack_prompt/result/Phase2_result_opt_1.md` | รายงานนี้ |
| ไม่แตะ | `rag/data/chunks.jsonl`, Qdrant (`pharmacy_docs`), ไฟล์ข้อมูล/PDF, frontend, config | อ่านอย่างเดียว |
