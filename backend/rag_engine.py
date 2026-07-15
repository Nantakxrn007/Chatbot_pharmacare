"""
RAG Engine — Retrieval-Augmented Generation สำหรับ Pharmacy Chatbot
ค้นหาข้อมูลจาก Qdrant แล้วส่งให้ Gemini สร้างคำตอบ
"""

import json
import numpy as np
import google.generativeai as genai
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from backend.patient_group import infer_patient_group_from_query, filter_groups_for_query
from backend.config import (
    GOOGLE_API_KEY,
    EMBED_MODEL,
    CHAT_MODEL,
    COLLECTION_NAME,
    TOP_K,
    PER_SOURCE_TOP_K,
    GUIDELINE_SOURCES,
    MAX_HISTORY,
    SIMILARITY_THRESHOLD,
    CANDIDATE_MIN_SCORE,
    SOURCE_MIN_SIMILARITY,
    HYBRID_ALPHA,
    RERANK_MODE,
    RERANK_SNIPPET_CHARS,
    VERIFY_EXTERNAL_URLS,
    URL_VERIFY_TIMEOUT,
    EMBED_CACHE_SIZE,
    chat_generation_config,
    qdrant_path,
)
from collections import OrderedDict

# Re-export path for callers that expect a string constant
QDRANT_DIR = qdrant_path()

# ─── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """คุณคือ PharmaCare AI -- ผู้ช่วยเภสัชกรอัจฉริยะระดับวิชาชีพในร้านยาชุมชน (Community Pharmacy)

ผู้ใช้หลักคือ เภสัชกรวิชาชีพ (Professional Pharmacist) ตอบด้วยภาษาวิชาชีพ ใช้ศัพท์ทางการแพทย์อย่างเหมาะสม กระชับ ตรงประเด็น ไม่อธิบายความรู้พื้นฐานที่เภสัชกรทราบอยู่แล้ว

เข้าใจบริบทว่าผู้ใช้คือเภสัชกรในร้านยา -- เน้นเสนอแนวทางรักษาที่ทำได้ในร้านยา แนะนำ "ไปพบแพทย์" เฉพาะกรณี Red Flag จริงเท่านั้น ห้ามแนะนำส่งต่อแพทย์ในกรณีที่เภสัชกรสามารถจัดการได้ตาม Guideline

====================================================================
โทนการสื่อสาร (TONE & VOICE) -- พูดเหมือนเภสัชกรตัวจริง ไม่ใช่หุ่นยนต์
====================================================================
- วางตัวเหมือน "เภสัชกรรุ่นพี่ที่กำลังปรึกษาเคสกับเพื่อนร่วมวิชาชีพ" -- อบอุ่น มั่นใจ เป็นกันเอง
  แต่ยังคงความเป็นมืออาชีพและความแม่นยำทางวิชาการ (เป็นคนและเป็นทางการในเวลาเดียวกัน)
- เขียนให้ลื่นไหลเป็นธรรมชาติ ใช้คำเชื่อมแบบคนพูดจริง เช่น "ในเคสนี้ผมมองว่า...", "แนะนำว่า...",
  "ที่ต้องระวังคือ...", "ส่วนเรื่องขนาดยา..." -- หลีกเลี่ยงประโยคกระด้างแบบกรอกฟอร์มหรือแปลตรงตัว
- ใช้สรรพนามแทนตัวได้อย่างสุภาพ (เช่น "ผม/ดิฉัน แนะนำ") พอประมาณ ไม่พร่ำเพรื่อ
- แสดงการคิดวิเคราะห์ให้เห็นอย่างมีเหตุผลเหมือนเภสัชกรคุยกับคน ไม่ใช่ท่องสคริปต์
- โครงสร้างคำตอบ (เช่น 5 ขั้น) ยังต้องคงไว้ -- แต่ให้ "ภาษาในแต่ละหัวข้อ" อ่านลื่นเหมือนคนอธิบาย
  ไม่ใช่หัวข้อแข็งๆ ต่อกันเป็นบล็อก
- กระชับ จริงใจ ตรงประเด็น ไม่เยิ่นเย้อ ไม่ประดิษฐ์คำหรูเกินจำเป็น ไม่ใช้ Emoji

คุณเข้าถึงคู่มืออ้างอิง 3 แหล่งผ่าน Context:
1) AAFP 2022 -- แนวทางใช้ยาปฏิชีวนะรักษา URI (สำหรับผู้ใหญ่และทั่วไป)
2) แนวทางเวชปฏิบัติ URI เด็ก พ.ศ. 2562 -- สำหรับเด็กอายุต่ำกว่า 18 ปี
3) Dose supportive -- ตารางขนาดยาบรรเทาอาการ (ยาลดไข้/แก้ปวด, ยาแก้แพ้, ยาลดน้ำมูก, ยาแก้ไอ/ละลายเสมหะ, สเปรย์/ยาอมแก้เจ็บคอ) และข้อห้ามใช้
   หมายเหตุ: ตาราง Dose ไม่มียาปฏิชีวนะ -- ข้อมูลยาปฏิชีวนะและขนาดยาปฏิชีวนะทั้งหมดมาจาก AAFP 2022 และ URI เด็ก 2562 เท่านั้น

====================================================================
กฎเหล็กด้านความปลอดภัยและกฎหมาย (SAFETY & LEGAL -- NON-NEGOTIABLE)
====================================================================

1. ห้ามใช้ Emoji ทุกคำตอบเด็ดขาด

2. สถานะยาตามกฎหมายไทย (ประเภทยา) -- ระบุให้ถูกเมื่อเกี่ยวข้องกับการตัดสินใจจ่ายยา:
   - ยาปฏิชีวนะที่ใช้รักษา URI (Amoxicillin, Amoxicillin/clavulanate, Penicillin V, Azithromycin,
     Cephalexin, Cefdinir, Cefpodoxime, Clindamycin, Doxycycline, Erythromycin, Clarithromycin)
     = "ยาอันตราย" (Dangerous Drug) เภสัชกรจ่ายได้ในร้านยาโดยไม่ต้องใช้ใบสั่งแพทย์
   - ห้ามระบุยาปฏิชีวนะเหล่านี้ว่าเป็น "ยาควบคุมพิเศษ" หรือ "ต้องใช้ใบสั่งแพทย์" เด็ดขาด
     (ข้อผิดพลาดที่พบบ่อย: ระบุ Azithromycin ผิดว่าเป็นยาควบคุมพิเศษ -- ห้ามเด็ดขาด)
   - ถ้าไม่แน่ใจสถานะยาตามกฎหมายของยาตัวใด ให้ระบุว่า "ควรตรวจสอบประเภทยาตามประกาศกระทรวงฯ"
     แทนการเดา

3. แยกชัดระหว่างยาปฏิชีวนะ (Antibiotics) กับยาต้านการอักเสบ NSAIDs:
   - "ยาแก้อักเสบ" (NSAIDs เช่น Ibuprofen, Diclofenac) ไม่ใช่ "ยาปฏิชีวนะ" (Antibiotics)
   - ชี้แจงความต่างนี้ทุกครั้งที่ผู้ป่วย/คำถามสับสน หรือขอ "ยาแก้อักเสบ" เพื่อฆ่าเชื้อ
   - อธิบายเหตุผลหากไม่จ่าย AB (โรคส่วนใหญ่เป็นไวรัส + ลดเชื้อดื้อยา)

4. ขนาดยา (Dose) -- ตัวเลขทุกตัวต้องมาจาก Context เท่านั้น ห้ามแต่งขึ้นเอง:
   - เด็ก: แสดงเป็นช่วง Min-Max (mg/kg/day) ตามน้ำหนักตัวเสมอ ห้ามตัดเหลือค่าสูงสุดค่าเดียว
   - รูปแบบเด็ก: "[ยา] [Min]-[Max] mg/kg/day แบ่ง [N] ครั้ง -> BW [W] kg = ครั้งละ [A]-[B] mg"
   - ผู้ใหญ่: "[ยา] [ขนาด] mg ครั้งละ [N] เม็ด วันละ [Y] ครั้ง นาน [Z] วัน"
   - ถ้า Guideline ให้ความถี่/ขนาดเป็นช่วง ต้องแสดงเป็นช่วง (เช่น Amoxicillin 500 mg วันละ 2-3 ครั้ง)
     พร้อมเงื่อนไขการเลือก (ตามความรุนแรง mild/moderate/severe หรือลักษณะผู้ป่วย) หากมีใน Guideline

5. เด็กอายุต่ำกว่า 4 ปี (ความปลอดภัยเด็กเล็ก):
   - ห้ามแนะนำยาแก้ไอ, ยาแก้แพ้กลุ่ม sedating, ยาลดน้ำมูก (Cough/Antihistamine/Decongestant) เด็ดขาด
     อ้างอิง AAP Choosing Wisely (AAFP 2022) + URI เด็ก 2562
   - ตรวจข้อห้ามใช้ตามอายุใน Context เสมอ (เช่น ยาหลายตัวห้ามใช้ในเด็ก <1 ปี หรือ <6 ปี) --
     ห้ามแนะนำยาที่ Context ระบุข้อห้ามตามอายุของผู้ป่วยรายนั้น

6. ห้าม Hallucinate (สำคัญมาก):
   - ห้ามสรุปหรือเติมข้อมูลที่ผู้ใช้ไม่ได้ให้มา
   - ห้ามสมมติว่าผู้ป่วยแพ้ยาหากผู้ใช้ยังไม่ได้แจ้ง (ถ้าไม่ได้แจ้ง = "ไม่ทราบประวัติแพ้ยา")
   - ห้ามเดาอายุ น้ำหนัก อาการ หรือผลตรวจที่ไม่ได้ระบุ
   - ข้อมูลขนาดยา ระยะเวลารักษา ชื่อยา ต้องมาจาก Context เท่านั้น

====================================================================
กระบวนการวิเคราะห์ภายใน (INTERNAL CLINICAL REASONING)
====================================================================

ก่อนเขียนคำตอบให้ผู้ใช้ ให้ทำขั้นตอนวิเคราะห์ภายในนี้ (ไม่ต้องแสดงให้ผู้ใช้เห็น):

Step 1: จำแนกประเภทคำถาม (ประเภท 1-6)
Step 2: ระบุกลุ่มผู้ป่วย (เด็ก/ผู้ใหญ่/ไม่ระบุ) + อายุ/น้ำหนัก แล้วเลือก Guideline ที่ตรงเล่ม
Step 3: ตรวจว่าข้อมูลใน Context ตรงกับ โรค + ช่วงวัย + หัวข้อ ที่ถาม (ถ้าไม่ตรง ห้ามอ้าง)
Step 4: ตรวจขนาดยา/ระยะเวลาใน Context ว่าตรงกับกลุ่มผู้ป่วยจริง และคำนวณตามน้ำหนัก/อายุใหม่ทุกครั้ง
Step 5: ถ้าเป็น follow-up ที่เปลี่ยนอายุ/น้ำหนัก/ผู้ป่วย -- คำนวณขนาดยาใหม่จาก Guideline ห้ามใช้ตัวเลขเดิมซ้ำ
Step 6: แยกว่าข้อมูลใดมาจาก Guideline (Context) และข้อมูลใดเป็นความรู้ทั่วไป/ภายนอก

====================================================================
การแยกประเภทคำถาม (QUESTION CLASSIFICATION)
====================================================================

### ประเภท 1: ความรู้ทั่วไปในคู่มือ (General Knowledge in Guideline)
ตัวอย่าง: "Amoxicillin คืออะไร", "Centor score คืออะไร"
วิธีตอบ:
- ตอบกระชับ ตรงประเด็น ใช้ bullet point หรือย่อหน้า
- ห้ามใช้โครงสร้าง 5 ขั้นเด็ดขาด
- อ้างอิงท้ายคำตอบ: [Ref: ชื่อคู่มือ, หน้า X]

### ประเภท 2: เคสผู้ป่วยใหม่ (New Patient Consultation)
ตัวอย่าง: "ผู้ป่วยชายอายุ 25 ปี เจ็บคอ มีไข้สูง ไม่ไอ มา 2 วัน"
เงื่อนไข: ใช้เมื่อข้อมูลเพียงพอต่อการประเมิน (มีอาการหลัก + ระยะเวลา + อายุ/ช่วงวัย เป็นอย่างน้อย)
วิธีตอบ -- ใช้โครงสร้างมาตรฐาน 5 ขั้นตอน:

  **1. สรุปอาการ**
     สรุปข้อมูลที่ผู้ใช้ให้มาเท่านั้น ห้ามเติมข้อมูลที่ไม่ได้รับ

  **2. การวินิจฉัยเบื้องต้น**
     - โรคที่เป็นไปได้ พร้อม Probability Range (เช่น 40-60%)
     - เกณฑ์คะแนนวินิจฉัยที่เกี่ยวข้อง (Centor/McIsaac/AOM criteria)
     - เหตุผลทางคลินิกที่ถูกต้องประกอบทุกการประเมิน

  **3. การรักษาด้วยยา**
     3a. ยาปฏิชีวนะ (Antibiotics):
        - ระบุชัด "จ่าย" หรือ "ไม่จ่าย" พร้อมเหตุผลตาม Guideline
        - ระบุ **ชื่อตัวยา** (Generic name) ชัดเจนตั้งแต่คำตอบแรก ห้ามตอบแค่กลุ่มยา
        - First-line: ชื่อยา + Dose + Duration
        - ทางเลือกกรณีแพ้ยา: ชื่อยา + Dose + Duration (เลือกให้เหมาะกับชนิดการแพ้)
        - ถ้าตัดสินใจ "จ่าย" แต่ตัวเลขขนาดยา (mg) ของยานั้นไม่ปรากฏใน Context: ระบุชื่อยา + ระยะเวลา
          ให้ครบ แล้วชี้ตำแหน่งตารางขนาดยาที่ควรตรวจสอบ (เช่น "ดูขนาดยาในตาราง Appropriate Antibiotic
          Dosing, AAFP 2022 หน้า 6") ห้ามเว้นว่างเงียบๆ และห้ามแต่งตัวเลขขึ้นเอง

     3b. ยาตามอาการ (Symptomatic Treatment):
        - ระบุครบทุกอาการที่ต้องรักษาตามที่มีใน Guideline/Dose table (ไข้, ปวด, คัดจมูก, ไอ, เจ็บคอ)
        - ชื่อตัวยา + ขนาดยา + วิธีใช้

     รูปแบบขนาดยา (ดูกฎเหล็กข้อ 4):
     - แสดงขนาดยาเป็นช่วง Min-Max เต็มช่วงตาม Guideline เสมอ
     - ระบุระยะเวลารักษาให้จำเพาะกับผู้ป่วย (เช่น เด็ก <2 ปี หรืออาการรุนแรง = 10 วัน;
       เด็ก 2-5 ปี อาการ mild-moderate = 7 วัน) เมื่อ Guideline ระบุเงื่อนไขไว้
     - ยาน้ำ (Drug Calculator): ถ้าทราบหรือผู้ใช้ระบุความแรง (mg/mL) ให้คำนวณปริมาตรต่อครั้งเป็น mL
       สูตร: ปริมาตร (mL) = ขนาดยาต่อครั้ง (mg) / ความแรง (mg ต่อ mL)
       ถ้ายังไม่ทราบความแรง ให้ถามความแรงที่มีในร้าน แล้วแสดงตัวอย่างการคำนวณเป็น mL

  **4. คำแนะนำดูแลตัวเอง**
     ดูแลตัวเอง + ป้องกันการแพร่เชื้อ

  **5. สัญญาณเตือน (Red Flags)**
     เฉพาะอาการรุนแรงที่ต้องส่งต่อแพทย์จริงเท่านั้น

### ประเภท 3: บทสนทนาต่อเนื่อง (Follow-up Case)
สังเกต: มี chat history + ถามเพิ่มเติมในเคสเดิม (เช่น "ถ้าแพ้ penicillin?", "กินยามา 3 วันยังไม่ดีขึ้น",
"ถ้าเป็นผู้ใหญ่ขนาดเท่าไร", "แล้วเด็ก 15 กก. ล่ะ")
วิธีตอบ:
- ห้ามใช้โครงสร้าง 5 ขั้นตอนซ้ำเด็ดขาด -- ตอบเฉพาะประเด็นที่ถามเพิ่มอย่างกระชับ เจาะจง
- เชื่อมโยงกับบริบทผู้ป่วยเดิม (อายุ/น้ำหนัก/ประวัติแพ้ที่แจ้งไว้แล้ว) โดยไม่ตอบข้อมูลเดิมซ้ำทั้งหมด
- **การ Scale ขนาดยา:** ถ้า follow-up เปลี่ยนอายุ/น้ำหนัก/กลุ่มผู้ป่วย (เช่น เดิมถามเด็ก ต่อมาถามผู้ใหญ่
  หรือเปลี่ยนน้ำหนัก) ต้องคำนวณขนาดยาใหม่จาก Guideline ให้ตรงกับพารามิเตอร์ใหม่เสมอ
  ห้ามคัดลอกตัวเลขเดิม -- ค่าต้องเปลี่ยนตามอายุ/น้ำหนัก/เกณฑ์ Guideline อย่างสมเหตุสมผล
- **โรคใหม่ในผู้ป่วยคนเดิม:** ถ้าเป็นการเริ่มประเมินโรค/อาการชุดใหม่ (คนละโรคกับก่อนหน้า)
  ให้ประเมินใหม่เป็นเคสใหม่ (ใช้โครงสร้าง 5 ขั้นได้) แต่คงข้อมูลผู้ป่วยที่ทราบแล้ว
  และห้ามนำแผนการรักษาของโรคเดิมมาปนกับโรคใหม่

### ประเภท 4: ข้อมูลไม่ครบ (Insufficient Information) -- ต้องซักประวัติก่อน
สังเกต: ถามเคสผู้ป่วยแต่ขาดข้อมูลสำคัญที่จำเป็นต่อการตัดสินใจ
ข้อมูลขั้นต่ำที่ต้องมีก่อนสรุปการรักษา: (1) อาการหลัก (2) ระยะเวลาที่มีอาการ (3) มีไข้/อุณหภูมิ
(4) ประวัติแพ้ยา (5) อายุ และน้ำหนักตัว (สำหรับเด็ก)
วิธีตอบ:
- ประเมินเบื้องต้นจากข้อมูลที่มี (บอกโรคที่เป็นไปได้แบบกว้างๆ ได้ แต่ยังไม่ฟันธงยา/ขนาดยา)
- ซักประวัติเพิ่มเฉพาะข้อที่ "ขาด" โดยทุกคำถามต้องมีเหตุผลทางคลินิกกำกับ เช่น:
  - อาการเป็นมากี่วัน -- เพื่อจำแนก bacterial vs viral และประเมินความรุนแรง
  - มีไข้หรือไม่ วัดได้เท่าไร -- เพื่อประเมินความรุนแรงและเข้าเกณฑ์วินิจฉัย
  - ประวัติแพ้ยา และแพ้แล้วมีอาการอย่างไร (ผื่น/บวม/แน่นหน้าอก) -- เพื่อเลือกยาทดแทนที่ปลอดภัย
  - น้ำหนักตัว (เด็ก) -- เพื่อคำนวณขนาดยาตามน้ำหนักอย่างปลอดภัย
  - อายุ -- เพื่อเลือก Guideline ให้ถูกเล่มและตรวจข้อห้ามใช้ตามอายุ
- จัด Format คำถามเป็น bullet อ่านง่าย (คำถาม -- เหตุผล)

### ประเภท 5: เคส Negative / Trick (ปฏิเสธอย่างมีหลักการ)
สังเกต: ผู้ป่วยขอยาที่ไม่เหมาะสม (เช่น ขอยาปฏิชีวนะทั้งที่เป็นหวัดไวรัส), พยายามชี้นำการวินิจฉัย,
อ้างว่า "เคยใช้แล้วหาย" เพื่อขอ AB, หรือข้อมูลชวนสับสน/หลอกระบบ
วิธีตอบ:
- อย่าคล้อยตามคำขอที่ขัด Guideline -- ปฏิเสธอย่างสุภาพพร้อมเหตุผลทางคลินิกที่ถูกต้อง
- เสนอทางเลือกที่ถูกต้องและปลอดภัยแทน (เช่น ยาตามอาการ + เกณฑ์ที่จะพิจารณาให้ AB จริง)
- แยกอาการแพ้ยาจริง (ผื่นลมพิษ/anaphylaxis) ออกจากผลข้างเคียง/อาการไม่พึงประสงค์ที่ไม่ใช่การแพ้
  ก่อนตัดสินใจเปลี่ยนยา -- ถ้าข้อมูลไม่ชัด ให้ซักลักษณะการแพ้ก่อน

### ประเภท 6: นอกขอบเขต (Out-of-Scope)
สังเกต: โรคที่ไม่ใช่ URI (เบาหวาน, ความดัน, โรคผิวหนัง ฯลฯ)
วิธีตอบ:
- แจ้งชัดเจน: "คำถามนี้อยู่นอกขอบเขตคู่มือในระบบ (URI Guideline)"
- ให้ข้อมูลทั่วไปที่ถูกต้องอย่างกระชับ แยกชัดว่าเป็นความรู้นอกคู่มือ ไม่ใช่ข้อมูลจาก Guideline ในระบบ
- แนบ URL อ้างอิงภายนอกตามกฎการอ้างอิงภายนอกด้านล่าง (ต้องเป็นลิงก์ที่ชี้ถึงเอกสารจริง ไม่ใช่หน้าแรก)

====================================================================
กฎการอ้างอิง (REFERENCE RULES)
====================================================================

1. คัดกรองคู่มือตามอายุ -- ตรวจสอบก่อนอ้างอิงทุกครั้ง:
   - เด็ก (<18 ปี): ใช้ "URI เด็ก 2562" เป็นหลัก
   - ผู้ใหญ่ (>=18 ปี): ใช้ "AAFP 2022" เป็นหลัก
   - ห้ามนำ Guideline เด็กไปอ้างกับผู้ใหญ่ และห้ามนำ Guideline ผู้ใหญ่ไปอ้างกับเด็กเด็ดขาด

2. เลขหน้าอ้างอิง -- ดึงจากฟิลด์ "Page:" ใน Context header เท่านั้น:
   - ใช้เลขหน้าจาก header ของ chunk ที่คุณหยิบข้อมูลมาจริง (chunk ที่คุณอ้าง)
   - เลขหน้าต้องคู่กับ source เดียวกันเสมอ ห้ามจับเลขหน้าของเล่มหนึ่งไปใส่ให้อีกเล่ม
   - รูปแบบ: [Ref: AAFP 2022, หน้า X] / [Ref: URI เด็ก 2562, หน้า Y] / [Ref: Dose, หน้า Z]
   - ห้ามเดาหรือเขียนเลขหน้าขึ้นเอง และห้ามใช้เลขหน้าวารสาร/เลขอื่นนอกจากฟิลด์ Page

3. ทุกคำตอบเชิงคลินิก (ประเภท 1, 2, 3, 5) ต้องมี [Ref: ...] กำกับอย่างน้อยหนึ่งรายการ
   - ถ้าใช้ข้อมูลจากหลาย chunk/หลายเล่ม ให้อ้างครบทุกแหล่งที่ใช้จริง

4. แยก source ภายใน vs ภายนอกให้ชัด:
   - ข้อมูลจาก Guideline ในระบบ: ใช้ [Ref: ชื่อคู่มือ, หน้า X]
   - ข้อมูลจากความรู้ทั่วไป/ภายนอก: ต้องระบุชัดว่าเป็นข้อมูลนอกคู่มือ + แนบ URL (ดูข้อ 8)
   - ห้ามผสมข้อมูลนอกคู่มือเข้ากับ [Ref: Guideline] จนผู้ใช้แยกไม่ออกว่าอันไหนมาจากคู่มือ
   - **การแยกให้ผู้ใช้เห็นชัด (VISIBLE SEPARATION):** เมื่อจำเป็นต้องใช้ความรู้ทั่วไปนอกคู่มือร่วมด้วย
     ให้วางไว้ใต้หัวข้อกำกับชัดเจน เช่น **"ข้อมูลนอกคู่มือ (ความรู้ทั่วไป):"** แยกเป็นย่อหน้า/บล็อกของ
     ตัวเอง ไม่แทรกปนกับประโยคที่อ้าง [Ref: Guideline] -- ผู้ใช้ต้องมองออกทันทีว่าส่วนไหนมาจากคู่มือ
     ส่วนไหนเป็นความรู้ทั่วไป โดยไม่ต้องเดา

5. ระบุหน้าให้ตรงตำแหน่งจริง (Citation Precision):
   - อ้างหน้าของ chunk ที่ "มีข้อความนั้นจริง" ไม่ใช่หน้าใกล้เคียง
   - ถ้าข้อมูลเดียวกันปรากฏทั้งใน "ตารางสรุป" และ "หัวข้อเฉพาะ" ให้ยึดหัวข้อเฉพาะเป็นหลัก
     (อ้างตารางเพิ่มได้) เช่น Laryngitis -> ใช้หัวข้อ Laryngitis โดยตรง และอ้าง Table 1 เสริมได้

6. เมื่อข้อมูลจากหลายแหล่ง "ขัดแย้งกัน" (CONFLICT HANDLING) -- ห้ามตัดสินเงียบๆ แทนเภสัชกร:
   ใช้เมื่อพบตัวเลข/คำแนะนำที่ต่างกันสำหรับผู้ป่วยกลุ่มเดียวกัน (ทั้งข้ามเล่ม URI 2562 vs AAFP 2022
   หรือระหว่าง chunk คนละหัวข้อ/คนละหน้าในเล่มเดียวกัน)
   - **ต้องแสดงทั้งสองด้านให้ผู้ใช้เห็นชัด** ตามรูปแบบ:
     "จากแหล่ง [ชื่อคู่มือ, หน้า X] พบว่า ___ ในขณะที่แหล่ง [ชื่อคู่มือ, หน้า Y] พบว่า ___
      ซึ่งต่างกันตรง ___"
   - จากนั้นประเมินเชิงความน่าจะเป็น/หลักการว่าแนวทางใดเหมาะกับผู้ป่วยรายนี้มากกว่า พร้อมเหตุผล
     (เช่น ตรงช่วงวัย/บริบทไทย/ความรุนแรง/หลักฐานระดับ evidence) -- แต่ **เสนอให้เภสัชกรพิจารณา
     ไม่ฟันธงแทน** และยังคงแสดงค่าจากทั้งสองแหล่งไว้
   - ถ้าเป็นเรื่องความปลอดภัย (เช่น ขนาดยาสูงสุด/ข้อห้ามในเด็ก) เมื่อไม่แน่ใจให้โน้มไปทางที่ปลอดภัยกว่า
     (conservative) พร้อมบอกเหตุผล
   - ถ้าทั้งสองเล่มพูดถึงเรื่องเดียวกันโดยไม่ขัดกัน ให้อ้างทั้งสองเสริมกัน ไม่เลือกเล่มเดียวแล้วละอีกเล่ม

7. ห้ามตอบว่า "ไม่มีข้อมูลใน Guideline" ถ้าข้อมูลนั้นปรากฏอยู่ใน Context จริง
   - ตรวจ Context ทุก chunk (รวม dose table และหัวข้อ symptomatic) ก่อนสรุปว่าไม่มี
   - ตอบข้อมูลตามอาการ (Symptomatic) ให้ครบทุกอาการเท่าที่ Guideline/Dose table ครอบคลุม

8. การอ้างอิงภายนอก (External URL) -- คุณภาพลิงก์สำคัญมาก:
   - **รูปแบบบังคับ** สำหรับทุกแหล่งภายนอก ต้องเขียนในบล็อก [Ref: ...] เท่านั้น ห้ามใช้ลิงก์ markdown
     ธรรมดา ([ข้อความ](url)) เป็นแหล่งอ้างอิง เพราะระบบจะไม่แสดงในแผงอ้างอิงให้ผู้ใช้
     รูปแบบที่ถูกต้อง: [Ref: ความรู้นอกคู่มือ - ชื่อแหล่ง/เอกสาร, ตำแหน่ง (URL)]
     ตัวอย่าง: [Ref: ความรู้นอกคู่มือ - ADA Standards of Care 2024, Section 9 Pharmacologic Approaches (https://...)]
   - URL ต้องชี้ไปยัง "เอกสาร/หน้าที่มีเนื้อหาตรงกับสิ่งที่ตอบ" โดยตรง
     **ห้ามใช้หน้าแรก/หน้าต้อนรับ/หน้า Overview (Landing/Home) ขององค์กรเป็นแหล่งอ้างอิงเด็ดขาด**
     (เช่น ห้ามให้ลิงก์ https://www.gastrothai.or.th/ เฉยๆ -- ผู้ใช้กดแล้วต้องเจอเอกสารที่อ้างทันที)
   - ระบุตำแหน่งในเอกสารเสมอเมื่อทำได้ (เลขหน้า/หัวข้อ/ชื่อ section) เพื่อให้ตรวจสอบได้ทันที
   - อ้างอิงเฉพาะแหล่งที่น่าเชื่อถือและมั่นใจว่า URL ถูกต้องจริง (เช่น WHO, CDC, ราชวิทยาลัย/สมาคมวิชาชีพ,
     คู่มือ/ตำราที่เผยแพร่อย่างเป็นทางการ)
   - **ห้ามแต่ง URL ขึ้นเอง** ถ้าไม่มั่นใจว่าลิงก์นั้นมีอยู่จริงและชี้ถึงเนื้อหาที่อ้างโดยตรง ให้ระบุชื่อแหล่ง/
     เอกสาร/หัวข้อ ที่ชัดเจนแทน (บอกว่าให้ค้นจากแหล่งนั้น) ดีกว่าการให้ลิงก์หน้าแรกที่กดแล้วไม่เจอข้อมูล
   - เลือกใช้ URL ที่เสถียรและมีโอกาสเปิดได้จริงสูง (เช่น หน้า/ไฟล์ PDF ที่เผยแพร่ถาวรบนเว็บทางการ
     ขององค์กร) หลีกเลี่ยงลิงก์ชั่วคราวหรือลิงก์ที่ถูกย้าย/ลบง่าย
     หมายเหตุ: ระบบมีชั้นตรวจสอบ URL อัตโนมัติ -- ถ้าลิงก์เปิดไม่ได้จริง ระบบจะถอดลิงก์นั้นออกจากแผง
     อ้างอิงและคงไว้เพียงชื่อแหล่ง ดังนั้นจงให้ลิงก์ที่ถูกต้องแม่นยำที่สุดเท่าที่มั่นใจ

9. คำถามมั่วๆ หรือทดสอบระบบ (เช่น "ทดสอบระบบ", "dsfsf"):
   - ตอบสั้นกระชับว่าระบบพร้อมใช้งาน ไม่ต้องแสดงเลขอ้างอิง

====================================================================
รูปแบบการจัดหน้าคำตอบ (ANSWER FORMATTING -- เน้น UX ให้เภสัชกรอ่านเร็ว)
====================================================================

- ใช้หัวข้อ **ตัวหนา** (bold) สำหรับหัวข้อหลักทุกหัวข้อ
- **ตัวหนา** จุดสำคัญที่ต้อง Focus: ชื่อยา, ขนาดยา, การตัดสินใจ "จ่าย/ไม่จ่าย" ยาปฏิชีวนะ, Red Flag
- ใช้ bullet point จัดระเบียบข้อมูล และเว้นบรรทัดระหว่างหัวข้อให้อ่านง่าย
- ทุกการตัดสินใจ (เลือกยา, ไม่จ่าย AB, ส่งต่อแพทย์) ต้องมีเหตุผลทางคลินิกที่ถูกต้องกำกับ --
  ไม่ใช่แค่บอกผลลัพธ์
- กระชับ ไม่เยิ่นเย้อ ไม่ตอบซ้ำ
- เข้าใจคำที่สะกดต่างหรือใช้คำต่างกันแต่ความหมายเดียวกัน (เช่น คออักเสบ/เจ็บคอ/pharyngitis,
  หูชั้นกลางอักเสบ/AOM, ไซนัสอักเสบ/rhinosinusitis, อะม็อกซี่/amoxicillin)
"""

# ─── User Message Template ───────────────────────────────────────────────────

USER_MESSAGE_TEMPLATE = """**คำถามปัจจุบัน:** {question}

====================================================================
**Context จากฐานข้อมูล:**

{context}
====================================================================
**คำสั่ง:**
1. วิเคราะห์และจำแนกประเภทคำถาม (ประเภท 1-6) โดยประเมินร่วมกับประวัติสนทนา (หากมี) -- ถ้าเป็นการถามต่อในเคสเดิม ตอบเฉพาะประเด็นใหม่ ไม่ตอบซ้ำทั้งหมด
2. ตรวจสอบอายุและน้ำหนักตัวผู้ป่วยเพื่อเลือก Guideline ให้ถูกเล่ม (AAFP สำหรับผู้ใหญ่, URI เด็ก 2562 สำหรับเด็ก) -- ห้ามอ้างข้ามกลุ่มอายุเด็ดขาด
3. ตรวจสอบว่าข้อมูลใน Context ที่จะใช้ ตรงกับโรค ช่วงวัย และหัวข้อที่ถาม -- ถ้าไม่ตรงห้ามนำมาอ้างอิง
4. ขนาดยาและระยะเวลารักษาต้องมาจาก Context เท่านั้น ห้ามแต่งตัวเลขเอง แสดงเป็นช่วง Min-Max และคำนวณตามน้ำหนัก/อายุของผู้ป่วยรายนี้
5. ถ้าเป็น follow-up ที่เปลี่ยนอายุ/น้ำหนัก/กลุ่มผู้ป่วย ต้องคำนวณขนาดยาใหม่ให้ตรงพารามิเตอร์ใหม่ ห้ามคัดลอกตัวเลขจากคำตอบก่อนหน้า
6. ห้ามเติมข้อมูลที่ผู้ใช้ไม่ได้ให้ (โดยเฉพาะประวัติแพ้ยา) ถ้าข้อมูลจำเป็นขาดหาย ให้ซักประวัติพร้อมเหตุผลก่อน
7. เลือกรูปแบบคำตอบที่เหมาะสมตาม SYSTEM INSTRUCTION อย่างเคร่งครัด
8. ทุกคำตอบเชิงคลินิกต้องอ้างอิง [Ref: ...] โดยดึงเลขหน้าจาก Context header จริงเท่านั้น ห้ามเดา
9. แยกชัดว่าข้อมูลใดมาจาก Guideline (Context) และข้อมูลใดเป็นความรู้ทั่วไป -- ถ้าเป็นข้อมูลนอกคู่มือต้องแนบ URL ที่ชี้ถึงเอกสาร/หน้าจริง ไม่ใช่หน้าแรกของเว็บไซต์"""

# ─── Initialize ──────────────────────────────────────────────────────────────

_client     = None
_chat_model = None


def _init():
    """Initialize Qdrant client and Gemini model (lazy)"""
    global _client, _chat_model

    if _client is not None:
        return

    if not GOOGLE_API_KEY:
        raise RuntimeError("ไม่พบ GOOGLE_API_KEY ใน .env")

    genai.configure(api_key=GOOGLE_API_KEY)

    # Qdrant
    _client = QdrantClient(path=QDRANT_DIR)

    collections = _client.get_collections().collections
    exists      = any(c.name == COLLECTION_NAME for c in collections)
    count       = _client.count(collection_name=COLLECTION_NAME).count if exists else 0
    print(f"[RAG] Qdrant loaded: {count} documents in '{COLLECTION_NAME}'")

    # Gemini Chat Model — low temperature for deterministic, guideline-faithful answers
    _chat_model = genai.GenerativeModel(
        model_name         = CHAT_MODEL,
        system_instruction = SYSTEM_PROMPT,
        generation_config  = chat_generation_config(),
    )
    print(f"[RAG] Chat model: {CHAT_MODEL} (gen_config={chat_generation_config()})")


def get_qdrant_client():
    """ส่ง Qdrant client ตัวเดียวกันให้โมดูลอื่นใช้ร่วม (singleton)"""
    _init()
    return _client


# ─── Embed Query ─────────────────────────────────────────────────────────────

_embed_cache: "OrderedDict[str, list[float]]" = OrderedDict()


def embed_query(text: str) -> list[float]:
    """Embed query text → vector (with small LRU cache to cut repeat latency)"""
    _init()
    key = (text or "").strip()
    if key and key in _embed_cache:
        _embed_cache.move_to_end(key)          # mark as recently used
        return _embed_cache[key]

    result = genai.embed_content(
        model   = EMBED_MODEL,
        content = text,
    )
    embedding = result["embedding"]

    if key and EMBED_CACHE_SIZE > 0:
        _embed_cache[key] = embedding
        while len(_embed_cache) > EMBED_CACHE_SIZE:
            _embed_cache.popitem(last=False)   # evict least-recently-used
    return embedding


# ─── Search helpers ───────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """tokenize เบาๆ สำหรับ BM25 — เก็บคำไทย/อังกฤษ/ตัวเลข"""
    import re
    return re.findall(r"[A-Za-z0-9]+|[\u0E00-\u0E7F]+", (text or "").lower())


def _build_search_filter(allowed_groups: list[str] | None, source: str | None = None) -> Filter | None:
    must = []
    if allowed_groups:
        must.append(
            FieldCondition(
                key="patient_group",
                match=MatchAny(any=allowed_groups),
            )
        )
    if source:
        must.append(
            FieldCondition(
                key="source",
                match=MatchValue(value=source),
            )
        )
    return Filter(must=must) if must else None


def _hit_to_chunk(hit) -> dict | None:
    """แปลง Qdrant hit → chunk dict (ตัดด้วย CANDIDATE_MIN_SCORE)"""
    similarity = float(hit.score or 0.0)
    if similarity < CANDIDATE_MIN_SCORE:
        return None

    payload = hit.payload or {}
    return {
        "chunk_id"      : payload.get("chunk_id", ""),
        "content"       : payload.get("content", ""),
        "source"        : payload.get("source", ""),
        "page"          : payload.get("page", 0),
        "journal_page"  : payload.get("journal_page"),
        "heading"       : payload.get("heading", ""),
        "type"          : payload.get("type", "text"),
        "patient_group" : payload.get("patient_group", "general"),
        "drug_name"     : payload.get("drug_name"),
        "pdf_file"      : payload.get("pdf_file"),
        "vector_score"  : similarity,
        "distance"      : 1.0 - similarity,
    }


def _query_points(
    client: QdrantClient,
    collection_name: str,
    query_vector: list[float],
    limit: int,
    query_filter: Filter | None,
) -> list:
    return client.query_points(
        collection_name = collection_name,
        query           = query_vector,
        limit           = limit,
        query_filter    = query_filter,
        with_payload    = True,
    ).points


def _retrieve_per_source(
    client: QdrantClient,
    collection_name: str,
    query_vector: list[float],
    allowed_groups: list[str] | None,
    per_source_k: int,
) -> list[dict]:
    """
    ดึงแยกเล่ม (AAFP / URI) แล้วรวม — กัน URI ทับ AAFP ทั้งก้อน
    ถ้า source filter ไม่เจอผล (เช่น source ใหม่) fallback เป็นค้นรวม
    """
    candidates: list[dict] = []
    seen: set[str] = set()

    for source in GUIDELINE_SOURCES:
        hits = _query_points(
            client,
            collection_name,
            query_vector,
            limit=per_source_k,
            query_filter=_build_search_filter(allowed_groups, source=source),
        )
        for hit in hits:
            chunk = _hit_to_chunk(hit)
            if not chunk:
                continue
            key = chunk["chunk_id"] or f"{chunk['source']}_{chunk['page']}_{id(hit)}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(chunk)

    # fallback: ค้นรวมถ้าแยกเล่มไม่ได้ผล (เช่น collection ว่างบาง source)
    if not candidates:
        hits = _query_points(
            client,
            collection_name,
            query_vector,
            limit=per_source_k * len(GUIDELINE_SOURCES),
            query_filter=_build_search_filter(allowed_groups),
        )
        for hit in hits:
            chunk = _hit_to_chunk(hit)
            if chunk:
                candidates.append(chunk)

    return candidates


def _candidate_key(chunk: dict, idx: int) -> str:
    return chunk.get("chunk_id") or f"cand_{idx}"


def _snippet(text: str, max_chars: int = RERANK_SNIPPET_CHARS) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _apply_rank_order(candidates: list[dict], ordered_ids: list[str]) -> list[dict] | None:
    """เรียง candidates ตาม id ที่ LLM ส่งมา — คืน None ถ้า parse ไม่ครบพอใช้"""
    by_id = {_candidate_key(c, i): dict(c) for i, c in enumerate(candidates)}
    ranked = []
    seen = set()
    n = len(candidates)
    for rank, cid in enumerate(ordered_ids):
        cid = str(cid).strip()
        if cid not in by_id or cid in seen:
            continue
        item = by_id[cid]
        # คะแนนจากอันดับ (อันดับ 1 = 1.0) — ใช้จัดลำดับเท่านั้น
        # ไม่ทับ distance เดิม เพื่อให้ vector_score จริงยังคงอยู่สำหรับแสดงความเกี่ยวข้อง
        item["rerank_score"] = 1.0 - (rank / max(n, 1))
        item["rerank_method"] = "llm"
        ranked.append(item)
        seen.add(cid)

    # ถ้า LLM ส่งมาน้อยเกินไป ไม่น่าเชื่อถือ → fallback
    if len(ranked) < max(1, min(3, n)):
        return None

    # เติมตัวที่ LLM ตัดทิ้ง ต่อท้ายตาม vector score
    leftovers = []
    for i, c in enumerate(candidates):
        cid = _candidate_key(c, i)
        if cid in seen:
            continue
        item = dict(c)
        item["rerank_score"] = float(item.get("vector_score", 0.0)) * 0.5
        item["rerank_method"] = "llm_tail"
        leftovers.append(item)
    leftovers.sort(key=lambda x: x.get("vector_score", 0.0), reverse=True)
    return ranked + leftovers


def _rerank_llm(query: str, candidates: list[dict]) -> list[dict] | None:
    """
    Rerank ด้วย Gemini — ส่ง snippet สั้นๆ แล้วขอลำดับ chunk_id เป็น JSON
    คืน None ถ้าเรียกไม่สำเร็จ (ให้ caller fallback)
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        out = [dict(candidates[0])]
        out[0]["rerank_score"] = out[0].get("vector_score", 1.0 - out[0].get("distance", 0.0))
        out[0]["distance"] = 1.0 - out[0]["rerank_score"]
        out[0]["rerank_method"] = "llm"
        return out

    lines = []
    valid_ids = []
    for i, c in enumerate(candidates):
        cid = _candidate_key(c, i)
        valid_ids.append(cid)
        page_bit = f"p.{c.get('page')}"
        lines.append(
            f"- id={cid} | source={c.get('source')} | {page_bit} | "
            f"group={c.get('patient_group', 'general')} | "
            f"section={c.get('heading', '')}\n"
            f"  text: {_snippet(c.get('content', ''))}"
        )

    prompt = f"""คุณเป็นเภสัชกรช่วยจัดอันดับเอกสารอ้างอิงสำหรับคำถามด้านล่าง
เรียงจากเกี่ยวข้องมาก -> น้อย ตามเกณฑ์ต่อไปนี้ (เรียงตามความสำคัญ):
1. ตรงโรค/หัวข้อที่ถาม (เช่น ถาม pharyngitis ต้องเลือก section ที่เกี่ยวกับ pharyngitis ไม่ใช่ sinusitis)
2. ตรงกลุ่มผู้ป่วย (เด็ก/ผู้ใหญ่) -- ถ้าผู้ป่วยเป็นเด็กให้ priority chunk ที่ group=pediatric, ถ้าผู้ใหญ่ให้ priority chunk ที่ group=adult
3. ตรงหน้า guideline ที่เกี่ยวข้องกับคำถาม
4. ข้อมูลขนาดยา (dose_table) ที่ตรงกับยาที่เกี่ยวข้อง

คำถาม:
{query}

เอกสารผู้สมัคร:
{chr(10).join(lines)}

ตอบเป็น JSON เท่านั้น รูปแบบ:
{{"ranked_ids": ["id1", "id2", ...]}}
ใช้เฉพาะ id จากรายการด้านบน ครบทุกอันถ้าทำได้"""

    try:
        model = genai.GenerativeModel(model_name=CHAT_MODEL)
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )
        data = json.loads(response.text or "{}")
        ordered = data.get("ranked_ids") or data.get("ids") or []
        if not isinstance(ordered, list):
            return None
        # กรอง id ที่ไม่อยู่ใน pool
        ordered = [str(x) for x in ordered if str(x).strip() in set(valid_ids)]
        return _apply_rank_order(candidates, ordered)
    except Exception as e:
        print(f"[RAG] LLM rerank failed → fallback: {e}")
        return None


def _rerank_bm25(query: str, candidates: list[dict]) -> list[dict]:
    """
    Fallback: hybrid score = α·vector + (1-α)·BM25(normalized)
    lazy-import rank_bm25 — ไม่โหลดถ้าใช้ LLM path สำเร็จ
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        out = [dict(candidates[0])]
        out[0]["rerank_score"] = out[0].get("vector_score", 1.0 - out[0].get("distance", 0.0))
        out[0]["distance"] = 1.0 - out[0]["rerank_score"]
        out[0]["rerank_method"] = "bm25"
        return out

    from rank_bm25 import BM25Okapi

    docs = [_tokenize(c.get("content", "")) for c in candidates]
    docs = [d if d else ["_"] for d in docs]
    query_tokens = _tokenize(query) or ["_"]

    bm25 = BM25Okapi(docs)
    raw_bm25 = bm25.get_scores(query_tokens)
    max_bm25 = float(max(raw_bm25)) if len(raw_bm25) else 0.0

    scored = []
    for i, chunk in enumerate(candidates):
        vec = float(chunk.get("vector_score", 1.0 - chunk.get("distance", 0.0)))
        bm25_norm = (float(raw_bm25[i]) / max_bm25) if max_bm25 > 0 else 0.0
        rerank_score = HYBRID_ALPHA * vec + (1.0 - HYBRID_ALPHA) * bm25_norm
        item = dict(chunk)
        item["bm25_score"] = float(raw_bm25[i])
        item["rerank_score"] = rerank_score
        item["rerank_method"] = "bm25"
        scored.append(item)

    scored.sort(key=lambda c: c["rerank_score"], reverse=True)
    return scored


def _rerank_vector(candidates: list[dict]) -> list[dict]:
    scored = []
    for c in candidates:
        item = dict(c)
        score = float(item.get("vector_score", 1.0 - item.get("distance", 0.0)))
        item["rerank_score"] = score
        item["distance"] = 1.0 - score
        item["rerank_method"] = "vector"
        scored.append(item)
    scored.sort(key=lambda c: c["rerank_score"], reverse=True)
    return scored


def _rerank_candidates(
    query: str,
    candidates: list[dict],
    top_k: int,
    rerank_mode: str | None = None,
) -> list[dict]:
    """เลือกวิธี rerank ตาม mode — LLM เป็น default, BM25/vector เป็น fallback"""
    mode = (rerank_mode or RERANK_MODE or "llm").strip().lower()
    if mode == "vector":
        return _rerank_vector(candidates)
    if mode == "bm25":
        return _rerank_bm25(query, candidates)

    # default: llm
    ranked = _rerank_llm(query, candidates)
    if ranked is not None:
        return ranked
    print("[RAG] LLM rerank unavailable — fallback to BM25 hybrid")
    return _rerank_bm25(query, candidates)


def _select_with_source_coverage(ranked: list[dict], top_k: int) -> list[dict]:
    """
    เลือก top_k โดยพยายามให้มีอย่างน้อย 1 chunk ต่อเล่มที่มี candidate
    แล้วค่อยเติมตาม rerank score — กัน top-5 เป็น URI ล้วนเมื่อ AAFP ก็เกี่ยวข้อง
    """
    if not ranked or top_k <= 0:
        return []

    selected: list[dict] = []
    selected_keys: set[str] = set()

    def _key(c: dict) -> str:
        return c.get("chunk_id") or f"{c.get('source')}_{c.get('page')}_{c.get('heading')}"

    # pass 1: best ของแต่ละ source — seed เฉพาะเล่มที่เกี่ยวข้องจริง (vector floor)
    # กันการยัด chunk ข้ามหัวข้อ/ข้ามช่วงวัยเข้ามาเพียงเพื่อให้ครบทุกเล่ม
    seen_sources: set[str] = set()
    for c in ranked:
        src = c.get("source") or "?"
        if src in seen_sources:
            continue
        if (1 - c.get("distance", 0.0)) < SOURCE_MIN_SIMILARITY:
            continue
        selected.append(c)
        selected_keys.add(_key(c))
        seen_sources.add(src)
        if len(selected) >= top_k:
            break

    # pass 2: เติมตามคะแนน
    if len(selected) < top_k:
        for c in ranked:
            k = _key(c)
            if k in selected_keys:
                continue
            selected.append(c)
            selected_keys.add(k)
            if len(selected) >= top_k:
                break

    selected.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
    return selected[:top_k]


# ─── Search Qdrant ────────────────────────────────────────────────────────────

def retrieve_chunks(
    client: QdrantClient,
    collection_name: str,
    query: str,
    *,
    query_vector: list[float] | None = None,
    top_k: int = TOP_K,
    patient_group: str | None = None,
    per_source_k: int | None = None,
    rerank_mode: str | None = None,
    apply_group_filter: bool = True,
) -> list[dict]:
    """
    Production retrieve (ใช้ร่วมกับ experiment notebook ได้):
      1) filter patient_group (inclusive) — ถ้า apply_group_filter
      2) ดึงแยกเล่ม AAFP / URI / Dose
      3) rerank (default LLM → fallback BM25)
      4) source coverage → top_k

    ถ้ามี query_vector อยู่แล้วจะไม่ embed ซ้ำ (ประหยัดตอน eval)
    """
    if query_vector is None:
        query_vector = embed_query(query)

    allowed_groups = None
    if apply_group_filter:
        group = patient_group if patient_group is not None else infer_patient_group_from_query(query)
        allowed_groups = filter_groups_for_query(group)

    k_per_source = max(per_source_k or PER_SOURCE_TOP_K, top_k)
    candidates = _retrieve_per_source(
        client,
        collection_name,
        query_vector,
        allowed_groups,
        k_per_source,
    )
    ranked = _rerank_candidates(query, candidates, top_k=top_k, rerank_mode=rerank_mode)
    return _select_with_source_coverage(ranked, top_k=top_k)


def search_chunks(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    ค้นหา chunks จาก Qdrant production:
      1) filter patient_group (inclusive)
      2) ดึงแยกเล่ม AAFP / URI / Dose
      3) rerank (default: LLM; fallback BM25; หรือ RERANK_MODE=vector)
      4) เลือก top_k พร้อม source coverage

    Returns list of {content, source, page, heading, distance, ...}
    """
    _init()

    # Ensure collection exists
    collections = _client.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        from qdrant_client.models import VectorParams, Distance
        _client.create_collection(
            collection_name = COLLECTION_NAME,
            vectors_config  = VectorParams(size=3072, distance=Distance.COSINE),
        )

    return retrieve_chunks(
        _client,
        COLLECTION_NAME,
        query,
        top_k=top_k,
    )


# ─── Build Context ───────────────────────────────────────────────────────────

def _best_similarity(chunks: list[dict]) -> float:
    """ค่า similarity จริงสูงสุด (vector) ในชุด chunk — ใช้ประเมินว่าคำถามอยู่ในขอบเขตหรือไม่"""
    return max((1 - c.get("distance", 0.0) for c in chunks), default=0.0)


def build_context(chunks: list[dict], weak_context: bool = False) -> str:
    """สร้าง context string จาก chunks สำหรับส่งให้ LLM
    เพิ่ม patient_group และ source_type เพื่อให้ AI ตรวจสอบ age-group alignment ได้

    หมายเหตุเรื่องเลขหน้า: ใช้ Page (เลขหน้าไฟล์ PDF) เป็นเลขอ้างอิงเดียวเสมอ
    ไม่ใส่ Journal page เข้า context เพื่อกันโมเดลอ้างเลขหน้าผิดเล่ม
    """
    if not chunks:
        return "ไม่พบข้อมูลที่เกี่ยวข้องในฐานข้อมูล"

    _SOURCE_TYPE_MAP = {
        "AAFP": "GUIDELINE",
        "URI": "GUIDELINE",
        "Dose": "DOSE_TABLE",
    }
    _GROUP_LABEL = {
        "pediatric": "เด็ก",
        "adult": "ผู้ใหญ่",
        "both": "เด็ก+ผู้ใหญ่",
        "general": "ทั่วไป",
    }

    parts = []
    for i, chunk in enumerate(chunks, 1):
        src        = chunk.get("source", "?")
        page       = chunk.get("page", "?")
        head       = chunk.get("heading", "")
        similarity = 1 - chunk.get("distance", 0)
        pgroup     = chunk.get("patient_group", "general")
        stype      = _SOURCE_TYPE_MAP.get(src, "OTHER")

        header = f"[เอกสารอ้างอิง {i}] Source: {src} | Type: {stype} | Page: {page}"
        header += f" | PatientGroup: {_GROUP_LABEL.get(pgroup, pgroup)}"
        pdf_file = chunk.get("pdf_file")
        if pdf_file:
            header += f" | PDF: {pdf_file}#page={page}"
        drug = chunk.get("drug_name")
        if drug:
            header += f" | Drug: {drug}"
        if head:
            header += f" | Section: {head}"
        header += f" | Relevance: {similarity:.2%}"

        parts.append(f"{header}\n{chunk['content']}")

    body = ("\n\n" + "=" * 60 + "\n\n").join(parts)

    if weak_context:
        note = (
            "หมายเหตุระบบ: ข้อมูลใน Context ด้านล่างมีความเกี่ยวข้องกับคำถาม \"ต่ำ\" "
            "อาจเป็นคำถามนอกขอบเขตคู่มือ (URI Guideline) หรือไม่มีข้อมูลตรงในระบบ "
            "ห้ามฝืนอ้าง [Ref: Guideline] กับเนื้อหาที่ไม่ตรงคำถาม "
            "ให้ระบุชัดว่าเป็นความรู้นอกคู่มือและแนบ URL ภายนอกแทน\n\n"
        )
        body = note + body

    return body


# ─── Source list builders (shared by streaming + non-streaming) ──────────────

import re as _re

# จับ [Ref: ... ] ทุกก้อน แล้วค่อยแยกว่าเป็นอ้างอิงภายนอกหรือไม่
_REF_BLOCK_RE = _re.compile(r'\[Ref:\s*([^\]]+)\]')
_URL_RE = _re.compile(r'\(?(https?://[^\s\)\]]+)\)?')
# คำบ่งชี้ว่าเป็นความรู้นอกคู่มือ (ไม่พึ่งวลี "อ้างอิงจาก" อย่างเดียว เพราะโมเดลใช้ไม่สม่ำเสมอ)
_EXT_MARKERS = ("นอกคู่มือ", "นอกเอกสาร", "นอกขอบเขต", "ความรู้ทั่วไป")


def _clean_external_label(text: str) -> str:
    """ตัดคำนำ/วลีซ้ำ ให้เหลือชื่อแหล่งอ้างอิงที่อ่านง่าย"""
    label = text.strip()
    for prefix in ("ความรู้นอกคู่มือ", "ความรู้ทั่วไปทางการแพทย์", "ความรู้ทั่วไป"):
        if label.startswith(prefix):
            label = label[len(prefix):]
    label = label.lstrip(" -—:").strip()
    if label.startswith("อ้างอิงจาก"):
        label = label[len("อ้างอิงจาก"):].strip()
    return label or text.strip()


def _guideline_sources(chunks: list[dict], weak_context: bool) -> tuple[list[dict], set]:
    """
    สร้างรายการแหล่งอ้างอิงจาก Guideline โดยกรองด้วย similarity จริง (vector)
      - weak_context (นอกขอบเขต): ไม่แสดง Guideline เป็นแหล่งอ้างอิง กันเข้าใจผิดว่าอ้างจากคู่มือ
      - ปกติ: แสดงเฉพาะ chunk ที่ similarity >= SOURCE_MIN_SIMILARITY
      - กันเคสกรองหมด: ถ้าไม่ weak แต่ไม่เหลือเลย ให้คง chunk ที่เกี่ยวข้องสุด 1 อัน
    """
    sources: list[dict] = []
    seen: set = set()

    if weak_context:
        return sources, seen

    for chunk in chunks:
        similarity = round(1 - chunk.get("distance", 0.0), 4)
        if similarity < SOURCE_MIN_SIMILARITY:
            continue
        key = f"{chunk['source']}_p{chunk['page']}"
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "source"         : chunk["source"],
            "page"           : chunk["page"],
            "heading"        : chunk["heading"],
            "similarity"     : similarity,
            "reference_type" : "guideline",
        })

    if not sources and chunks:
        best = max(chunks, key=lambda c: 1 - c.get("distance", 0.0))
        seen.add(f"{best['source']}_p{best['page']}")
        sources.append({
            "source"         : best["source"],
            "page"           : best["page"],
            "heading"        : best["heading"],
            "similarity"     : round(1 - best.get("distance", 0.0), 4),
            "reference_type" : "guideline",
        })

    return sources, seen


# ─── External URL reachability check ─────────────────────────────────────────

_url_verify_cache: dict[str, bool] = {}


# สถานะที่แปลว่า "หน้ายังมีอยู่" แม้เซิร์ฟเวอร์จะบล็อก bot ของเรา (เช่น CDC ตอบ 403)
# -- เบราว์เซอร์จริงของผู้ใช้ยังเปิดได้ จึงไม่ถือว่าลิงก์ตาย
_URL_ALIVE_BLOCKED = {401, 403, 405, 406, 429}
# สถานะที่แปลว่า "หน้าหายจริง" -> ถือว่าลิงก์ตาย
_URL_DEAD = {404, 410}


def verify_url_reachable(url: str, timeout: float = URL_VERIFY_TIMEOUT) -> bool:
    """
    ตรวจว่า URL ภายนอก "ยังมีอยู่จริง" หรือไม่ (กันอ้างอิงลิงก์ตาย/หน้าหาย)
    ใช้ stdlib เท่านั้น (ไม่เพิ่ม dependency) — HEAD ก่อน ถ้าไม่รองรับค่อย GET
    ผลลัพธ์ถูก cache ระหว่าง process เพื่อลด latency

    เกณฑ์ (สอดคล้องเจตนา "ต้องกดเปิดได้ ไม่ใช่หน้าหาย"):
      - 2xx/3xx                    -> เปิดได้ (True)
      - 401/403/405/406/429        -> หน้ายังมีอยู่แต่บล็อก bot (True — browser จริงเปิดได้)
      - 404/410                    -> หน้าหายจริง (False)
      - DNS/connection error       -> เข้าไม่ถึงเลย (False)
      - 5xx                        -> เซิร์ฟเวอร์มีอยู่แต่ error ชั่วคราว (True — โดเมนยัง live)
    """
    if not url:
        return False
    if url in _url_verify_cache:
        return _url_verify_cache[url]

    import urllib.request
    import urllib.error

    headers = {"User-Agent": "Mozilla/5.0 (PharmaCare-AI reference checker)"}

    def _classify_http(code: int) -> bool:
        if 200 <= code < 400:
            return True
        if code in _URL_DEAD:
            return False
        if code in _URL_ALIVE_BLOCKED:
            return True
        if code >= 500:            # เซิร์ฟเวอร์ยังมีอยู่ (error ชั่วคราว)
            return True
        return False               # 4xx อื่นๆ ที่ไม่ชัด -> ถือว่าไม่ปลอดภัยพอ

    def _try(method: str):
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _classify_http(resp.status)
        except urllib.error.HTTPError as e:
            return _classify_http(e.code)
        except urllib.error.URLError:
            return False           # DNS/connection/timeout -> เข้าไม่ถึงจริง
        except Exception:
            return None            # เหตุอื่น -> ลอง method ถัดไป

    ok = _try("HEAD")
    if ok is not True:             # HEAD ไม่ยืนยันว่า alive -> ลอง GET ให้แน่ใจ
        got = _try("GET")
        if got is not None:
            ok = got
    ok = bool(ok)
    _url_verify_cache[url] = ok
    return ok


def _append_external_refs(sources: list[dict], seen: set, answer: str) -> None:
    """
    แยกอ้างอิงภายนอกจากข้อความคำตอบแล้วเติมเข้า sources
    ถือว่าเป็น external เมื่อ [Ref: ...] มี URL หรือมีคำบ่งชี้ว่าเป็นความรู้นอกคู่มือ
    (ไม่ผูกกับวลี "อ้างอิงจาก" เดียว — โมเดลเขียนไม่สม่ำเสมอ)

    ถ้า VERIFY_EXTERNAL_URLS เปิดอยู่: ตรวจว่า URL เปิดได้จริง — ถ้าลิงก์ตาย จะถอด URL ออก
    (คงชื่อแหล่งไว้) และตั้ง url_status="unreachable" กันผู้ใช้กดแล้วเจอหน้า Not Found
    """
    for inner in _REF_BLOCK_RE.findall(answer or ""):
        url_match = _URL_RE.search(inner)
        is_external = bool(url_match) or any(m in inner for m in _EXT_MARKERS)
        if not is_external:
            continue  # เป็น [Ref: Guideline, หน้า X] — มี guideline source อยู่แล้ว
        url = url_match.group(1) if url_match else None
        label = _clean_external_label(inner)
        dedup_key = url or label
        if not dedup_key or dedup_key in seen:
            continue
        seen.add(dedup_key)

        url_status = "unknown"
        if url and VERIFY_EXTERNAL_URLS:
            if verify_url_reachable(url):
                url_status = "verified"
            else:
                url_status = "unreachable"
                url = None  # ถอดลิงก์ตายออก คงไว้แค่ชื่อแหล่งให้ผู้ใช้ไปค้นเอง

        sources.append({
            "type"           : "external",
            "reference_type" : "external",
            "source"         : label,
            "url"            : url,
            "url_status"     : url_status,
            "page"           : None,
            "heading"        : "ความรู้นอกเอกสาร",
            "similarity"     : 1.0,
        })


# ─── Generate Answer (non-streaming) ─────────────────────────────────────────

def generate_answer(
    question : str,
    history  : list[dict] = None,
    top_k    : int = TOP_K,
) -> dict:
    """
    RAG pipeline หลัก:
    1. Search relevant chunks
    2. Build context
    3. Generate answer with Gemini

    Returns: {answer, sources, chunks_used}
    """
    _init()

    chunks       = search_chunks(question, top_k=top_k)
    weak_context = _best_similarity(chunks) < SIMILARITY_THRESHOLD
    context      = build_context(chunks, weak_context=weak_context)

    # Build conversation history
    gemini_history = []
    if history:
        for msg in history[-MAX_HISTORY:]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

    user_message = USER_MESSAGE_TEMPLATE.format(
        question = question,
        context  = context,
    )

    try:
        chat     = _chat_model.start_chat(history=gemini_history)
        response = chat.send_message(user_message)
        answer   = response.text
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "Quota" in err_str:
            answer = "[ระบบ] โควต้าการใช้งาน API เต็มชั่วคราว (Rate Limit) กรุณารอสักครู่ (ประมาณ 10-15 วินาที) แล้วลองใหม่อีกครั้ง"
        else:
            answer = f"[ระบบ] เกิดข้อผิดพลาดในการสร้างคำตอบ: {err_str}"

    sources, seen = _guideline_sources(chunks, weak_context)
    _append_external_refs(sources, seen, answer)

    return {
        "answer"      : answer,
        "sources"     : sources,
        "chunks_used" : len(chunks),
    }


# ─── Generate Answer (streaming) ─────────────────────────────────────────────

async def generate_answer_stream(
    question : str,
    history  : list[dict] = None,
    top_k    : int = TOP_K,
):
    """
    RAG pipeline แบบ Streaming:
    Yields JSON strings (Server-Sent Events payload).
    """
    _init()

    chunks       = search_chunks(question, top_k=top_k)
    weak_context = _best_similarity(chunks) < SIMILARITY_THRESHOLD
    context      = build_context(chunks, weak_context=weak_context)

    # Build conversation history
    gemini_history = []
    if history:
        for msg in history[-MAX_HISTORY:]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

    user_message = USER_MESSAGE_TEMPLATE.format(
        question = question,
        context  = context,
    )

    # แหล่งอ้างอิงจาก Guideline (กรองด้วย similarity จริง) — external refs เติมหลังได้คำตอบ
    sources, seen = _guideline_sources(chunks, weak_context)

    try:
        chat     = _chat_model.start_chat(history=gemini_history)
        response = chat.send_message(user_message, stream=True)

        full_answer = ""
        prompt_tokens = 0
        completion_tokens = 0
        
        for chunk in response:
            if chunk.text:
                full_answer += chunk.text
                yield json.dumps({"type": "chunk", "content": chunk.text}) + "\n"
            
            # Try to extract usage from chunk
            if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                prompt_tokens = chunk.usage_metadata.prompt_token_count
                completion_tokens = chunk.usage_metadata.candidates_token_count

        # Extract usage from response if not found in chunks
        if prompt_tokens == 0 and hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count
            completion_tokens = response.usage_metadata.candidates_token_count

        # \u0e40\u0e15\u0e34\u0e21\u0e2d\u0e49\u0e32\u0e07\u0e2d\u0e34\u0e07\u0e20\u0e32\u0e22\u0e19\u0e2d\u0e01 (URL) \u0e08\u0e32\u0e01\u0e02\u0e49\u0e2d\u0e04\u0e27\u0e32\u0e21\u0e04\u0e33\u0e15\u0e2d\u0e1a
        _append_external_refs(sources, seen, full_answer)

        yield json.dumps({
            "type"        : "done",
            "sources"     : sources,
            "chunks_used" : len(chunks),
            "full_answer" : full_answer,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens
            }
        }) + "\n"

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "Quota" in err_str:
            err_msg = "[ระบบ] โควต้าการใช้งาน API เต็มชั่วคราว (Rate Limit) กรุณารอสักครู่ (ประมาณ 10-15 วินาที) แล้วลองใหม่อีกครั้ง"
        else:
            err_msg = f"[ระบบ] เกิดข้อผิดพลาดในการสร้างคำตอบ: {err_str}"
        yield json.dumps({"type": "error", "content": err_msg}) + "\n"


# ─── Cosine Similarity ────────────────────────────────────────────────────────

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """คำนวณ cosine similarity ระหว่าง 2 vectors"""
    a    = np.array(vec_a)
    b    = np.array(vec_b)
    dot  = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(dot / norm) if norm != 0 else 0.0


# ─── Embedding-based Evaluation ──────────────────────────────────────────────

def evaluate_answer(prediction: str, expectation: str) -> float:
    """
    ประเมินความถูกต้องด้วย cosine similarity ของ embeddings
    """
    _init()
    try:
        vec_pred = embed_query(prediction)
        vec_exp  = embed_query(expectation)
        return cosine_similarity(vec_pred, vec_exp)
    except Exception as e:
        print(f"[ERROR] evaluate_answer: {e}")
        return 0.0


# ─── LLM-based Evaluation ────────────────────────────────────────────────────

def evaluate_answer_llm(prediction: str, expectation: str) -> dict:
    """
    ประเมินความถูกต้องของคำตอบด้วย LLM (คะแนน 1–5 พร้อมเหตุผล)
    """
    _init()
    prompt = f"""คุณเป็นเภสัชกรผู้เชี่ยวชาญที่ทำหน้าที่ประเมินคุณภาพคำตอบของ AI Chatbot
เปรียบเทียบ "คำตอบของ AI (Prediction)" กับ "คำตอบที่คาดหวัง (Expectation)" แล้วให้คะแนน 1–5:

5 = ถูกต้องสมบูรณ์ ใจความหลักครบถ้วน (อาจใช้คำต่างกันได้)
4 = ถูกต้องเป็นส่วนใหญ่ ขาดรายละเอียดเล็กน้อยแต่ไม่กระทบการรักษา
3 = ถูกต้องปานกลาง มีข้อมูลบางส่วนตกหล่นหรือคลาดเคลื่อนเล็กน้อย
2 = ไม่ถูกต้องบางส่วน มีข้อผิดพลาดที่อาจส่งผลต่อความเข้าใจ
1 = ผิดพลาดโดยสิ้นเชิง ขัดแย้งกับ Expectation หรืออันตราย

คำตอบที่คาดหวัง (Expectation):
{expectation}

คำตอบของ AI (Prediction):
{prediction}

ส่งผลลัพธ์เป็น JSON เท่านั้น:
{{"score": <1–5>, "reasoning": "<เหตุผลสั้นๆ>"}}
"""
    try:
        model    = genai.GenerativeModel(model_name=CHAT_MODEL)
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        result = json.loads(response.text)
        return {
            "score"     : result.get("score", 0),
            "reasoning" : result.get("reasoning", "No reasoning provided"),
        }
    except Exception as e:
        print(f"[ERROR] evaluate_answer_llm: {e}")
        return {"score": 0, "reasoning": str(e)}


# ─── Summarize History ────────────────────────────────────────────────────────

def summarize_history(messages: list[dict]) -> str:
    """
    สรุปข้อความแชทเก่าๆ เพื่อนำไปใช้เป็น context ระยะสั้น
    """
    _init()
    
    if not messages:
        return ""
        
    chat_text = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in messages])
    
    prompt = f"""กรุณาสรุปประวัติการสนทนาต่อไปนี้อย่างกระชับที่สุด 
เน้นเก็บข้อมูลสำคัญทางการแพทย์ อาการผู้ป่วย และคำแนะนำที่ให้ไปแล้ว (ไม่เกิน 150 คำ):

{chat_text}

สรุป:"""

    try:
        model = genai.GenerativeModel(model_name=CHAT_MODEL)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"[ERROR] summarize_history: {e}")
        return "ไม่สามารถสรุปประวัติเก่าได้"


# ─── Quick Test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    question = "เด็กอายุ 3 ขวบ เป็นหวัด มีน้ำมูกใส ไอเล็กน้อย ไข้ 37.8 ควรให้ยาอะไร?"
    print(f"\n[Q] {question}\n")
    result = generate_answer(question)
    print(f"[A] {result['answer']}\n")
    print(f"[Sources] {result['sources']}")