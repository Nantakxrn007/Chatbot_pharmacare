"""
Rate limiting แบบ in-memory (sliding window) — ไม่ต้องพึ่ง dependency เพิ่ม

ข้อจำกัด: เก็บในหน่วยความจำของ process เดียว (รีสตาร์ท = รีเซ็ต, หลาย worker = นับแยกกัน)
เพียงพอสำหรับ uvicorn 1 worker ตามที่รันอยู่ ถ้าขยายเป็นหลาย worker ให้ย้ายไป Redis
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Request

from backend.config import (
    LOGIN_MAX_FAILS,
    LOGIN_FAIL_WINDOW_SEC,
    LOGIN_LOCKOUT_SEC,
    TRUST_PROXY_HEADERS,
)


def get_client_ip(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        # proxy ของเราต่อท้าย IP จริงไว้ขวาสุด; ค่าทางซ้ายปลอมโดย client ได้
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_gc = time.monotonic()

    def hit(self, key: str, limit: int, window_sec: int) -> tuple[bool, int]:
        """นับ 1 ครั้ง → (อนุญาตไหม, retry_after วินาที)"""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            cutoff = now - window_sec
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry = int(dq[0] + window_sec - now) + 1
                return False, max(retry, 1)
            dq.append(now)
            self._gc(now, window_sec)
            return True, 0

    def _gc(self, now: float, window_sec: int) -> None:
        if now - self._last_gc < 60:
            return
        self._last_gc = now
        stale = [k for k, dq in self._hits.items() if not dq or dq[-1] <= now - max(window_sec, 3600)]
        for k in stale:
            self._hits.pop(k, None)


class LoginGuard:
    """
    กัน brute-force ที่หน้า login
    - นับครั้งที่ผิดแยก (IP, username) → ล็อกคู่นั้น
    - นับรวมต่อ IP (ลองหลาย username) → ล็อก IP นั้น
    ตรวจก่อนเช็ครหัสผ่านเสมอ: ระหว่างล็อก รหัสที่ถูกก็เข้าไม่ได้ (ไม่ให้เดาต่อได้)
    """

    IP_FAIL_MULTIPLIER = 4

    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(ip: str, username: str) -> tuple[str, str]:
        return f"pair:{ip}:{(username or '').lower()}", f"ip:{ip}"

    def check(self, ip: str, username: str) -> int:
        """คืน 0 ถ้าลองได้; ถ้าถูกล็อกคืนวินาทีที่ต้องรอ"""
        now = time.monotonic()
        with self._lock:
            wait = 0
            for key in self._keys(ip, username):
                until = self._locked_until.get(key, 0)
                if until > now:
                    wait = max(wait, int(until - now) + 1)
                elif key in self._locked_until:
                    del self._locked_until[key]
            return wait

    def record_failure(self, ip: str, username: str) -> int:
        """บันทึกการ login ผิด → คืนวินาทีที่ถูกล็อก (0 = ยังไม่ล็อก)"""
        now = time.monotonic()
        pair_key, ip_key = self._keys(ip, username)
        locked = 0
        with self._lock:
            for key, limit in ((pair_key, LOGIN_MAX_FAILS), (ip_key, LOGIN_MAX_FAILS * self.IP_FAIL_MULTIPLIER)):
                dq = self._fails.setdefault(key, deque())
                cutoff = now - LOGIN_FAIL_WINDOW_SEC
                while dq and dq[0] <= cutoff:
                    dq.popleft()
                dq.append(now)
                if len(dq) >= limit:
                    self._locked_until[key] = now + LOGIN_LOCKOUT_SEC
                    dq.clear()
                    locked = LOGIN_LOCKOUT_SEC
            return locked

    def locked_entries(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            return [
                {"key": k.split(":", 1)[0], "target": k.split(":", 1)[1], "retry_after": int(v - now) + 1}
                for k, v in self._locked_until.items() if v > now
            ]

    def record_success(self, ip: str, username: str) -> None:
        pair_key, _ = self._keys(ip, username)
        with self._lock:
            self._fails.pop(pair_key, None)
            self._locked_until.pop(pair_key, None)


api_limiter = SlidingWindowLimiter()
login_guard = LoginGuard()
