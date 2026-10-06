"""
Auth Module — ระบบยืนยันตัวตนด้วย JWT + bcrypt
ผู้ใช้เก็บใน data/users.json (เพิ่ม/แก้ไขได้ง่าย)
"""

import json
import hashlib
import hmac
import os
import time
import base64
import re
import threading

from backend.config import USERS_FILE, JWT_SECRET, TOKEN_EXPIRE_HOURS

# ─── Config ──────────────────────────────────────────────────────────────────

SECRET_KEY = JWT_SECRET


# ─── Password Hashing (PBKDF2-HMAC-SHA256, stdlib) ───────────────────────────
# รูปแบบใหม่: pbkdf2_sha256$<iterations>$<salt>$<hash>
# รูปแบบเก่า (SHA-256 รอบเดียว "salt:hash") ยังตรวจผ่านได้ และถูกอัปเกรดตอน login สำเร็จ

PBKDF2_ITERATIONS = 600_000
_PBKDF2_PREFIX = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
    return f"{_PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith(_PBKDF2_PREFIX + "$"):
        try:
            _, iters, salt, digest = stored_hash.split("$", 3)
            check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters)).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(check, digest)

    if ":" not in stored_hash:
        return False
    salt, hashed = stored_hash.split(":", 1)
    check = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return hmac.compare_digest(check, hashed)


def _needs_rehash(stored_hash: str) -> bool:
    if not stored_hash.startswith(_PBKDF2_PREFIX + "$"):
        return True
    try:
        return int(stored_hash.split("$", 3)[1]) < PBKDF2_ITERATIONS
    except (ValueError, IndexError):
        return True


# hash หลอกไว้ตรวจเมื่อไม่มี username นั้น → เวลาตอบเท่ากัน ไม่เปิดช่องเดา username จากความเร็ว
_DUMMY_HASH = hash_password("dummy-password-for-timing")


# ─── JWT (simple implementation, no pyjwt dependency) ────────────────────────

def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_token(username: str) -> str:
    """สร้าง JWT token"""
    header = _b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_data = {
        "sub": username,
        "exp": int(time.time()) + TOKEN_EXPIRE_HOURS * 3600,
        "iat": int(time.time()),
    }
    payload = _b64_encode(json.dumps(payload_data).encode())
    
    signing_input = f"{header}.{payload}"
    signature = hmac.new(
        SECRET_KEY.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    sig = _b64_encode(signature)
    
    return f"{header}.{payload}.{sig}"


def verify_token(token: str) -> str | None:
    """ตรวจ JWT token → return username หรือ None"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        
        header, payload, sig = parts
        
        # Verify signature
        signing_input = f"{header}.{payload}"
        expected_sig = hmac.new(
            SECRET_KEY.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        
        if not hmac.compare_digest(_b64_decode(sig), expected_sig):
            return None
        
        # Decode payload
        payload_data = json.loads(_b64_decode(payload))
        
        # Check expiration
        if payload_data.get("exp", 0) < time.time():
            return None
        
        username = payload_data.get("sub")
        return username if username and is_user_active(username) else None
    except Exception:
        return None


# ─── User Management ────────────────────────────────────────────────────────

def _load_users() -> list[dict]:
    """โหลด users จาก JSON file"""
    if not USERS_FILE.exists():
        return []
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_users(users: list[dict]):
    """บันทึก users ลง JSON file"""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


_users_lock = threading.Lock()
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LEN = 8


def is_user_active(username: str) -> bool:
    for u in _load_users():
        if u["username"] == username:
            return not u.get("disabled", False)
    return False


def verify_credentials(username: str, password: str) -> bool:
    """ตรวจ username + password (และอัปเกรด hash เก่าเป็น PBKDF2 เมื่อผ่าน)"""
    users = _load_users()
    for user in users:
        if user["username"] == username:
            if not verify_password(password, user["password_hash"]):
                return False
            if user.get("disabled", False):
                return False
            if _needs_rehash(user["password_hash"]):
                user["password_hash"] = hash_password(password)
                _save_users(users)
            return True
    verify_password(password, _DUMMY_HASH)
    return False


def list_users() -> list[dict]:
    """ข้อมูลผู้ใช้สำหรับหน้า admin — ไม่รวม password hash"""
    return [
        {
            "username": u["username"],
            "display_name": u.get("display_name", u["username"]),
            "role": u.get("role", "user"),
            "department": u.get("department", ""),
            "disabled": bool(u.get("disabled", False)),
        }
        for u in _load_users()
    ]


DEPARTMENTS = (
    "แผนกผู้ป่วยนอก",
    "แผนกผู้ป่วยใน",
    "ห้องจ่ายยา",
    "แผนกฉุกเฉิน",
    "แผนกกุมารเวชกรรม",
)


def public_profile(username: str) -> dict | None:
    for u in _load_users():
        if u["username"] == username:
            return {
                "username": u["username"],
                "display_name": u.get("display_name", u["username"]),
                "role": u.get("role", "user"),
                "department": u.get("department", ""),
                "disabled": bool(u.get("disabled", False)),
            }
    return None


def _active_admin_count(users: list[dict]) -> int:
    return sum(1 for u in users if u.get("role") == "admin" and not u.get("disabled", False))


def _check_password_strength(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD_LEN} ตัวอักษร")


def create_user(username: str, password: str, display_name: str | None, role: str = "user") -> None:
    if not USERNAME_RE.match(username or ""):
        raise ValueError("ชื่อผู้ใช้ต้องเป็น a-z, 0-9, _ . - ยาว 3-32 ตัว")
    if role not in ("user", "admin"):
        raise ValueError("บทบาทไม่ถูกต้อง")
    _check_password_strength(password)
    with _users_lock:
        users = _load_users()
        if any(u["username"].lower() == username.lower() for u in users):
            raise ValueError("ชื่อผู้ใช้นี้มีอยู่แล้ว")
        users.append({
            "username": username,
            "password_hash": hash_password(password),
            "display_name": (display_name or username).strip() or username,
            "role": role,
        })
        _save_users(users)


def _mutate_user(username: str, fn) -> None:
    with _users_lock:
        users = _load_users()
        for u in users:
            if u["username"] == username:
                fn(u, users)
                _save_users(users)
                return
        raise ValueError("ไม่พบผู้ใช้นี้")


def set_user_password(username: str, new_password: str) -> None:
    _check_password_strength(new_password)
    _mutate_user(username, lambda u, _all: u.__setitem__("password_hash", hash_password(new_password)))


def set_user_role(username: str, role: str) -> None:
    if role not in ("user", "admin"):
        raise ValueError("บทบาทไม่ถูกต้อง")

    def fn(u, users):
        if u.get("role") == "admin" and role != "admin" and _active_admin_count(users) <= 1 and not u.get("disabled"):
            raise ValueError("ต้องมี admin ที่ใช้งานได้อย่างน้อย 1 คน")
        u["role"] = role
    _mutate_user(username, fn)


def set_user_disabled(username: str, disabled: bool) -> None:
    def fn(u, users):
        if disabled and u.get("role") == "admin" and not u.get("disabled") and _active_admin_count(users) <= 1:
            raise ValueError("ต้องมี admin ที่ใช้งานได้อย่างน้อย 1 คน")
        u["disabled"] = bool(disabled)
    _mutate_user(username, fn)


def update_profile(username: str, display_name: str | None = None, department: str | None = None) -> None:
    def fn(u, _all):
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise ValueError("ชื่อที่แสดงต้องไม่ว่าง")
            u["display_name"] = name
        if department is not None:
            if department not in DEPARTMENTS:
                raise ValueError("ไม่มีแผนกนี้")
            u["department"] = department
    _mutate_user(username, fn)


def set_user_display_name(username: str, display_name: str) -> None:
    name = (display_name or "").strip()
    if not name:
        raise ValueError("ชื่อที่แสดงต้องไม่ว่าง")
    _mutate_user(username, lambda u, _all: u.__setitem__("display_name", name))


def get_user_role(username: str) -> str:
    for user in _load_users():
        if user["username"] == username:
            return user.get("role", "user")
    return "user"


def get_user_display_name(username: str) -> str:
    """ดึงชื่อแสดงผลของผู้ใช้"""
    users = _load_users()
    for user in users:
        if user["username"] == username:
            return user.get("display_name", username)
    return username


def init_default_users():
    """สร้างไฟล์ users.json พร้อม admin user ถ้ายังไม่มี"""
    if USERS_FILE.exists():
        return
    
    initial_password = os.getenv("ADMIN_INITIAL_PASSWORD") or base64.urlsafe_b64encode(os.urandom(12)).decode()
    default_users = [
        {
            "username": "admin",
            "password_hash": hash_password(initial_password),
            "display_name": "ผู้ดูแลระบบ",
            "role": "admin"
        }
    ]
    _save_users(default_users)
    print(f"[AUTH] Created default users file: {USERS_FILE}")
    if not os.getenv("ADMIN_INITIAL_PASSWORD"):
        print(f"[AUTH] Initial admin password (shown once, change it): {initial_password}")


# ─── Add User Helper (สำหรับเพิ่มผู้ใช้ใหม่) ────────────────────────────────

def add_user(username: str, password: str, display_name: str = None, role: str = "user"):
    """
    เพิ่มผู้ใช้ใหม่ — เรียกจาก command line ได้:
        python -c "from backend.auth import add_user; add_user('pharmacist1', 'mypass', 'ภญ.สมศรี')"
    """
    users = _load_users()
    
    # Check duplicate
    if any(u["username"] == username for u in users):
        print(f"[AUTH] ❌ Username '{username}' มีอยู่แล้ว")
        return False
    
    users.append({
        "username": username,
        "password_hash": hash_password(password),
        "display_name": display_name or username,
        "role": role,
    })
    _save_users(users)
    print(f"[AUTH] ✅ เพิ่มผู้ใช้ '{username}' สำเร็จ")
    return True


# Auto-init on import
init_default_users()
