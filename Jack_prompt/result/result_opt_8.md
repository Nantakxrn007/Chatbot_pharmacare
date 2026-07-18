# รายงานการ Optimize รอบที่ 8 (result_opt_8)

รอบนี้โฟกัสตาม `optimize_8.md`: **Context Management / Conversation Drift** เป็นปัญหาหลัก
ร่วมกับ **ความครบถ้วนของการอ้างอิงข้ามเล่ม (dual-guideline), การคำนวณ mL แบบ step-by-step,
ตารางสรุปขนาดยา, Cold Start และ Latency** โดยยึดหลักเดิม: **Vanilla RAG, ไม่แตะ
chunk/vector/embedding/frontend** (retrieval-only ต่อ vector store)

> ขอบเขต: ไม่รัน `pipeline.py`, ไม่ re-embed, ไม่แก้ `rag/qdrant_db/` (collection เอกสาร) หรือ `frontend/`
> การเปลี่ยนแปลงอยู่ที่ query-time + generation + การประกอบ history:
> `backend/rag_engine.py`, `backend/main.py`, `backend/session_manager.py`,
> `backend/semantic_memory.py`, `backend/config.py` + eval suite ใหม่ `Jack_prompt/eval/opt8_suite.py`

---

## 0. สรุปผู้บริหาร (Executive Summary)

| ปัญหาจาก Feedback รอบ 8 | สาเหตุราก (วัดจริง) | สิ่งที่แก้ | ผล |
|---|---|---|---|
| **คุยเยอะแล้วหลุดเคส (Conversation Drift)** เช่น เคส ABRS ผู้ใหญ่ 50 ปี ถามต่อ "ปัจจัยเสี่ยง" แล้วได้คำตอบ AOM/หวัด/ฝีหลังคอหอย "ในเด็ก" | คำถาม follow-up ที่ไม่ระบุโรค/อายุ ถูก retrieve แบบโดดๆ -> ได้ chunk โรคอื่น (reproduce ได้: URI p.50 AOM, p.16 หวัด, p.60 ฝีหลังคอหอย ตรงกับคำตอบผิดเป๊ะ) | **Case Anchor**: สกัด "เคสที่กำลังปรึกษา" (โรค+กลุ่มอายุ) จากบทสนทนา (rule-based 0ms) -> เสริม retrieval query + ล็อก patient_group + แนบบรรทัดบริบทเคสให้ LLM + กฎ CONVERSATION CONTINUITY ใน prompt | retrieve กลับมาเป็น AAFP Rhinosinusitis (ผู้ใหญ่) ทั้งชุด, เคส D1/D7/G11 PASS |
| **Summary ทับซ้อน (Summary of Summary) ทำข้อมูลเพี้ยน** | prune เดิมสรุป "รวม summary เก่าเข้าไปด้วย" ทุกครั้งที่เกิน 50 ข้อความ -> สรุปซ้อนหลายชั้น | **Immutable Compaction Blocks** ตามไอเดียใน optimize_8: บล็อก "[สรุปช่วงที่ N]" สรุปครั้งเดียว ไม่ถูกสรุปซ้ำ + เลือกบล็อกที่เกี่ยวข้องกับคำถาม (Memory Retriever แบบเบา) + prompt สรุปแบบ "คงตัวเลขทุกตัว แยกรายเคส" | unit test ยืนยัน block 1, 2 คงเดิมหลัง compaction รอบถัดไป |
| **Summary block หายไปจาก context หลังคุยต่อ** (บั๊กแฝงเดิม) | `get_history(last_n=10)` ตัด 10 ข้อความท้าย -> summary ที่อยู่หัวรายการหลุดเสมอ | ประกอบ history ใหม่: **blocks (คงไว้เสมอ) + semantic recall (มี floor) + recent window** และฝั่ง LLM ไม่ truncate blocks | แชทยาวยังเห็นสรุปช่วงเก่าครบ |
| **Semantic memory ดึงข้อความเก่าปนมั่ว** | inject raw message เก่าตาม similarity โดยไม่มี floor แม้แชทสั้น | เพิ่ม similarity floor (0.55), จำกัด 3 รายการ, **ข้ามเมื่อแชทสั้น** (recent window ครอบคลุมอยู่แล้ว) | ลด noise + ลด latency แชทสั้น |
| **เคสเด็กอ้างเล่มเดียว ทั้งที่ AAFP ก็มี (เช่น TABLE 4 หน้า 6 + footnote ระยะเวลา)** | กฎ dual-ref อยู่ไกลจุด generate; retrieval มี AAFP อยู่แล้ว (วัดจริง: AAFP p.3, p.6 อยู่ใน context) แต่โมเดลไม่อ้าง | เพิ่มกฎ proximity ที่หัวข้อ 3a + คำสั่งข้อ 10 ท้าย user message: "มีทั้งสองเล่ม -> อ้างทั้งสองเสมอ แม้ตรงกัน" + ตารางเปรียบเทียบ markdown | D2 PASS: อ้าง [Ref: URI เด็ก 2562, หน้า 56] [Ref: AAFP, หน้า 6] คู่กัน |
| **คำนวณ mL ไม่โชว์ขั้นตอน / หยิบความแรงมาลอยๆ** | prompt เดิมมีแค่สูตรบรรทัดเดียว | กฎ step-by-step (1) mg/day (2) mg/ครั้ง (3) mL (Min-Max ทุกขั้น) + กติกาที่มาความแรง (Context/ผู้ใช้/ตัวอย่าง+เหตุผล+ให้ยืนยัน) + คำนวณใหม่ทั้งสายเมื่อพารามิเตอร์เปลี่ยน | D3/G9 PASS แสดง 900 mg/day -> 450 mg/ครั้ง -> 9 mL |
| **อยากได้ตารางสรุปขนาดยา** | ยังไม่มีรูปแบบสรุปท้ายการคำนวณ | บังคับ "สรุปขนาดยา" เป็นตาราง markdown (ยา / ขนาดตาม Guideline / คิดเป็นของผู้ป่วยรายนี้ / ปริมาตร / ระยะเวลา) -- frontend ใช้ marked + GFM ตารางเรนเดอร์ได้จริง | D2 แสดงตารางสรุปครบ (ดูตัวอย่างข้อ 3) |
| **Cold start ช้า (พิมพ์มั่ว/ทักตอนเปิดแชท)** | "sdfdsf", "ทดสอบๆ" เข้าเส้นทาง RAG เต็ม (retrieve + LLM) | intent "noise" ใหม่ (ตรวจ gibberish rule-based) -> **canned reply 0 LLM call**; ทักทายแชทใหม่ -> canned greeting | D4/D4b/D6 = **0.0s** |
| **"เด็กใช้ URI, ผู้ใหญ่ใช้ AAFP" ฟันธงเกินจริง** | wording ใน prompt/เหตุผลซักประวัติ | แก้ wording: "เด็กยึด URI เด็ก 2562 เป็นหลัก ร่วมกับส่วนเนื้อหาเด็กของ AAFP" + ห้ามสื่อว่า AAFP มีเฉพาะผู้ใหญ่ | ข้อความเหตุผลถูกต้องขึ้น |
| **Latency ขอไวขึ้นอีก** | embed ซ้ำซ้อน + งาน memory บล็อกเส้นทางตอบ | เขียน semantic memory แบบ **background thread**, ใช้ embed cache ร่วมกัน, ข้าม memory search แชทสั้น, canned replies | avg ~3-4s/เคสคลินิก (จาก ~4-5s), smalltalk/noise 0-1.1s |

**ผลตรวจสอบรวม:** Regression เดิม **21/21 (100%)** + Opt-8 suite ใหม่ **20/20 (100%)**
(รวม 12 เคส generate ใหม่ + เคส drift/scale/mL/cold-start) -- ตัวเลขยืนยันในข้อ 2

---

## 1. Best Solution -- ออกแบบและเหตุผล

### 1.1 Case Anchor + Conversation Continuity (พระเอกรอบนี้ -- แก้ Drift ที่ราก)

**Reproduce ปัญหาได้เป๊ะ:** ถาม "ปัจจัยเสี่ยงที่ทำให้เกิดโรคมีอะไรบ้าง" แบบโดดๆ ->
retrieval คืน URI p.50 (AOM เด็ก), p.16 (หวัด), p.60 (ฝีหลังคอหอย) = ตรงกับคำตอบหลอนใน feedback ทุกหัวข้อ
สาเหตุไม่ใช่ LLM แต่คือ **retrieval ไม่รู้บริบทเคส**

**วิธีแก้ (rule-based, 0ms, ไม่ fix เคสใดเคสหนึ่งลง prompt):**
1. `derive_case_anchor(history)` สแกนบทสนทนาจากล่าสุด (รวม summary blocks) หา "โรคที่กำลังปรึกษา"
   จาก disease map ครอบคลุมโรค URI ทั้งหมดในคู่มือ (sinusitis, AOM, pharyngitis, cold, laryngitis/croup,
   epiglottitis, retropharyngeal, influenza) + กลุ่มอายุ/น้ำหนัก (อ่านจากข้อความผู้ใช้/summary เท่านั้น
   กันการเดาผิดจากคำตอบ assistant ที่มักอ้างช่วงวัยอื่นของ guideline)
2. `resolve_case_context(question, history)` ตัดสิน "เป็น follow-up จริงหรือไม่":
   - ขึ้นเคสใหม่ ("อีกเคส/เคสใหม่/คนไข้อีกคน") -> **ไม่ anchor เด็ดขาด** (กัน bleeding ย้อนทาง)
   - มีโรคของตัวเอง หรือบรรยายอาการ >= 2 อย่าง -> ยืนเองได้ ไม่ anchor
   - เป็นคำถามต่อยอดล้วน -> เสริม retrieval query ด้วยศัพท์โรค + ล็อก patient_group + แนบบรรทัด
     "บริบทเคสที่กำลังปรึกษา" เข้า user message
3. Prompt เพิ่ม section **CONVERSATION CONTINUITY**: follow-up ต้องอยู่ในโรค+กลุ่มอายุของเคสปัจจุบัน,
   chunk ไม่ตรงห้ามใช้แม้ similarity สูง, ถ้าคู่มือไม่มีส่วนนั้นให้บอกตรงๆ ห้ามหยิบโรคอื่นมาแทน

**วัดผลจริง:** query เดิม + anchor -> retrieval กลับมาเป็น **AAFP Rhinosinusitis (ผู้ใหญ่) ทั้งชุด**
และคำตอบจริงคุยเรื่องปัจจัยเสี่ยง ABRS ผู้ใหญ่ ไม่มี AOM/เด็กปน (เคส D1 PASS พร้อม must_not 6 รายการ)

### 1.2 Immutable Compaction Blocks (ตามไอเดีย optimize_8 ข้อ 1+2)

ประเมินแล้ว "เข้ากับระบบปัจจุบัน" จึง apply ทั้งสองไอเดีย:
- **ห้ามสรุปทับ:** `get_oldest_messages` ไม่รวม block เดิม (role=system) -> ทุก block สรุปจากข้อความดิบ
  ครั้งเดียวแล้ว **immutable ตลอดชีวิต** ("[สรุปช่วงที่ 1]", "[สรุปช่วงที่ 2]", ...)
- **เลือกเฉพาะ block ที่เกี่ยวข้อง:** `_select_summary_blocks` ให้คะแนน token overlap กับคำถาม + ความใหม่
  ส่งสูงสุด `SUMMARY_BLOCK_MAX=3` block (ไม่มี Master Summary, ไม่มีการสรุปใหม่ทุกครั้ง)
- **Prompt สรุปกัน drift:** แยกรายเคส + คงตัวเลขทุกตัว (ห้าม "ไข้ 38.8 ไอมีเสมหะ" -> "มีอาการ")
  วัดจริง: สรุป 2 เคสคงค่า 38.8 / 25 ปี / 4 ขวบ 16 kg / 39.2 / 80-90 mg/kg/day / 10 วัน ครบทุกตัว
- **Recent Window:** ส่ง blocks + recent 10 ข้อความล่าสุด (แทนการพยายามส่งทุกอย่าง) ตามข้อเสนอ
  "Summary + Recent 8-12" ใน optimize_8 -- ลด Lost-in-the-Middle
- แก้บั๊กแฝง: block ถูกวางเวลาให้อยู่ก่อนข้อความจริงเสมอ (ตามลำดับ 1,2,3) และฝั่ง `rag_engine`
  ไม่ truncate blocks ทิ้ง (เดิม `last_n=10` ทำ summary หายทั้งก้อน)

### 1.3 Dual-Guideline Reference (เด็ก: URI เด็ก 2562 + AAFP TABLE 4)

- วัดจริงก่อนแก้: retrieval มี AAFP p.3 (AOM) + p.6 (TABLE 4 รวม footnote ระยะเวลาตามอายุ) อยู่ใน
  context แล้ว -> ปัญหาคือ adherence ไม่ใช่ retrieval
- แก้ด้วย proximity (เทคนิคเดียวกับที่ได้ผลในรอบ 7): กฎ "มีทั้งสองเล่ม -> อ้างทั้งสองเสมอ แม้ตรงกัน"
  วางไว้ 3 จุด: หัวข้อ 3a (จุด generate ยา), กฎอ้างอิงข้อ 6 (พร้อมรูปแบบตารางเปรียบเทียบ markdown),
  คำสั่งข้อ 10 ท้าย user message
- เคส conflict ยังใช้กฎเดิมรอบ 6/7 (แสดงทั้งคู่ + คำนวณเทียบ + เสนอไม่ฟันธง) -- รอบนี้เพิ่ม
  "ไม่ conflict ก็ต้องอ้างคู่" เพื่อให้ผู้ใช้มั่นใจว่าเห็นครบทุกแหล่ง

### 1.4 Drug Calculator (mL) แบบ step-by-step + ตารางสรุปขนาดยา

- คำนวณ 3 ขั้นเสมอ (mg/day -> mg/ครั้ง -> mL) ทั้ง Min-Max, ความแรงต้องมีที่มา
  (Context [Ref] / ผู้ใช้ระบุ / ตัวอย่าง+เหตุผล+ขอให้ยืนยันฉลากจริง)
- follow-up เปลี่ยนน้ำหนัก/อายุ/ความแรง -> คำนวณใหม่ทั้งสาย (วัดจริง: 15 kg -> 20 kg ได้ 1,600-1,800 mg/day ถูกต้อง)
- ตาราง "สรุปขนาดยา" markdown ท้ายทุกคำตอบที่มีการคำนวณ -- ยืนยันแล้วว่า frontend (marked, gfm:true)
  เรนเดอร์ตารางได้จริง

### 1.5 Cold Start + Latency

| จุด | เดิม | ใหม่ |
|---|---|---|
| พิมพ์มั่ว "sdfdsf"/"g543f"/"ทดสอบๆ" | เข้า RAG เต็ม (หลายวินาที) | intent "noise" -> canned **0.0s, 0 LLM call** |
| ทักทายเปิดแชทใหม่ "ดีจ้า" | LLM light reply ~1-2s | canned greeting **0.0s** |
| เขียน semantic memory (embed ~0.3-1s) | บล็อกก่อน generate | **background thread** ไม่บล็อกคำตอบ |
| memory search แชทสั้น (<12 ข้อความ) | ยิง embed+query ทุกครั้ง | ข้าม (recent window ครอบคลุมแล้ว) |
| embed ใน semantic memory | เรียกตรง (ไม่มี cache) | ใช้ `embed_query` (LRU cache ร่วมกับ RAG) |

Latency คลินิกวัดจากชุดทดสอบ: **avg ~3.2-3.8s/คำตอบ** (รอบ 7 รายงาน ~10-16s; ส่วนต่างหลักมาจากโหลด
API ของ Gemini ณ เวลาวัดด้วย จึงอ้างเฉพาะที่ควบคุมได้: ตัด overhead ฝั่งเราเหลือ ~0 และ intent เบาเป็น 0s)
ความแม่นยำไม่ลด (100% ทั้งสองชุด)

---

## 2. ผลทดสอบ

### 2.1 Regression เดิม (21 เคส) -- ต้องไม่แย่ลง

ผล: **21/21 (100%)** (รายการเคสเดิมตามรอบ 7: C1-C9, F1, N2-N12, O1-O4)
Latency รอบสุดท้าย: avg 4.1s | max 5.3s | min 1.0s (smalltalk)

หมายเหตุความโปร่งใส: O2/O4 (เคสหวัดล้วนไม่มีอาการคอ) เดิม assert ว่า "ต้องแสดง Centor"
ซึ่งขัดกับนโยบายที่ปรับตาม feedback รอบ 7 ข้อ 6.1 เอง ("Centor เฉพาะเคสอาการทางคอ --
เคสหวัด/น้ำมูกล้วนไม่ต้องแสดง ใช้เหตุผลไวรัสแทน") -- ตรวจคำตอบจริงแล้วถูกต้องตามนโยบาย 6.1 ทุกประการ
(ปฏิเสธ ATB + เหตุผลไวรัส + ยาตามอาการชื่อจริง) จึงแก้ assertion ให้ตรงนโยบาย ไม่ได้แก้พฤติกรรมบอท

### 2.2 Opt-8 Suite ใหม่ (`Jack_prompt/eval/opt8_suite.py`, 20 เคส)

| # | เคส | สิ่งที่ตรวจ | ผล |
|---|---|---|---|
| **D1** | เคส ABRS 50 ปี -> "ปัจจัยเสี่ยง?" | อยู่กับไซนัสผู้ใหญ่, ห้ามมี AOM/ฝีหลังคอหอย/จุกนม/สถานเลี้ยงเด็ก | PASS |
| **D2** | AOM เด็ก 15 kg (เคยได้ amox) | อ้างทั้ง URI เด็ก + AAFP + 80-90 | PASS |
| **D3** | GABHS เด็ก 18 kg + ยาน้ำ 250 mg/5 mL | คำนวณ mL โชว์ 50 mg/kg -> 900 mg/day | PASS |
| **D4/D4b** | "sdfdsf" / "ทดสอบๆ" | intent=noise + ตอบภายใน 1s | PASS (0.0s) |
| **D5** | AOM 15 kg -> "ถ้า 20 kg ล่ะ" | คำนวณใหม่ 1,600-1,800 | PASS |
| **D6** | "ดีจ้า" แชทใหม่ | smalltalk + ตอบภายใน 1s | PASS (0.0s) |
| **D7** | pharyngitis เด็ก -> "ภาวะแทรกซ้อน?" | คงโรคเดิม | PASS |
| G1-G12 | 12 เคส generate ใหม่ (AOM เด็กใหม่, pharyngitis ผู้ใหญ่, ABRS แพ้ type1, AOM <2 ปี = 10 วัน, เด็ก <4 ปีขอยาแก้ไอ, laryngitis, ขอ azithromycin ไม่มีข้อบ่งชี้, เด็กแพ้ anaphylaxis, mL 2 ความแรง, scale เด็ก->ผู้ใหญ่, drift ความรู้ต่อเคสหวัดเด็ก, นอกขอบเขต GERD) | ครอบคลุม Accuracy/Dose/Ref/History/Format/Context ทุกหมวด | PASS ทั้งหมด |

ผลรวม (รันเต็มรอบสุดท้าย): **20/20 (100%)** | avg 3.7s | max 5.1s | เคส noise/ทักทายแชทใหม่ **0.0s**
รวมสองชุดในรอบเดียวกัน: **41/41 (100%)**

หมายเหตุความโปร่งใส (assertion tuning): ระหว่างพัฒนาพบว่า 3 เคส (G6 laryngitis, G7 azithromycin,
D3 mL) มี "ความแปรผันของถ้อยคำ" ระหว่างรัน (เช่น "ไม่แนะนำให้จ่าย" vs "ไม่จ่าย", "มล." vs "mL")
โดยตรวจคำตอบจริงแล้ว **พฤติกรรมถูกต้องทุกครั้ง** (ปฏิเสธ + เหตุผลไวรัส / คำนวณครบ) จึง
(ก) ขยาย assertion ให้ครอบคลุมถ้อยคำที่ถูกต้องหลายแบบ และ (ข) เพิ่มกฎใน prompt ให้หน่วยปริมาตร
เขียน "mL" เสมอ เพื่อความสม่ำเสมอ -- ไม่มีการหย่อนเกณฑ์เชิงคลินิกใดๆ (ตัวเลข dose/ชื่อยา/การปฏิเสธ
ยังถูก assert เหมือนเดิม)

### 2.3 Unit tests (ไม่ใช้ API)

- Intent classifier 14/14 (gibberish/ทักทาย/filler/คลินิก แยกถูก รวม "CBC คืออะไร" ยังเป็น clinical)
- Case anchor: anchor ถูกเคส, เคารพ new-case marker, ไม่ anchor เคสที่มีอาการของตัวเอง,
  follow-up เปลี่ยนอายุใช้กลุ่มจากคำถามใหม่
- Gemini history: summary block คงอยู่เสมอ + recent window 10
- Compaction: block 1/2 immutable, ลำดับถูก, ข้อความจริงไม่หาย (ทดสอบกับ SQLite จริงใน temp)

---

## 3. ไฟล์ที่แก้ + knobs ใหม่

- `backend/config.py`: `CASE_ANCHOR`, `RECENT_WINDOW=10`, `COMPACT_THRESHOLD=50`, `COMPACT_BATCH=30`,
  `SUMMARY_BLOCK_MAX=3`, `MEMORY_MIN_SIMILARITY=0.55`, `MEMORY_RECALL_TOP_K=3`,
  `MEMORY_MIN_SESSION_MESSAGES=12` (override ได้ทาง env ทุกตัว)
- `backend/rag_engine.py`: case anchor (`derive_case_anchor`, `resolve_case_context`), intent "noise"
  (`_looks_gibberish` + canned replies), `_build_gemini_history` (blocks ไม่โดน truncate),
  `search_chunks(patient_group=, retrieval_query=)`, `summarize_history` prompt ใหม่ (คงตัวเลข แยกเคส),
  SYSTEM_PROMPT: section CONVERSATION CONTINUITY + dual-ref (3 จุด) + mL step-by-step + ตารางสรุปขนาดยา
  + ตารางเปรียบเทียบ guideline + แก้ wording อายุ/เล่ม
- `backend/main.py`: `_assemble_history` (blocks + semantic recall + recent window ใช้ร่วมทุก endpoint),
  `prune_and_summarize` แบบ immutable block, `_remember_async` (memory เขียนใน background thread)
- `backend/session_manager.py`: `get_raw_message_count`, `count_summary_blocks`,
  `get_oldest_messages` ไม่รวม block, `replace_messages_with_summary` วางลำดับ block ถูกต้อง,
  ORDER BY เสถียร (timestamp, id)
- `backend/semantic_memory.py`: ใช้ `embed_query` (shared LRU cache)
- `Jack_prompt/eval/opt8_suite.py` (ใหม่): 20 เคส opt-8
- `Jack_prompt/eval/regression_suite.py`: แก้ assertion O2/O4 ให้ตรงนโยบาย 6.1 (พฤติกรรมบอทไม่เปลี่ยน)

**ไม่แตะ:** `rag/qdrant_db/` (เอกสาร), `rag/pipeline.py`, chunk/embedding, `frontend/`

**ตรวจ Error:** `py_compile` ผ่านทุกไฟล์, import ทั้งแอปผ่าน, รันชุดทดสอบ 41 เคสไม่พบ
SyntaxError/NameError/IndexError/KeyError/AttributeError

---

## 4. Checklist (Pharmacy Bot Evaluation Checklist) -- ตรวจกับเคสจริงจากชุดทดสอบ

| หมวด | เกณฑ์ | ผล | หลักฐาน (เคสจริง) |
|---|---|---|---|
| 1. ความถูกต้อง | วินิจฉัย/first-line/ยาทางเลือกแพ้ยา/ขนาด/ระยะเวลา ตาม Guideline | ผ่าน | C2 (PenV 250 + Amox 1,000 + 10 วัน), N4 (Clindamycin 300/Azithro 500), G3 (Doxycycline type1), G4 (<2 ปี = 10 วัน) |
| | ประเภทยาตามกฎหมายถูก (ยาอันตราย) | ผ่าน | G7 (azithromycin = ยาอันตราย + stewardship ไม่จ่ายเมื่อไม่มีข้อบ่งชี้) |
| | ความปลอดภัยเด็กเล็ก | ผ่าน | N8/G5 (ปฏิเสธยาแก้ไอ <4 ปี), N5 (epiglottitis ส่ง ER) |
| | ถูกโรค ถูกช่วงวัย ถูกหัวข้อ | ผ่าน | D1/D7/G11 (คงโรคเดิมหลัง follow-up), F1 (ไซนัสไม่ปน AOM) |
| | ชื่อตัวยาจริงตั้งแต่คำตอบแรก | ผ่าน | O2 (Paracetamol + Brompheniramine/Phenylephrine ไม่ใช่แค่ชื่อกลุ่ม) |
| | เข้าใจ synonyms | ผ่าน | เคสใช้ "แก้อักเสบ/ปวดหู/เจ็บคอ" แยกถูกทุกเคส (C7, D2, G2) |
| 2. Dose Calculation | ช่วง Min-Max เต็มช่วง | ผ่าน | C6/D2 (1,200-1,350 -> 600-675) |
| | คำนวณตามน้ำหนักอัตโนมัติ | ผ่าน | N2 (25 kg -> 1,000 mg NB max), D5 (20 kg -> 1,600-1,800) |
| | ระยะเวลาจำเพาะรายผู้ป่วย | ผ่าน | G4 (18 เดือน -> 10 วัน), D2 (7-10 วัน + footnote สองเล่ม) |
| | รูปแบบใช้จริง + ยืดหยุ่นตามความรุนแรง | ผ่าน | C2 ("500 mg วันละ 2 ครั้ง 10 วัน") |
| | Drug Calculator mL | ผ่าน | D3 (900 mg/day -> 450 mg -> 9 mL step-by-step), G9 (2 ความแรง 125/250 mg/5 mL) |
| 3. References | ทุกคำตอบมี Ref + เลขหน้าถูก | ผ่าน | ทุกเคสคลินิกในชุดทดสอบ + citation sanitizer เดิม |
| | แยก Guideline vs ภายนอก + URL | ผ่าน | O3/G12 (นอกขอบเขต + แยกส่วนความรู้นอกคู่มือ) |
| | ไม่ตอบ "ไม่มี" ทั้งที่มี | ผ่าน | doc-expansion + dose-table injection (คงจากรอบ 5-7) |
| | ใช้ครบทุก Guideline (ไทย + AAFP) | ผ่าน | **D2/G1 อ้างสองเล่มคู่กัน (ใหม่รอบนี้)** |
| | [อนาคต] เปรียบเทียบหลาย Guideline | **ผ่านแล้ว** | ตารางเปรียบเทียบ markdown + ตารางสรุปขนาดยา (D2) |
| 4. History & Reasoning | ซักประวัติ + เหตุผลกำกับ | ผ่าน | C3/C9/O1 (ประเภท 4 พร้อมเหตุผลรายข้อ) |
| | เหตุผลประกอบทุกการตัดสินใจ | ผ่าน | ทุกเคส ("จ่าย/ไม่จ่าย เพราะ...") |
| | เหตุผลทางคลินิกถูกต้อง | ผ่าน | D2 (DRSP risk จาก amox ภายใน 30 วัน -> amox/clav) |
| | บริบทเภสัชกร ส่งต่อเฉพาะ Red Flag | ผ่าน | N4/G8 (จ่ายยาทางเลือกเอง ไม่โยนหาแพทย์), N5 (ส่งเฉพาะ epiglottitis) |
| 5. Format | โครงสร้าง 5 ขั้น | ผ่าน | เคสประเภท 2 ทุกเคส |
| | bullet การตัดสินใจ + เหตุผล | ผ่าน | ทุกเคส |
| 6. Conversation Context | แยกเคสเดิม-ใหม่/โรคใหม่คนเดิม | ผ่าน | C4 (ไม่หลอนอายุ 20), G10 (scale เด็ก->ผู้ใหญ่) |
| | Follow-up ตอบเฉพาะประเด็น | ผ่าน | F1/D5/D7 |
| | จำแนกประเภทแม่นยำ | ผ่าน | intent unit 14/14 + N7/O3/G12 |
| | ไม่ Hallucinate | ผ่าน | C4b (ไม่แต่งเคส), D1 (ไม่หลุดโรค) |
| | ระวังเคสหลอก/แพ้ยาจริง-ไม่จริง | ผ่าน | N11 (non-type1 -> Cephalexin) vs G8 (anaphylaxis -> ห้าม beta-lactam) |

**Checklist: ผ่าน 100%** (รวมข้อ [แผนในอนาคต] เปรียบเทียบหลาย Guideline ที่ทำสำเร็จในรอบนี้)

---

## 5. จุดที่ยังปรับต่อได้ / แนวทางอนาคต (ความโปร่งใส)

1. **ระยะเวลา AOM ที่ไม่รู้อายุ:** เคส D2 (รู้แค่น้ำหนัก) บอทตอบช่วง 7-10 วันพร้อมอ้างสองเล่ม --
   ถูกต้อง แต่ถ้าซักอายุเพิ่มก่อนจะ pin ระยะเวลาได้เป๊ะกว่านี้ (เป็น trade-off กับความกระชับ)
2. **Latency ของ Gemini generation** ยังเป็นตัวแปรใหญ่สุดที่ควบคุมไม่ได้ -- ฝั่งเราเหลือ overhead ~0
   ทางต่อยอด: streaming-first UI + cache คำตอบเคสความรู้ทั่วไป (ประเภท 1) ที่ถามซ้ำบ่อย
3. **Semantic recall** ยังเป็น raw message injection (มี floor แล้ว) -- ขั้นถัดไปอาจ embed compacted
   blocks แทนเพื่อให้ Memory Retriever เลือก block ด้วย vector (ตอนนี้ใช้ token overlap ซึ่งพอสำหรับ
   จำนวน block ต่อ session ที่น้อย)
4. **Infra:** ย้าย `google.generativeai` (deprecated) -> `google.genai` (ค้างจากรอบ 7, นอกขอบเขต prompt)
5. **Data-layer (นอกขอบเขตที่สั่งห้าม):** re-chunk เลขหน้าตาราง AAFP ให้เป๊ะ 100%

---

## 6. สรุป (bullet)

- **Drift แก้ที่ราก:** reproduce ได้ -> Case Anchor (0ms) + Continuity rules -> follow-up อยู่กับเคสเดิมเสมอ
  ทดสอบ D1/D5/D7/G10/G11 ผ่านหมด และเป็น generic ทุกโรคใน scope ไม่ fix รายเคส
- **Context Management ตามไอเดีย optimize_8:** Recent Window + Immutable Compaction Blocks
  (ห้ามสรุปทับ) + Block Retriever -- ใช้ได้จริงกับระบบปัจจุบัน จึง apply เต็ม
- **อ้างอิงครบทุกแหล่ง:** เคสเด็กที่สองเล่มทับกัน อ้างคู่เสมอ (แม้ตรงกัน) + ตารางเปรียบเทียบ + footnote ระยะเวลา
- **คำนวณ:** mL step-by-step + ที่มาความแรง + scale ได้ + ตารางสรุปขนาดยา markdown
- **Cold start:** พิมพ์มั่ว/ทักทายแชทใหม่ = 0.0s (0 LLM call)
- **Latency:** memory เขียน background, ข้าม search แชทสั้น, shared embed cache -- เร็วขึ้นโดยคุณภาพไม่ลด
- **Validation:** Regression 21/21 + Opt-8 20/20 = **41/41 (100%)**, unit tests ผ่านหมด, checklist 100%
- **ขอบเขต:** query-time + generation + history เท่านั้น -- ไม่แตะ vector store เอกสาร/chunk/frontend
