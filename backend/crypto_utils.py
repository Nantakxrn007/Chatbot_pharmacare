"""
Field-level encryption สำหรับข้อมูลที่ระบุตัวผู้ป่วยได้ (ชื่อผู้ป่วย / เนื้อหาแชท)

- เข้ารหัสด้วย Fernet (AES-128-CBC + HMAC-SHA256) คีย์จาก DATA_ENCRYPTION_KEY
- ค่าที่เข้ารหัสแล้วขึ้นต้นด้วย "enc1:" → แยกออกจากข้อมูลเก่าที่ยังเป็น plaintext ได้
  (decrypt() ของ plaintext คืนค่าเดิม จึง migrate ทีละแถวได้โดยไม่พัง)
- ค้นหาชื่อผู้ป่วยใช้ blind index = HMAC-SHA256(BLIND_INDEX_KEY, ชื่อ) แทนการเก็บชื่อจริงไว้ค้นหา

ถ้าทำคีย์ DATA_ENCRYPTION_KEY หาย ข้อมูลที่เข้ารหัสแล้วจะกู้คืนไม่ได้ — สำรองไว้นอกเครื่อง
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import unicodedata

from cryptography.fernet import Fernet, InvalidToken

from backend.config import DATA_ENCRYPTION_KEY, BLIND_INDEX_KEY

logger = logging.getLogger("pharmacare.crypto")

PREFIX = "enc1:"
UNREADABLE = "[ข้อมูลถูกเข้ารหัส ถอดรหัสไม่ได้ — ตรวจสอบ DATA_ENCRYPTION_KEY]"

_fernet: Fernet | None = None


def require_keys() -> None:
    """เรียกตอนเริ่มระบบ — ไม่มีคีย์ให้หยุดทันที ดีกว่าเขียนข้อมูลผู้ป่วยเป็น plaintext เงียบๆ"""
    if not DATA_ENCRYPTION_KEY or not BLIND_INDEX_KEY:
        raise RuntimeError(
            "ไม่พบ DATA_ENCRYPTION_KEY / BLIND_INDEX_KEY ใน .env — "
            "ระบบไม่เริ่มทำงานเพื่อป้องกันข้อมูลผู้ป่วยถูกเก็บแบบไม่เข้ารหัส"
        )
    _get_fernet()


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not DATA_ENCRYPTION_KEY:
            raise RuntimeError("ไม่พบ DATA_ENCRYPTION_KEY ใน .env")
        _fernet = Fernet(DATA_ENCRYPTION_KEY.encode())
    return _fernet


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(text: str | None) -> str | None:
    if text is None or text == "":
        return text
    if is_encrypted(text):
        return text
    token = _get_fernet().encrypt(text.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(value: str | None) -> str | None:
    if value is None or not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("decrypt failed: wrong DATA_ENCRYPTION_KEY or corrupted data")
        return UNREADABLE


def encrypt_json(obj) -> str:
    return encrypt(json.dumps(obj, ensure_ascii=False))


def decrypt_json(value: str | None):
    plain = decrypt(value)
    if plain is None or plain == UNREADABLE:
        return None
    return json.loads(plain)


def blind_index(text: str) -> str:
    """ค่า deterministic สำหรับ WHERE patient_key = ? — ย้อนกลับเป็นชื่อไม่ได้ถ้าไม่มี BLIND_INDEX_KEY"""
    if not BLIND_INDEX_KEY:
        raise RuntimeError("ไม่พบ BLIND_INDEX_KEY ใน .env")
    normalized = unicodedata.normalize("NFC", (text or "").strip())
    return hmac.new(BLIND_INDEX_KEY.encode(), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def short_ref(text: str) -> str:
    """ตัวอ้างอิงสั้นๆ สำหรับ audit log — ไม่ให้ชื่อจริงรั่วลง log"""
    return blind_index(text)[:10]
