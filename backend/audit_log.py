"""
Audit log — บันทึกว่า ใคร ทำอะไร กับอะไร เมื่อไหร่ จาก IP ไหน

หลักการ
- append-only: trigger ใน SQLite บล็อก UPDATE / DELETE
- hash chain: แต่ละแถวผูกกับแถวก่อนหน้าด้วย HMAC → แก้/ลบกลางทางแล้วตรวจพบด้วย verify_chain()
- ไม่เก็บ PII: ไม่บันทึกชื่อผู้ป่วยหรือเนื้อหาแชท เก็บแค่ตัวอ้างอิง (patient:<hash สั้น>, session id, ความยาวข้อความ)

หมายเหตุ: คนที่ลบไฟล์ audit_log.db ทั้งไฟล์ได้ก็ลบประวัติได้ — production ควรส่ง log ออกไปเก็บนอกเครื่องด้วย
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from backend.config import AUDIT_DB, BLIND_INDEX_KEY

_GENESIS = "0" * 64


class AuditLog:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or AUDIT_DB)
        self._lock = threading.Lock()
        self._key = (BLIND_INDEX_KEY or "").encode()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    username TEXT,
                    ip TEXT,
                    action TEXT NOT NULL,
                    resource TEXT,
                    status TEXT,
                    detail TEXT,
                    prev_hash TEXT NOT NULL,
                    hash TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit(username)")
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END
                """
            )
            conn.commit()

    def _digest(self, prev: str, ts: str, username: str, ip: str, action: str,
                resource: str, status: str, detail: str) -> str:
        msg = "\x1f".join([prev, ts, username, ip, action, resource, status, detail])
        return hmac.new(self._key, msg.encode("utf-8"), hashlib.sha256).hexdigest()

    def log(self, action: str, username: str = "", ip: str = "", resource: str = "",
            status: str = "ok", detail: str = "") -> None:
        ts = datetime.now(timezone.utc).isoformat()
        username, ip, resource, status, detail = (username or "", ip or "", resource or "", status or "", detail or "")
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else _GENESIS
            digest = self._digest(prev, ts, username, ip, action, resource, status, detail)
            conn.execute(
                "INSERT INTO audit (ts, username, ip, action, resource, status, detail, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, username, ip, action, resource, status, detail, prev, digest),
            )
            conn.commit()

    def query(self, limit: int = 100, username: str | None = None, action: str | None = None,
              since: str | None = None, status: str | None = None) -> list[dict]:
        sql = "SELECT id, ts, username, ip, action, resource, status, detail FROM audit WHERE 1=1"
        args: list = []
        if username:
            sql += " AND username = ?"
            args.append(username)
        if action:
            sql += " AND action LIKE ?"
            args.append(f"{action}%")
        if status:
            sql += " AND status = ?"
            args.append(status)
        if since:
            sql += " AND ts >= ?"
            args.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def summary(self, hours: int = 24) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._conn() as conn:
            def one(sql, *a):
                return conn.execute(sql, a).fetchone()[0]
            total = one("SELECT COUNT(*) FROM audit WHERE ts >= ?", since)
            logins_ok = one("SELECT COUNT(*) FROM audit WHERE ts >= ? AND action='login' AND status='ok'", since)
            logins_failed = one("SELECT COUNT(*) FROM audit WHERE ts >= ? AND action='login' AND status IN ('failed','locked')", since)
            rate_limited = one("SELECT COUNT(*) FROM audit WHERE ts >= ? AND action='rate_limited'", since)
            denied = one("SELECT COUNT(*) FROM audit WHERE ts >= ? AND status='denied'", since)
            top_fail_ips = [dict(r) for r in conn.execute(
                "SELECT ip, COUNT(*) AS n FROM audit WHERE ts >= ? AND action='login' AND status IN ('failed','locked') "
                "GROUP BY ip ORDER BY n DESC LIMIT 5", (since,))]
            by_user = [dict(r) for r in conn.execute(
                "SELECT username, COUNT(*) AS n, MAX(ts) AS last_ts FROM audit WHERE ts >= ? AND username != '' "
                "GROUP BY username ORDER BY n DESC LIMIT 20", (since,))]
            total_rows = one("SELECT COUNT(*) FROM audit")
        return {
            "hours": hours, "events": total, "logins_ok": logins_ok, "logins_failed": logins_failed,
            "rate_limited": rate_limited, "denied": denied, "top_failed_ips": top_fail_ips,
            "by_user": by_user, "total_rows": total_rows,
        }

    def verify_chain(self) -> dict:
        """ตรวจว่า chain ครบและไม่ถูกแก้ → {ok, rows, first_bad_id}"""
        prev = _GENESIS
        count = 0
        with self._conn() as conn:
            for r in conn.execute("SELECT * FROM audit ORDER BY id ASC"):
                expected = self._digest(
                    prev, r["ts"], r["username"] or "", r["ip"] or "", r["action"],
                    r["resource"] or "", r["status"] or "", r["detail"] or "",
                )
                if r["prev_hash"] != prev or r["hash"] != expected:
                    return {"ok": False, "rows": count, "first_bad_id": r["id"]}
                prev = r["hash"]
                count += 1
        return {"ok": True, "rows": count, "first_bad_id": None}


audit_log = AuditLog()
