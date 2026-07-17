# รายงานการ Optimize รอบที่ 7 (result_opt_7)

รอบนี้โฟกัสตาม Feedback ใน `optimize_7.md` (ทดสอบ UI จริง ต่อจากรอบ 6) -- แก้ที่ **Latency + RAG + Prompt +
Intent/State + External Ref** โดยยึดหลักเดิม: **Vanilla RAG, ไม่แตะ chunk/vector/embedding/frontend**

> ขอบเขต: ไม่รัน `pipeline.py`, ไม่ re-embed, ไม่แก้ `rag/qdrant_db/` หรือ `frontend/`
> ทุกการเปลี่ยนแปลงอยู่ที่ query-time + generation ใน `backend/rag_engine.py`, `backend/config.py`
> เพิ่ม eval suite ที่ `Jack_prompt/eval/regression_suite.py`

---

## 0. สรุปผู้บริหาร (Executive Summary)

| ปัญหาจาก Feedback รอบ 7 | สาเหตุ | สิ่งที่แก้ | ผล |
|---|---|---|---|
| **Latency ช้ากว่าเดิมมาก** | LLM rerank = call เต็ม 1 ครั้ง/คำถาม (วัดได้ **8-18s**) | เปลี่ยน `RERANK_MODE` เป็น **vector** (0.05s) | ตัด 8-17s/คำถาม เลือก chunk ชุดเดียวกัน |
| พิมพ์ "เดี๋ยวมีอีกเคสแป๊ป" -> บอทแต่งเคสฝีหลังคอหอยขึ้นมาเอง | intent gate ไม่จับข้อความ "คั่นเวลา" | `_FILLER_RE` + กฎห้ามแต่งเคสเมื่อไม่มีข้อมูลผู้ป่วย | ตอบ "พร้อมรับเคส เชิญเลย" ไม่แต่งอาการ |
| เคสเจ็บคอ+ขอ ATB (ไม่รู้อายุ/ไอ) ไม่ยอมซักประวัติ จ่ายเลย | จัดเป็นประเภท 2 ทั้งที่ Centor ยังประเมินไม่ครบ | บล็อก Modified Centor + multi-type (ขาด input Centor = ซักก่อน) | ถามอายุ/ไอ/ต่อมน้ำเหลือง + โชว์ Centor พร้อมช่องที่ยังไม่ทราบ |
| ไม่โชว์ Modified Centor ในหลายเคส | prompt เดิมไม่บังคับ | บังคับแสดง Centor ทุกเคสคอ/หวัด/URI (ยกเว้น AOM/sinusitis) | ทุกเคส URI แสดง Centor + แปลผล |
| "การวินิจฉัย" มี "--" รก + อยากได้ bullet สรุปเพิ่ม | format เดิม | จัดใหม่ (ชื่อโรค + (โอกาส) + เหตุผลขึ้นบรรทัดใหม่) + bullet สรุป+แผนเฝ้าระวัง | อ่านง่ายขึ้น + มีสรุป "หากไม่ดีขึ้น 7-10 วัน เฝ้าระวังไซนัส" |
| คำว่า "ขอบบน/ขอบล่าง" ดูแปลก | wording | นำเสนอเป็น "ช่วง" โดยตรง (1,200-1,350 -> 600-675) | ไม่มีคำว่าขอบบน/ขอบล่างแล้ว |
| แพ้ยา บอกแค่ "เลี่ยง beta-lactam" ไม่บอกว่ายาอะไร | prompt ไม่ได้สั่งยกตัวอย่าง | กฎ: เลี่ยงกลุ่มใดต้องยกตัวอย่างยา 2-3 ตัว | "เลี่ยง beta-lactam (เช่น amoxicillin, penicillin V, cephalexin, cefdinir)" |
| เคสเด็ก AAFP vs URI ทับกัน ไม่โชว์ทั้ง 2 | conflict rule ยังไม่เน้นเด็ก | เน้นย้ำเคสเด็ก: โชว์ทั้ง 2 + คำนวณเทียบ + สรุป | narrative + คำนวณ 2 แนวทางเทียบ |
| รายละเอียด (110 mcg) หายเป็นบางครั้ง | prompt เดิมไกลจากจุดที่ generate | ย้ายกฎ "คงค่าในวงเล็บ" ไปไว้ที่หัวข้อ 3b (proximity) | สเปรย์พ่นจมูกระบุ (110 mcg) |
| ปวดหลัง/นอก URI ไม่จัดเป็นนอกขอบเขต | ตัวอย่างไม่ครอบคลุม | เพิ่มตัวอย่าง back pain ในประเภท 6 | จัดเป็นประเภท 6 |
| markdown link นอกคู่มือไม่ขึ้นแผงอ้างอิง | parser จับแค่ [Ref:] | `_MD_LINK_RE` normalize [text](url) เข้าแผง | link markdown ขึ้นแผงอ้างอิงด้วย |
| URL นอกคู่มืออาจไม่ตรงเนื้อหา | ตรวจแค่ reachable | content-match: fetch หน้า + เทียบ keyword ถ้าไม่ตรงถอดลิงก์ | ลิงก์ที่เนื้อหาไม่ตรงถูกถอดออก |

**ผลตรวจสอบรวม:** Regression suite อัตโนมัติ **21/21 (100%)** + unit tests (intent 12/12, retry 3/3)

**Latency:** overhead ของ retrieval ลดจาก **~8-18s -> ~0.2s** (ตัด LLM rerank), เวลารวมต่อคำตอบเหลือ ~10-16s
(ขึ้นกับความยาว output + โหลด API ของ Gemini ล้วนๆ) และ **streaming เห็น token แรกเกือบทันที** (ไม่ต้องรอ rerank)

---

## 1. Best Solution -- ออกแบบและเหตุผล

### 1.1 Latency: ตัด LLM rerank -> vector rerank (Impact สูงสุด)

**วัดจริง (retrieve_chunks ต่อ query, เทียบ 3 mode):**

| mode | latency | chunk ที่เลือก |
|---|---|---|
| **llm** (เดิม) | **8-18s** | AAFP p4,p5,p6,p7 ... |
| **vector** (ใหม่) | **0.05s** | AAFP p4,p5,p6,p7 ... (ชุดเดียวกัน) |
| bm25 | 0.06s | ปนตาราง Dose สูงเกิน (แย่กว่า) |

- LLM rerank คือ "call เต็ม 1 ครั้ง" ต่อทุกคำถาม -- เป็นตัวการหลักของ latency ที่ผู้ใช้บ่น
- vector rerank เลือก **chunk ชุดเดียวกัน** เพราะ document-expansion (ดึงตารางทั้งตาราง) + dose-table-aware
  injection (รอบ 6) การันตีว่าตารางยา/ทางเลือกแพ้ยาเข้า context อยู่แล้ว **โดยไม่ต้องพึ่ง LLM rerank**
- สลับ default `RERANK_MODE=vector` -> เร็วขึ้น 8-17s/คำถาม โดยความถูกต้องไม่ลด (validate 21/21)
  (ยังคง `RERANK_MODE=llm` ไว้เป็น option ถ้าต้องการ)

### 1.2 Conversation State / Intent (แก้เคสแต่งเรื่องเอง)

**ปัญหา:** "เดี๋ยวมีอีกเคสแป๊ป" = ข้อความคั่นเวลา ไม่มีข้อมูลผู้ป่วย แต่บอทแต่งเคส "ฝีหลังคอหอย" ขึ้นมาเอง

**แก้ (2 ชั้น -- ทำหน้าที่เป็น state machine เบาๆ):**
- **ชั้น gate (`classify_message_intent` + `_FILLER_RE`):** ข้อความสั้นที่เป็นการเกริ่น/คั่นเวลา
  ("เดี๋ยว/แป๊ป/รอ/มีอีกเคส/ขอถามอีก") และ **ไม่มีสัญญาณคลินิก** -> จัดเป็น smalltalk -> ข้าม retrieval
  ตอบ "พร้อมรับเคส เชิญเลย" (ทดสอบ 12/12 แยกถูก รวมเคสที่มีข้อมูลจริงยังเป็น clinical)
- **ชั้น prompt (safety rule 6):** ถ้าข้อความล่าสุดไม่มีข้อมูลผู้ป่วยเลย **ห้ามแต่งอาการ/สร้างเคสสมมติ**
- **เหตุผลที่ไม่ทำ FSM เต็ม:** state machine แบบเต็มเพิ่มความซับซ้อน/latency; วิธี rule-based 2 ชั้นนี้
  ให้ผลดีกว่า (0ms, ไม่พลาดเคสจริง) จึงเลือกอันนี้ (ถ้าทำแล้วแย่ลงจะไม่ใช้ ตามที่โจทย์ระบุ)

### 1.3 Modified Centor -- โชว์เสมอ + ขับการซักประวัติ (multi-type)

- เพิ่มบล็อก "การประเมิน Modified Centor / McIsaac" (เกณฑ์ + คะแนน + การแปลผล) และบังคับ:
  **ทุกเคสที่มีอาการทางคอ/หวัด/น้ำมูก/ไอ/คัดจมูก ต้องแสดงคะแนน Centor เสมอ** (ยกเว้น AOM/sinusitis ล้วน)
  -- แม้อาการเข้าทางไวรัสชัด ก็โชว์คะแนนต่ำ (<2) เพื่อยืนยันเหตุผลว่าไม่ต้องใช้ ATB
- **Multi-type / history-first:** เคสที่ "ดูข้อมูลพอ" แต่ยัง **ขาด input ที่ต้องใช้คำนวณ Centor
  (โดยเฉพาะอายุ + มีไอหรือไม่ + ต่อมน้ำเหลือง)** ให้จัดเป็นประเภท 4 (นำด้วยการซักประวัติ) ห้ามด่วนจ่าย ATB
  แก้เคส "เจ็บคอมาก ไข้ 38.8 ทอนซิลมีจุดขาว ขอ ATB (ไม่บอกอายุ/ไอ)" ที่เดิมจ่ายเลยไม่ยอมถาม

### 1.4 Format / Tone

- **การวินิจฉัย:** จัดใหม่ให้เรียบร้อย (เลิกใช้ "--" กลางประโยค) -> `1. **ชื่อโรค** (โอกาส: **สูง**)` แล้ว
  เหตุผลขึ้นบรรทัดใหม่ + เพิ่ม **bullet สรุปการประเมิน** ต่อท้าย (โดยรวมเข้าได้กับอะไร + แผนเฝ้าระวัง เช่น
  "หากไม่ดีขึ้นภายใน 7-10 วัน เฝ้าระวังไซนัส")
- **Dose:** เลิกคำว่า "ขอบบน/ขอบล่าง" -> แสดงเป็นช่วงโดยตรง "1,200-1,350 mg/day -> ครั้งละ 600-675 mg"
- โทน: ย้ำเภสัชกรรุ่นพี่ที่เป็นธรรมชาติ

### 1.5 ความครบถ้วน (แพ้ยา + เด็ก + detail)

- **แพ้ยา:** เมื่อบอกให้เลี่ยงกลุ่มยา ต้องยกตัวอย่างยา 2-3 ตัว (เช่น beta-lactam: amoxicillin, penicillin V,
  cephalexin, cefdinir, cefpodoxime) -- ให้เภสัชกรเห็นภาพว่าห้ามตัวไหน
- **เด็ก conflict:** เน้นย้ำถ้า URI เด็ก 2562 กับ AAFP (ส่วนเด็ก) ทับกันแต่ต่างกัน -> โชว์ทั้ง 2 + คำนวณเทียบ + สรุป
- **detail วงเล็บ:** ย้ายกฎ "คงค่าในวงเล็บ (110 mcg)" ไปไว้ที่หัวข้อยาตามอาการ (3b) ให้ใกล้จุด generate

### 1.6 External Ref (markdown normalize + content-match)

- **`_MD_LINK_RE`:** normalize markdown link `[text](url)` ที่โมเดลเขียนแทน [Ref] -> เข้าแผงอ้างอิงเสมอ
- **`verify_url_content_match`:** fetch หน้า HTML (best-effort, stdlib) แล้วเทียบ keyword จำเพาะ (ชื่อยา/
  เอกสาร) -- ถ้าเปิดได้แต่ไม่พบ keyword เลย = "content_mismatch" -> **ถอด URL ออก** (คงชื่อแหล่งไว้)
  ทำงานเฉพาะเคส external (นอกขอบเขต ซึ่งพบไม่บ่อย) + เป็น HTML (PDF/parse ไม่ได้ = เชื่อ reachability)
  knob: `VERIFY_EXTERNAL_CONTENT`

### 1.7 Out-of-Scope

- เพิ่มตัวอย่าง "ปวดหลังส่วนล่าง/ปวดกล้ามเนื้อ/ปวดท้อง" ในประเภท 6 -> จัดเป็นนอกขอบเขตชัดเจน
  (อาจซักประวัติช่วยเบื้องต้นได้ แต่ยังกำกับว่านอกขอบเขต ไม่หยิบ URI Guideline มาตอบ)

---

## 2. ผลทดสอบ -- Regression Suite (`Jack_prompt/eval/regression_suite.py`)

รันอัตโนมัติทุกครั้งที่แก้ prompt (assert keyword: ชื่อยา/ขนาด/ประเภท/ไม่มี bleeding + วัด accuracy/latency)

| # | เคส | สิ่งที่ตรวจ | ผล |
|---|---|---|---|
| C1 | "ดีมากตอบได้ดี" หลังตอบเคส | intent=smalltalk, ไม่ตอบเคสเดิม | PASS |
| C2 | pharyngitis 20 ปี | PenV 250 + Amox 1,000 + 10 วัน + Centor | PASS |
| C3 | เด็ก 4 ขวบ ไอ เจ็บคอ | ประเภท 4, ไม่จ่ายยามั่ว | PASS |
| C4 | "อีกเคส..." แพ้ penicillin | ไม่หลอน "อายุ 20" | PASS |
| **C4b** | **"เดี๋ยวมีอีกเคสแป๊ป"** | smalltalk, **ไม่แต่งเคสฝีหลังคอหอย** | PASS |
| C6 | AOM 15 kg | min-max 1,200-1,350, ไม่มี "ขอบบน/ขอบล่าง" | PASS |
| C7 | เด็ก 3 ขวบ ขอยาแก้อักเสบ | ปฏิเสธ AB + ไวรัส + ยาแก้อักเสบ != ปฏิชีวนะ | PASS |
| C8 | ไซนัส 50 ปี + steroid spray | Augmentin + (110 mcg) | PASS |
| C9 | ขอ ciprofloxacin | ประเภท 4 ซักประวัติ | PASS |
| F1 | follow-up ATB timing | ตอบเฉพาะโรคที่ถาม | PASS |
| N2 | เด็ก 8 ขวบ 25 kg pharyngitis | Amox 50 mg/kg + Centor | PASS |
| N4 | ผู้ใหญ่แพ้ penicillin anaphylaxis | Clindamycin 300 + Azithromycin 500 + ยกตัวอย่าง beta-lactam | PASS |
| N5 | epiglottitis เด็ก | ส่ง ER/ฉุกเฉิน | PASS |
| N7 | เบาหวาน (นอกขอบเขต) | ประเภท 6 | PASS |
| N8 | เด็ก 2 ขวบ ขอยาแก้ไอ | ปฏิเสธ <4 ปี | PASS |
| N11 | เด็กแพ้ amox non-type1 | Cephalexin | PASS |
| N12 | ABRS แพ้ penicillin ผื่นลมพิษ | Doxycycline/Cefixime | PASS |
| **O1** | **เจ็บคอ+ขอ ATB (ไม่บอกอายุ/ไอ)** | **Centor + ซักถามอายุ/ไอ/ต่อมน้ำเหลือง** | PASS |
| **O2** | **หวัด+ขอ ATB ผู้ใหญ่** | **Centor (<2) + ปฏิเสธ AB + antihistamine** | PASS |
| **O3** | **ปวดหลัง (นอกขอบเขต)** | **ประเภท 6** | PASS |
| **O4** | **เด็ก 8 ปี หวัด 4 วัน** | **Centor + เฝ้าระวังไซนัส** | PASS |

**ACCURACY: 21/21 (100%)** | LATENCY: avg ~10-16s (แปรตามโหลด API), retrieval overhead ~0.2s

Unit: intent classifier **12/12** (รวม filler + เคสจริงยังเป็น clinical), retry helper **3/3**

---

## 3. ไฟล์ที่แก้ + knobs

- `backend/config.py`
  - `RERANK_MODE` default `llm` -> **`vector`** (ตัด latency)
  - `PER_SOURCE_TOP_K` = 12 (คงจากรอบ 6)
  - ใหม่: `VERIFY_EXTERNAL_CONTENT` (content-match on/off)
- `backend/rag_engine.py`
  - Intent: `_FILLER_RE` + `classify_message_intent` (เพิ่ม filler), smalltalk instruction รองรับคั่นเวลา
  - External: `_MD_LINK_RE`, `_label_keywords`, `_fetch_page_text`, `verify_url_content_match`,
    `_resolve_external`; `_append_external_refs` เขียนใหม่ (จับทั้ง [Ref:] + markdown link)
  - Prompt: บล็อก Modified Centor (ใหม่), safety rule 6 (anti-fabrication), rule 4 (ช่วง dose ไม่ใช้ขอบบน/ล่าง),
    step 2 (differential format ใหม่ + สรุป bullet + Centor), 3a (multi-type routing), 3b (detail วงเล็บ),
    type 3/5 (ยกตัวอย่างยาเมื่อเลี่ยงกลุ่ม), type 6 (back pain), Ref rule 6 (เน้นเด็ก dual-guideline)
- `Jack_prompt/eval/regression_suite.py` (ใหม่) -- regression suite อัตโนมัติ 21 เคส

**ไม่แตะ:** `rag/qdrant_db/`, `rag/pipeline.py`, chunk/embedding, `frontend/`

**ตรวจ Error:** `py_compile` ผ่านทุกไฟล์, import ทั้งแอปผ่าน, รัน 21 เคสผ่านหมด ไม่พบ NameError/UnboundLocalError

---

## 4. จุดที่ยังปรับต่อได้ (ความโปร่งใส)

- **Latency ของ Gemini generation** (~10-16s) เป็นตัวแปรที่เหลือ ขึ้นกับความยาว output + โหลด API ของ Gemini
  เอง (ควบคุมจากฝั่งเราไม่ได้) -- ที่ทำได้แล้วคือตัด overhead ฝั่ง retrieval (rerank) ออกจนเกือบ 0
- **content-match ของ external URL** เป็น best-effort: หน้าที่เป็น PDF หรือ JS-rendered จะตรวจเนื้อหาไม่ได้
  (fallback เชื่อ reachability) -- ครอบคลุมหน้า HTML ทั่วไปได้ดี
- **SYSTEM_PROMPT ~34k chars (~9.6k tokens):** ยังไม่ถึงขั้น truncate บน flash-lite (context window ใหญ่)
  แต่รอบหน้าถ้าจะเพิ่มกฎอีก ควร consolidate ให้สั้นลงเพื่อ adherence ที่คมขึ้น

---

## 5. Next Steps

1. **[Eval]** ต่อยอด regression suite: เพิ่ม assertion ระดับ "เลขหน้า Ref อยู่ในช่วงจริง" + latency budget alert
2. **[Prompt]** consolidate SYSTEM_PROMPT ให้กระชับ (รวมกฎที่ทับซ้อน) เพื่อลด token + เพิ่ม adherence
3. **[Data-layer]** re-chunk ตาราง AAFP ให้เลขหน้า dose เป๊ะ 100% (นอกขอบเขต query-time)
4. **[Infra]** ย้าย `google.generativeai` (deprecated) -> `google.genai`

---

## 6.1 แก้เพิ่ม (รอบ follow-up ตาม feedback)

- **สรุปอาการ = ความเรียง (ไม่ใช่ bullet):** หัวข้อ "1. สรุปอาการ" ให้เขียนเป็นประโยค/ย่อหน้าธรรมชาติ อ่านลื่นกว่า
- **Modified Centor -- เลิก hard-code ในprompt, ดึงจากเอกสารจริง:** เดิมเขียนเกณฑ์+คะแนนไว้ใน prompt (เสี่ยง
  มั่ว) -> เปลี่ยนเป็นสั่งให้ **อ่านค่าคะแนนจากตาราง "Modified Centor Criteria" (AAFP หน้า 4, TABLE 2) ใน Context
  โดยตรง** (ยืนยันแล้วว่าตารางนี้ retrieve เข้ามาใน context จริง -- chunk `AAFP_0013` type=table_html)
  วัดจริง: เคส pharyngitis ผู้ใหญ่ 25 ปี -> Centor = 4 (ไม่ไอ+1, อายุ15-45=0, ไข้+1, ต่อมน้ำเหลือง+1, ทอนซิลหนอง+1)
  ตรงตามตารางเป๊ะ + อ้าง [Ref: AAFP, หน้า 4]
- **Centor แสดงเฉพาะเคสที่เกี่ยวข้องจริง (ไม่ใช่ทุกเคส):** เฉพาะเคสอาการทางคอ/pharyngitis -- เคสหวัด/น้ำมูกล้วน
  (แม้ขอ ATB) ไม่ต้องแสดง Centor ใช้เหตุผล "เป็นไวรัส" อธิบายแทน (AOM/sinusitis ใช้เกณฑ์เฉพาะโรค)
- **ให้คะแนน Centor ตามอาการจริง (ไม่ 0 หมด):** อ่านอาการที่ผู้ใช้ระบุแล้ว map เข้าตาราง ห้ามใส่ 0 ให้ข้อที่ผู้ใช้
  บอกข้อมูลแล้ว; ข้อที่ยังไม่รู้ = ทำเครื่องหมาย "ยังไม่ทราบ" (ไม่ใช่ 0) แล้วซักก่อนสรุปคะแนน
- **บอกชื่อยาจริงเสมอ ไม่ใช่แค่กลุ่มยา (ทุกเคส):** เมื่อกล่าวถึงกลุ่มยาต้องตามด้วยชื่อยาจริง 1-2 ตัวจาก Context
  วัดจริง: เคสหวัดขอ ATB -> "Analgesic: **Paracetamol** 500 mg; Antihistamine/Decongestant:
  **Brompheniramine + Phenylephrine** [Ref: Dose, หน้า 27]" (เดิมบอกแค่ชื่อกลุ่ม)

## 6. สรุป (bullet)
- **Latency = พระเอกรอบนี้:** ตัด LLM rerank (8-18s) -> vector (0.2s) โดยความถูกต้องไม่ลด
- **Anti-fabrication:** filler gate + prompt -> ไม่แต่งเคสเมื่อไม่มีข้อมูล
- **Modified Centor:** โชว์ทุกเคส URI + ขับการซักประวัติเมื่อ input ไม่ครบ (multi-type)
- **Format/Tone:** differential เรียบร้อยขึ้น + สรุป bullet + เลิก "ขอบบน/ขอบล่าง"
- **ครบถ้วน:** ยกตัวอย่างยาเมื่อเลี่ยงกลุ่ม, เด็ก dual-guideline โชว์ 2 เล่ม, คง detail (110 mcg)
- **External ref:** normalize markdown link + content-match (ถอดลิงก์ที่เนื้อหาไม่ตรง)
- **Eval:** regression suite อัตโนมัติ 21/21 (รันทุกครั้งที่แก้ prompt)
- **ขอบเขต:** prompt/RAG/query-time เท่านั้น -- ไม่แตะ vector DB/chunk/embedding/frontend
