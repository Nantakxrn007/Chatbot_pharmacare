"""
Patient Summary — สร้าง AI Summary ของผู้ป่วยจาก Chat History
ใช้ Gemini สรุปข้อมูล พร้อม Risk Assessment
Summary ถูก cache ลง SQLite (ไม่เรียก LLM ทุกครั้ง)
"""

import json
import google.generativeai as genai
from backend.rag_engine import CHAT_MODEL, _init


SUMMARY_PROMPT = """คุณเป็นเภสัชกรผู้เชี่ยวชาญ ให้สรุปประวัติผู้ป่วยจากบทสนทนากับเภสัชกรด้านล่าง

ชื่อผู้ป่วย: {patient_name}

ประวัติการสนทนาทั้งหมด:
{chat_history}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
กรุณาสรุปเป็น JSON format ดังนี้ (ตอบเป็น JSON เท่านั้น ห้ามมี text อื่น):
{{
    "overall_summary": "สรุปภาพรวมของผู้ป่วย 2-4 ประโยค",
    "conditions": ["รายการโรค/อาการที่ตรวจพบ"],
    "medications_given": ["รายการยาที่แนะนำ/จ่ายไปแล้ว พร้อมขนาด"],
    "allergies": ["ประวัติแพ้ยา ถ้ามี ไม่มีให้ใส่ []"],
    "timeline": [
        {{
            "date": "วันที่ (format: YYYY-MM-DD หรือ ข้อความ ถ้าไม่มีวันที่ชัดเจน)",
            "summary": "สรุปสั้นๆ ของการปรึกษาครั้งนั้น"
        }}
    ],
    "risk_assessment": {{
        "level": "low/medium/high/critical",
        "description": "อธิบายระดับความเสี่ยงพร้อมเหตุผล 1-2 ประโยค",
        "factors": ["ปัจจัยเสี่ยงที่พบ"]
    }},
    "recommendations": ["คำแนะนำสำหรับการติดตามต่อ"],
    "data_sufficient": true
}}

หมายเหตุ:
- ถ้าข้อมูลจากแชทไม่เพียงพอที่จะสรุปครบทุกฟิลด์ ให้ "data_sufficient": false และใส่เฉพาะข้อมูลที่มี
- risk_assessment.level ใช้ 4 ระดับ:
  - "low": อาการเล็กน้อย ไม่ต้องติดตามเร่งด่วน
  - "medium": ต้องติดตามผล อาจมีข้อควรระวัง
  - "high": มีความเสี่ยงสูง ต้องติดตามใกล้ชิด
  - "critical": ต้องพบแพทย์/ส่งต่อโรงพยาบาลทันที
- timeline ให้เรียงตามลำดับเวลา
- ถ้า chat มีข้อมูลน้อยมาก (เช่น แค่ทักทาย) ให้ data_sufficient = false"""


def generate_patient_summary(patient_name: str, messages: list[dict]) -> dict:
    """
    ใช้ Gemini สรุปประวัติผู้ป่วยจาก chat history ทั้งหมด
    
    Args:
        patient_name: ชื่อผู้ป่วย
        messages: list of {role, content, timestamp, session_title, session_date}
    
    Returns:
        dict with summary data
    """
    _init()

    if not messages:
        return {
            "overall_summary": "ยังไม่มีประวัติการสนทนา",
            "conditions": [],
            "medications_given": [],
            "allergies": [],
            "timeline": [],
            "risk_assessment": {
                "level": "low",
                "description": "ยังไม่มีข้อมูลเพียงพอในการประเมินความเสี่ยง",
                "factors": []
            },
            "recommendations": ["เริ่มสนทนาเพื่อเก็บประวัติผู้ป่วย"],
            "data_sufficient": False,
        }

    # Build chat history text
    chat_parts = []
    current_session = None
    for msg in messages:
        session_title = msg.get("session_title", "")
        if session_title != current_session:
            current_session = session_title
            session_date = msg.get("session_date", "ไม่ทราบ")
            chat_parts.append(f"\n--- Session: {session_title} (เริ่ม: {session_date}) ---")
        
        role_label = "เภสัชกร" if msg["role"] == "user" else "AI"
        timestamp = msg.get("timestamp", "")
        chat_parts.append(f"[{timestamp}] {role_label}: {msg['content']}")

    chat_history = "\n".join(chat_parts)

    # Truncate if too long (keep last 8000 chars to stay within context)
    if len(chat_history) > 8000:
        chat_history = "...(ตัดส่วนต้นออกเพื่อความกระชับ)...\n" + chat_history[-8000:]

    prompt = SUMMARY_PROMPT.format(
        patient_name=patient_name,
        chat_history=chat_history,
    )

    try:
        model = genai.GenerativeModel(model_name=CHAT_MODEL)
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        result = json.loads(response.text)
        
        # Ensure all required fields exist
        result.setdefault("overall_summary", "ไม่สามารถสรุปได้")
        result.setdefault("conditions", [])
        result.setdefault("medications_given", [])
        result.setdefault("allergies", [])
        result.setdefault("timeline", [])
        result.setdefault("risk_assessment", {
            "level": "low",
            "description": "ไม่สามารถประเมินได้",
            "factors": []
        })
        result.setdefault("recommendations", [])
        result.setdefault("data_sufficient", True)
        
        return result
        
    except Exception as e:
        print(f"[PatientSummary] Error generating summary: {e}")
        return {
            "overall_summary": f"เกิดข้อผิดพลาดในการสรุป: {str(e)}",
            "conditions": [],
            "medications_given": [],
            "allergies": [],
            "timeline": [],
            "risk_assessment": {
                "level": "low",
                "description": "ไม่สามารถประเมินได้เนื่องจากเกิดข้อผิดพลาด",
                "factors": []
            },
            "recommendations": [],
            "data_sufficient": False,
        }
