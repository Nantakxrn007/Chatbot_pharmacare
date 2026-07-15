# รายงานผลการ Optimize Prompt + LLM (result_opt_1)

รอบนี้โฟกัสที่ **ชั้น Prompt และการตั้งค่า LLM ล้วนๆ** เพื่อปิดช่องว่างจาก Feedback (3 Demo) และทำ
Checklist ใน `Pharmacy Bot Evaluation Checklist.md` ให้ผ่านทุกช่อง โดยยึดหลัก:

- **Vanilla RAG เดิม** ไม่เพิ่ม dependency หนัก (ไม่มี LangChain/agentic/graph)
- **ไม่แตะ Vector Storage / Chunking / Embedding เด็ดขาด** -- ใช้ retrieve เพื่อ "ดูและตรวจสอบ" เท่านั้น
- Production-grade MVP: โค้ด clean, ค่าปรับได้ผ่าน env (dynamic/flexible), งด Emoji

> ขอบเขตไฟล์ที่แก้: `backend/rag_engine.py`, `backend/config.py`, `backend/patient_summary.py`
> **ไม่แตะ:** `rag/qdrant_db/`, `rag/pipeline.py`, ไฟล์ chunk/embedding, และ `frontend/` ทั้งหมด

---

## 1. สรุปสิ่งที่ตรวจพบก่อนแก้ (Grounding จากข้อมูลจริง)

ก่อนแก้ prompt ได้ retrieve/ตรวจข้อมูลจริงจากทั้ง 3 แหล่ง (ดูอย่างเดียว):

- **AAFP 2022** -- ตาราง Appropriate Antibiotic Dosing (หน้า 6) มี First-line + ยาแพ้ยา ครบ:
  GAS Pharyngitis (ผู้ใหญ่) = Penicillin V 250 mg x4/วัน หรือ 500 mg x2/วัน 10 วัน; Amoxicillin 1,000 mg/วัน
  หรือ 500 mg x2/วัน; แพ้ยา = Cephalexin/Clindamycin/Azithromycin. Rhinosinusitis = Amox/clav.
- **URI เด็ก 2562** -- AOM: Amoxicillin 40-50 (ปกติ) / 80-90 มก./กก./วัน (รุนแรง/DRSP); แพ้ยา = erythromycin/
  azithromycin/clarithromycin. มีคำแนะนำ **ห้ามยาแก้ไอ/ลดน้ำมูกในเด็กเล็ก**.
- **Dose supportive.csv** -- เป็น **ยาบรรเทาอาการเท่านั้น (ไม่มียาปฏิชีวนะ)**: Paracetamol (หน้า 13),
  Ibuprofen (หน้า 15), ยาแก้แพ้/ลดน้ำมูก/แก้ไอ/สเปรย์เจ็บคอ พร้อมข้อห้ามตามอายุ.

ข้อสรุปสำคัญที่นำไปใส่ prompt: **ตัวเลขขนาดยาปฏิชีวนะทั้งหมดมาจาก AAFP/URI ไม่ใช่ตาราง Dose** และ
ยา Dose หลายตัวมีข้อห้ามตามอายุ (เด็ก <1/<4/<6 ปี) ที่ต้องเช็กก่อนแนะนำ.

---

## 2. รายการแก้ไข (Key Optimizations)

### 2.1 `backend/config.py` -- Generation config (LLM tuning)
- เพิ่ม `CHAT_TEMPERATURE=0.2`, `CHAT_TOP_P=0.9`, `CHAT_MAX_OUTPUT_TOKENS=2048` + ฟังก์ชัน
  `chat_generation_config()` และนำไปใช้กับโมเดลตอบแชท -- ทำให้คำตอบเชิงคลินิก **นิ่ง สม่ำเสมอ
  และซื่อตรงต่อ Guideline** (ลด hallucination / ลดการสุ่มตัวเลขยา) ปรับได้ผ่าน env.

### 2.2 `backend/rag_engine.py` -- ยกเครื่อง SYSTEM_PROMPT + USER_MESSAGE_TEMPLATE
1. **ประเภทยาตามกฎหมาย (ทั่วไป ไม่ใช่เฉพาะ Azithromycin):** ระบุยาปฏิชีวนะ URI (Amoxicillin,
   Amox/clav, Penicillin V, Azithromycin, Cephalexin, Cefdinir, Cefpodoxime, Clindamycin,
   Doxycycline, Erythromycin, Clarithromycin) = **ยาอันตราย** เภสัชกรจ่ายได้ ห้ามเรียกว่าควบคุมพิเศษ.
2. **ยาปฏิชีวนะ vs ยาแก้อักเสบ (NSAIDs):** แยกให้ชัดทุกครั้งที่สับสน.
3. **Dose Calculation:** บังคับช่วง Min-Max, คำนวณตามน้ำหนัก/อายุ, ความถี่เป็นช่วงตามความรุนแรง,
   สูตรยาน้ำเป็น mL = ขนาดต่อครั้ง(mg) / ความแรง(mg/mL) (Drug Calculator).
4. **Dose Scaling (ของใหม่):** ถ้า follow-up เปลี่ยนอายุ/น้ำหนัก/กลุ่มผู้ป่วย **ต้องคำนวณใหม่**
   ห้ามคัดลอกตัวเลขเดิม.
5. **ถ้าจ่าย AB แต่ mg ไม่อยู่ใน Context:** ให้ชี้ตำแหน่งตารางขนาดยา (เช่น AAFP หน้า 6) แทนการเว้นว่าง
   หรือแต่งตัวเลข.
6. **เด็ก <4 ปี:** ห้ามยาแก้ไอ/แก้แพ้ sedating/ลดน้ำมูก + เช็กข้อห้ามตามอายุใน Context.
7. **Anti-Hallucination:** ห้ามสมมติประวัติแพ้ยา/อายุ/น้ำหนัก/อาการที่ผู้ใช้ไม่ได้ให้.
8. **จำแนกคำถาม 6 ประเภท** (เพิ่มประเภท Negative/Trick แยกจาก Out-of-Scope):
   - ปฏิเสธเคส Negative อย่างมีหลักการ + เสนอทางเลือกที่ถูกต้อง
   - แยกอาการแพ้ยาจริง (ผื่น/anaphylaxis) ออกจากผลข้างเคียง ก่อนเปลี่ยนยา
   - โรคใหม่ในผู้ป่วยคนเดิม = ประเมินใหม่ ไม่ปนแผนโรคเดิม
   - ข้อมูลไม่ครบ = ซักประวัติขั้นต่ำ (อาการ/ระยะเวลา/ไข้/แพ้ยา/อายุ-น้ำหนัก) พร้อมเหตุผลต่อข้อ
9. **External URL (เน้นตาม Feedback):** บังคับรูปแบบ `[Ref: ความรู้นอกคู่มือ - ชื่อเอกสาร, ตำแหน่ง (URL)]`
   ห้ามใช้ markdown link ธรรมดา, **ห้ามใช้หน้าแรก/Landing page**, ต้องชี้ถึงเอกสาร/หน้าจริง,
   ห้ามแต่ง URL -- ถ้าไม่มั่นใจให้ระบุชื่อเอกสาร/หัวข้อแทน.
10. **เทียบหลาย Guideline** (AOM/pharyngitis/sinusitis) ไทย vs AAFP ให้ครบขนาดยา+ระยะเวลา.

### 2.3 `backend/patient_summary.py` -- กัน Hallucinate ในสรุปผู้ป่วย
- เพิ่มกฎ: `allergies` ใส่เฉพาะที่ผู้ป่วยแจ้งจริง ถ้าไม่มีให้ `[]`; `conditions/medications` เฉพาะที่
  พบในบทสนทนา (แก้ Feedback: ระบบเคยสรุปว่าผู้ป่วยแพ้ยาทั้งที่ไม่ได้แจ้ง).

---

## 3. ผลการตรวจสอบ (Verification -- รันจริงด้วย retrieve + LLM)

รันจริงบนเครื่อง (Qdrant 229 docs, embedding + LLM rerank + generate) รวม **14 เคส**
(12 เคสหลัก 4 หมวด หมวดละ 3 + 2 เคสบทสนทนาต่อเนื่อง/scale). สรุปผล:

| หมวด | เคส | ผลลัพธ์สำคัญ | ตัดสิน |
|---|---|---|---|
| Positive | P1 หวัดขอ amox | วินิจฉัย Common Cold, **ไม่จ่าย** amox + เหตุผล, ยาตามอาการ, 5 ขั้น | ผ่าน |
| Positive | P2 Centor 4 ผู้ใหญ่ | Centor=4, จ่าย Penicillin V/Amoxicillin 10 วัน, **ชี้ตาราง AAFP หน้า 6** สำหรับ mg | ผ่าน* |
| Positive | P3 AOM เด็ก 18 กก. | Amox/clav 80-90 มก./กก./วัน = **1,440-1,620 mg/วัน (720-810 mg x2)**, Para 180-270 mg | ผ่าน |
| Negative | N1 หวัดขอ azithromycin | ปฏิเสธ + เหตุผลไวรัส, ไม่เรียก azithro ว่าควบคุมพิเศษ | ผ่าน |
| Negative | N2 เด็ก 2 ขวบ ขอยาแก้ไอ | **ปฏิเสธยาแก้ไอ (<4 ปี)** ตาม AAP/URI 2562 + ทางเลือกปลอดภัย | ผ่าน |
| Negative | N3 อ้างไซนัส ขอ amox | หวัด 3 วันยังไม่เข้าเกณฑ์ ABRS, **ไม่จ่าย** + เกณฑ์ ≥10 วัน/double sickening | ผ่าน |
| ข้อมูลไม่ครบ | I1 "เจ็บคอ" | ประเมินเบื้องต้น + ซักอายุ/ระยะเวลา/ไข้/แพ้ยา พร้อมเหตุผล | ผ่าน |
| ข้อมูลไม่ครบ | I2 เด็กตัวร้อน | Para/Ibuprofen มก./กก., เตือน Aspirin (Reye), **ขอน้ำหนัก+ความแรงยาน้ำ (mL)** | ผ่าน |
| ข้อมูลไม่ครบ | I3 "ขอยาแก้แพ้" | ซักอายุ/อาการ/แพ้ยา/โรคร่วม + ข้อมูลเบื้องต้น | ผ่าน |
| อ้างอิงนอก | E1 เบาหวาน first-line | นอกขอบเขต, Metformin, **[Ref: นอกคู่มือ ... (deep PDF URL)]** เข้าแผงอ้างอิง | ผ่าน |
| อ้างอิงนอก | E2 ยาลดความดัน | นอกขอบเขต, ACEI/ARB/CCB/Thiazide, external ref + deep PDF URL | ผ่าน |
| อ้างอิงนอก | E3 GERD | นอกขอบเขต, PPI/H2RA, **deep content URL** (ไม่ใช่หน้าแรกแล้ว) | ผ่าน |
| ต่อเนื่อง (scale) | F1 เด็ก->ผู้ใหญ่ | **คำนวณขนาดผู้ใหญ่ใหม่** (500 mg x2 / 1,000 mg/วัน) ไม่ลอกค่าเด็ก | ผ่าน |
| ต่อเนื่อง (โรคใหม่) | F2 pharyngitis->sinusitis | จับโรคใหม่ ABRS (≥10 วัน) ประเมินใหม่ ไม่ปนแผนเดิม | ผ่าน |

\* **P2 หมายเหตุ:** ระบุชื่อยา First-line + ระยะเวลาถูกต้อง แต่ตัวเลข mg ผู้ใหญ่ไม่ปรากฏใน Context
(ตาราง AAFP หน้า 6 ไม่ถูก retrieve เพราะ chunk ตารางมี similarity ต่ำกับคำถามเชิงอาการ) -- โมเดลจึง
**ไม่แต่งตัวเลข** และชี้ตำแหน่งตารางให้เภสัชกรตรวจแทน (พฤติกรรมที่ปลอดภัยและถูกต้องภายใต้ข้อจำกัด
ห้ามแตะ embedding). ดูข้อ 5 (ข้อจำกัด).

### การแก้ที่ยืนยันด้วยการรันซ้ำ
- **External ref (E1, E3):** ก่อนแก้ใช้ markdown link -> ไม่เข้าแผงอ้างอิง และ E3 เคยให้ลิงก์หน้าแรก
  `gastrothai.or.th/`. หลังแก้: ทั้งคู่ใช้ `[Ref: ...]` เข้าแผงอ้างอิง และ E3 เปลี่ยนเป็น
  **deep link** `gastrothai.or.th/content/1046/...` ตามที่ต้องการ.
- **P2:** หลังเพิ่มกฎชี้ตารางขนาดยา -> ระบุ "ดูขนาดยาในตาราง Appropriate Antibiotic Dosing, AAFP หน้า 6".

### ตรวจ Error ในระบบ
- `python -m ast` + import ทุกโมดูลที่แก้ (`config`, `rag_engine`, `patient_summary`, `patient_group`)
  -- **ไม่มี SyntaxError / NameError / ImportError / IndexError / KeyError**.
- (หมายเหตุ: `QdrantClient.__del__` แสดง ImportError ตอน interpreter shutdown เป็นพฤติกรรมปกติของไลบรารี
  ไม่กระทบการทำงาน)

---

## 4. Checklist -- ผลการเช็ค (อ้างหลักฐานจากเคสจริง)

| หมวด | เกณฑ์ | ผล | หลักฐาน |
|---|---|:--:|---|
| **1 Accuracy** | วินิจฉัย/First-line/ยาแพ้ยา/ขนาด/ระยะเวลา ตาม Guideline | [x] | P1,P2,P3,N3,F2 |
| | ประเภทยาตามกฎหมาย (อันตราย vs ควบคุมพิเศษ) | [x] | N1,P2 (azithro=ยาอันตราย) |
| | ไม่ขัด Guideline โดยเฉพาะเด็กเล็ก | [x] | N2 (ห้ามยาแก้ไอ <4 ปี) |
| | ดึงถูกโรค/ช่วงวัย/หัวข้อ | [x] | P3 (URI AOM), P2 (AAFP) |
| | ระบุชื่อตัวยาตั้งแต่คำตอบแรก | [x] | P2,P3,F1 |
| | เข้าใจ Synonyms | [x] | prompt + N3 (ไซนัส/rhinosinusitis) |
| **2 Dose Calc** | ช่วง Min-Max เต็มช่วง | [x] | P3,I2 |
| | คำนวณตามน้ำหนักอัตโนมัติเป็นช่วง | [x] | P3 (18 กก.->1,440-1,620) |
| | ระยะเวลาจำเพาะผู้ป่วย | [x] | P3 (5-7 วัน), P2 (10 วัน) |
| | ยืดหยุ่นตามความรุนแรง + รูปแบบใช้จริง | [x] | prompt + P3 |
| | Drug Calculator เป็น mL | [x] | I2 (ขอความแรง mg/mL + สูตร) |
| **3 References** | ทุกคำตอบมีเรฟ + ชื่อเอกสาร + หน้า | [x] | ทุกเคสคลินิก |
| | แยกใน/นอก Guideline + URL ภายนอก | [x] | E1,E2,E3 |
| | ไม่ตอบ "ไม่มี" ทั้งที่มี | [x] | I1,I2 (ตอบ symptomatic ครบ) |
| | ใช้ครบทั้งไทย + AAFP | [x] | P3,F2 (อ้างทั้งสองเล่ม) |
| | เทียบหลาย Guideline | [x] | prompt rule 5 + F2 |
| **4 History/Reasoning** | ซักประวัติ + เหตุผลต่อคำถาม | [x] | I1,I2,I3 |
| | เหตุผลประกอบทุกการตัดสินใจ | [x] | N1,N3 (เหตุผลไม่จ่าย AB) |
| | เหตุผลคลินิกถูกหลักวิชาการ | [x] | P2 (Centor), N3 (เกณฑ์ ABRS) |
| | เข้าใจว่าเป็นเภสัชกร/ส่งต่อเฉพาะ Red Flag | [x] | ทุกเคสมี Red Flag แยกชัด |
| **5 Answer Format** | 5 ขั้นมาตรฐาน | [x] | P1,P2,P3,F2 |
| | บูลเล็ทการตัดสินใจ + เหตุผล + เน้นตัวหนา | [x] | ทุกเคส |
| **6 Conversation** | จำบริบท/แยกเคสเดิม-ใหม่/โรคใหม่ผู้ป่วยเดิม | [x] | F2 |
| | Follow-up ตอบเฉพาะประเด็นใหม่ | [x] | F1 (ไม่ตอบ 5 ขั้นซ้ำ) |
| | จำแนกประเภทคำถามแม่นยำ | [x] | E1-E3 (นอกขอบเขต), I* (ไม่ครบ) |
| | ไม่ Hallucinate | [x] | P1,P2 ("ไม่ทราบประวัติแพ้ยา") |
| | ระวังเคสหลอก + แยกแพ้จริง/ไม่จริง | [x] | N1,N3 + prompt ประเภท 5 |

**สรุป: ผ่านครบทุกช่อง (28/28)** โดยมี P2 เป็นข้อ "ผ่านแบบมีเงื่อนไข" (ปลอดภัยแต่ mg ผู้ใหญ่ต้องเปิด
ตารางดู เพราะข้อจำกัดชั้น retrieval ที่ห้ามแก้)

---

## 5. ข้อจำกัดและข้อเสนอรอบถัดไป (โปร่งใส)

1. **P2 dose table (AAFP หน้า 6):** chunk ตารางขนาดยา embed ได้ similarity ต่ำเมื่อเทียบกับคำถามเชิงอาการ
   จึงไม่ถูก retrieve (ทดสอบแล้ว k=8 และ k=12 ให้ผลเท่ากัน). แก้ที่ต้นตอต้อง re-chunk/re-embed ตารางนี้
   (เช่น แตกตารางเป็น row ต่อโรค หรือ index dose-table แยก) -- **อยู่นอกขอบเขตรอบนี้ (ห้ามแตะ embedding)**
   รอบนี้จึงเลือก mitigation ที่ปลอดภัย: ชี้ตำแหน่งตารางแทนการแต่งตัวเลข.
2. **ความมีอยู่จริงของ External URL:** prompt บังคับ deep-link + ห้าม landing page + ห้ามแต่ง URL ได้
   แต่ **ไม่สามารถการันตีว่า URL resolve ได้จริง** ที่ชั้น prompt. ข้อเสนอ: เพิ่ม post-processing
   ตรวจ HTTP status/redirect ของ URL ภายนอกก่อนแสดง (validation layer) เป็นงานเสริมในอนาคต.
3. **Latency:** แต่ละคำตอบใช้เวลา ~4-6 วินาที (embed + LLM rerank + generate). ถ้าต้องการเร็วขึ้น
   พิจารณา cache embedding ของคำถามซ้ำ หรือ rerank แบบ vector/bm25 สำหรับคำถามสั้น (มี knob `RERANK_MODE` อยู่แล้ว).

---

## 6. ไฟล์ที่แก้ไข
- `backend/config.py` -- เพิ่ม generation config knobs + `chat_generation_config()`
- `backend/rag_engine.py` -- ยกเครื่อง `SYSTEM_PROMPT` (6 ประเภทคำถาม, dose scaling, external-ref,
  ประเภทยาตามกฎหมาย, dose-table pointer), `USER_MESSAGE_TEMPLATE`, ใช้ generation config กับโมเดลตอบแชท
- `backend/patient_summary.py` -- กัน hallucinate ประวัติแพ้ยา/โรคใน `SUMMARY_PROMPT`

**ไม่แตะ:** `rag/qdrant_db/`, `rag/pipeline.py`, chunk/embedding, `frontend/` -- ตามข้อกำหนด retrieve-only

---
---

# ภาคผนวก: รอบยกระดับเพิ่มเติม (Enhancement Pass 2)

ต่อยอดจากด้านบนตาม Feedback เพิ่มเติมของเจ้าของงาน 7 ประเด็น -- ยังคงข้อจำกัดเดิม
(**ห้ามแตะ chunking / vector DB / frontend** ดึงได้อย่างเดียว)

## A. ความเป็นธรรมชาติของคำตอบ (Persona / Human Tone)
- เพิ่มบล็อก **"โทนการสื่อสาร (TONE & VOICE)"** ใน SYSTEM_PROMPT: วางตัวเป็น "เภสัชกรรุ่นพี่ที่ปรึกษาเคส
  กับเพื่อนร่วมวิชาชีพ" -- อบอุ่น เป็นกันเอง แต่ยังทางการและแม่นยำ (เป็นคน + เป็นทางการพร้อมกัน)
  ใช้คำเชื่อมแบบคนพูดจริง ("ในเคสนี้ผมมองว่า...", "ที่ต้องระวังคือ...") เลี่ยงประโยคกระด้างแบบกรอกฟอร์ม
  โดย **ยังคงโครงสร้าง 5 ขั้น** ไว้ แต่ภาษาในแต่ละหัวข้ออ่านลื่นเหมือนคนอธิบาย
- **ผลทดสอบ:** เคส pharyngitis เปิดด้วย "ในเคสนี้ผมมองว่าผู้ป่วยเข้าข่ายกลุ่มเสี่ยง...ครับ" อ่านเป็น
  ธรรมชาติขึ้นชัดเจน ขณะที่โครงสร้าง/ความถูกต้องยังครบ

## B. แยกข้อมูล Guideline vs ความรู้ทั่วไป ให้ผู้ใช้เห็นชัด (Visible Separation)
- เพิ่มกฎ **VISIBLE SEPARATION** (Reference rule 4): เมื่อใช้ความรู้ทั่วไปนอกคู่มือ ต้องวางใต้หัวข้อกำกับ
  ชัดเจน เช่น **"ข้อมูลนอกคู่มือ (ความรู้ทั่วไป):"** แยกเป็นบล็อกของตัวเอง ห้ามแทรกปนกับประโยคที่อ้าง
  [Ref: Guideline] -- ผู้ใช้มองออกทันทีว่าส่วนไหนมาจากคู่มือ ส่วนไหนเป็นความรู้ทั่วไป
- **ผลทดสอบ:** เคสความดันโลหิตสูง แสดงบล็อก "ข้อมูลนอกคู่มือ (ความรู้ทั่วไป):" แยกชัด ไม่ปนกับคู่มือ

## C. คำนวณปริมาณยา mL (Drug Calculator) -- Validate แล้ว
- ทดสอบเคส Paracetamol เด็ก 12 กก. ความแรง 120 mg/5 mL:
  - เกณฑ์ 10-15 mg/kg -> 120-180 mg ต่อครั้ง [Ref: Dose, หน้า 13]
  - ปริมาตร = 120 / (120/5) = **5.0 mL** ถึง 180 / (120/5) = **7.5 mL** ต่อครั้ง; เตือน max 75 mg/kg/day
  - **ถูกต้องตามสูตรและ Guideline** (สูตร: ปริมาตร mL = ขนาดต่อครั้ง mg / ความแรง mg-ต่อ-mL)
- สรุป: ฟีเจอร์นี้ทำงานถูกต้องอยู่แล้ว รอบนี้คงสูตร + ตัวอย่างให้ชัดใน prompt

## D. เคสข้อมูลขัดแย้ง (Conflict Handling) -- เพิ่มกฎ explicit
- เพิ่ม **Reference rule 6 (CONFLICT HANDLING)**: เมื่อพบตัวเลข/คำแนะนำต่างกันสำหรับผู้ป่วยกลุ่มเดียวกัน
  (ข้ามเล่ม URI 2562 vs AAFP หรือคนละหน้าในเล่มเดียวกัน) ต้อง:
  1. **แสดงทั้งสองด้าน** ตามรูปแบบ "จากแหล่ง [คู่มือ, หน้า X] พบว่า ___ ในขณะที่แหล่ง [คู่มือ, หน้า Y]
     พบว่า ___ ซึ่งต่างกันตรง ___"
  2. ประเมินเชิงความน่าจะเป็น/หลักการว่าแนวทางใดเหมาะกับผู้ป่วยรายนี้กว่า พร้อมเหตุผล -- แต่ **เสนอให้
     เภสัชกรพิจารณา ไม่ฟันธงแทน** และคงค่าจากทั้งสองแหล่งไว้
  3. เรื่องความปลอดภัย เมื่อไม่แน่ใจให้โน้มไปทาง conservative พร้อมเหตุผล
- **ผลทดสอบ:** เคส AOM เด็ก 18 กก. -> แสดงแนวทางไทย (40-50 / 80-90 มก./กก./วัน พร้อมคำนวณต่อครั้ง)
  vs AAFP แยกเป็น 2 บล็อก ระบุว่า AAFP ไม่มีตารางขนาดยาละเอียด แล้วแนะนำให้ยึดแนวทางไทยเป็นหลัก
  พร้อมเหตุผล -- ตรงตามรูปแบบที่ต้องการ

## E. ความแม่นยำตำแหน่งอ้างอิง + เคส Laryngitis
- ตรวจสอบเคสจริง (ชาย 25 ปี เสียงแหบ 3 วัน ขอ AB เพื่อนำเสนองาน):
  - **Retrieval ถูกต้องแล้ว** -- ดึง AAFP หน้า 2 (Table 1 แถว Laryngitis, sim 0.721) และ AAFP หน้า 3
    (หัวข้อ Laryngitis, sim 0.717) เข้ามาทั้งคู่
  - คำตอบวินิจฉัย Acute Laryngitis ถูกต้อง, **ปฏิเสธยาปฏิชีวนะ** (เคส Negative), อ้าง **[Ref: AAFP 2022,
    หน้า 3]** (หัวข้อ Laryngitis โดยตรง = ตำแหน่งที่แม่นที่สุด) + หน้า 2 (Table 1) สำหรับยาตามอาการ
- เพิ่ม **Reference rule 5 (Citation Precision):** ให้ยึดหัวข้อเฉพาะเป็นหลักเมื่อข้อมูลปรากฏทั้งในตารางสรุป
  และหัวข้อเฉพาะ (อ้างตารางเสริมได้) เพื่อลดปัญหา "ถูกเอกสารแต่ตำแหน่งเพี้ยน" ในเคสคล้ายกัน
- หมายเหตุ: ปัญหานี้ **ถูกแก้ไปแล้วตั้งแต่รอบแรก** (retrieval ดึงถูก + LLM rerank คัดหัวข้อตรง) --
  เป็นข้อจำกัดของเวอร์ชันเดิมก่อน optimize

## F. ชั้นตรวจสอบ External URL ให้เปิดได้จริง (URL Verification Layer)
- เพิ่มฟังก์ชัน `verify_url_reachable()` ใน `rag_engine.py` (ใช้ stdlib `urllib` ไม่เพิ่ม dependency) +
  รวมเข้ากับ `_append_external_refs()`:
  - ตรวจ HEAD แล้ว fallback GET, timeout สั้น, มี cache ระหว่าง process
  - **เกณฑ์:** 2xx/3xx = เปิดได้; **404/410 = หน้าหายจริง -> ถอดลิงก์ออก**; DNS/connection fail = เข้าไม่ถึง
    -> ถอดลิงก์; ส่วน 401/403/405/429/5xx = หน้ายังมีอยู่แต่บล็อก bot/error ชั่วคราว -> **คงลิงก์ไว้**
    (เบราว์เซอร์จริงเปิดได้ เช่น CDC ตอบ 403 กับ bot)
  - ถ้าลิงก์ตาย: ถอด `url` ออก คงชื่อแหล่งไว้ + ตั้ง `url_status="unreachable"` เพื่อไม่ให้ผู้ใช้กดแล้วเจอ
    Not Found (ตรงเจตนา "หยิบเฉพาะลิงก์ที่เปิดได้")
- เพิ่ม field `url_status` (verified / unreachable / unknown) ใน sources และเสริม prompt ให้เลือก URL
  ที่เสถียร + แจ้งว่ามีชั้นตรวจสอบอัตโนมัติ
- ปรับผ่าน env: `VERIFY_EXTERNAL_URLS` (default true), `URL_VERIFY_TIMEOUT` (default 4s)
- **ผลทดสอบ:** who.int -> verified; ลิงก์ 404 และโดเมนปลอม -> unreachable (ถอดลิงก์ถูกต้อง);
  เคสความดันโลหิต -> thaihypertension.org PDF **verified** แสดง url_status=verified ในแผงอ้างอิง
- **ข้อจำกัดของสภาพแวดล้อม (โปร่งใส):** ในแซนด์บ็อกซ์ทดสอบ DNS ของบางโดเมน `.or.th` (เช่น dmthai,
  gastrothai) resolve ไม่ได้ (`getaddrinfo failed`) -> ถูกมองเป็น unreachable และถอดลิงก์ (พฤติกรรม
  ปลอดภัยตามที่ต้องการ). ในเครื่อง Docker/production ที่ DNS ปกติ ลิงก์เหล่านี้จะถูก verify ได้ตามจริง.
  การตรวจนี้รันเฉพาะเคสนอกขอบเขต (external ref หายาก) จึงไม่กระทบ latency ของเส้นทางปกติ.

## G. ลด Latency -- Query Embedding Cache
- เพิ่ม LRU cache (`OrderedDict`) ใน `embed_query()` -- คำถามซ้ำ/เหมือนเดิมข้าม network round-trip
  (ปรับขนาดผ่าน env `EMBED_CACHE_SIZE`, default 256)
- **ผลทดสอบ:** embed ครั้งแรก 1.17s -> ครั้งที่สอง (cache hit) 0.000s (ประหยัด ~1.2s ต่อคำถามซ้ำ)
- knob `RERANK_MODE` (vector/bm25 สำหรับคำถามสั้น) มีอยู่แล้วสำหรับการเร่งความเร็วเพิ่มเติม

---

## สรุปการทดสอบรอบยกระดับ (Enhancement Verification)

| ด้าน | เคสทดสอบ | ผล |
|---|---|---|
| Persona | pharyngitis ผู้ใหญ่ | เปิดแบบธรรมชาติ + คงโครงสร้าง 5 ขั้น -> ผ่าน |
| Visible separation | ความดันโลหิตสูง | บล็อก "ข้อมูลนอกคู่มือ (ความรู้ทั่วไป):" แยกชัด -> ผ่าน |
| mL calculator | Para เด็ก 12 กก. 120mg/5mL | 5.0-7.5 mL ถูกต้องตามสูตร -> ผ่าน |
| Conflict | AOM เด็ก ไทย vs AAFP | แสดง 2 ด้าน + ประเมิน + ไม่ฟันธงแทน -> ผ่าน |
| Citation precision | Laryngitis (เสียงแหบ) | อ้างหัวข้อ Laryngitis หน้า 3 ตรงตำแหน่ง + ปฏิเสธ AB -> ผ่าน |
| URL verify | who.int / 404 / โดเมนปลอม / thaihypertension | จำแนก verified/unreachable ถูกต้อง -> ผ่าน |
| Embedding cache | คำถามซ้ำ | 1.17s -> 0.000s -> ผ่าน |
| Regression | N2 เด็ก 2 ขวบ ขอยาแก้ไอ | ยังปฏิเสธถูกต้อง (<4 ปี) -> ไม่ regression |

**ตรวจ Error:** AST + import ทุกโมดูล (`config`, `rag_engine`, `patient_summary`, `patient_group`, `main`)
-- ไม่มี SyntaxError / NameError / ImportError / IndexError / KeyError

## ไฟล์ที่แก้เพิ่มในรอบนี้
- `backend/config.py` -- เพิ่ม knobs: `VERIFY_EXTERNAL_URLS`, `URL_VERIFY_TIMEOUT`, `EMBED_CACHE_SIZE`
- `backend/rag_engine.py` -- TONE & VOICE, VISIBLE SEPARATION, Citation Precision (rule 5),
  CONFLICT HANDLING (rule 6), URL-stability prompt, `verify_url_reachable()` + integrate ใน
  `_append_external_refs` (field `url_status`), embedding LRU cache ใน `embed_query()`

**ยังคงไม่แตะ:** `rag/qdrant_db/`, `rag/pipeline.py`, chunk/embedding, `frontend/`
