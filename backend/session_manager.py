"""
Session Manager — จัดการ chat sessions ลง SQLite
รองรับ patient-centric sessions (เชื่อมกับชื่อผู้ป่วย)
"""

import shutil
import sqlite3
import uuid
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.crypto_utils import (
    encrypt, decrypt, encrypt_json, decrypt_json, blind_index, is_encrypted, require_keys,
)

NEW_CHAT_TITLE = "แชทใหม่"

class SessionManager:
    """
    เก็บ chat sessions ใน SQLite — patient-centric
    """

    def __init__(self, db_path: str = None, max_messages_per_session: int = 50):
        if db_path is None:
            from backend.config import CHAT_HISTORY_DB
            self.db_path = str(CHAT_HISTORY_DB)
        else:
            self.db_path = db_path
            
        self._max_messages = max_messages_per_session
        require_keys()
        self._backup_if_plaintext()
        self._init_db()
        self._migrate_encrypt()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA secure_delete = ON")
        return conn

    @staticmethod
    def _dec_session(row) -> dict:
        d = dict(row)
        for k in ("title", "patient_name", "session_title"):
            if k in d:
                d[k] = decrypt(d[k])
        d.pop("patient_key", None)
        return d

    def _backup_if_plaintext(self):
        """มีข้อมูลที่ยังไม่เข้ารหัส → สำรองไฟล์ DB ก่อนแตะโครงสร้าง/ข้อมูลใดๆ"""
        if not Path(self.db_path).exists():
            return
        plaintext_found = False
        conn = sqlite3.connect(self.db_path)
        try:
            for q in (
                "SELECT 1 FROM messages WHERE content IS NOT NULL AND content != '' AND content NOT LIKE 'enc1:%' LIMIT 1",
                "SELECT 1 FROM sessions WHERE patient_name IS NOT NULL AND patient_name NOT LIKE 'enc1:%' LIMIT 1",
            ):
                if conn.execute(q).fetchone():
                    plaintext_found = True
                    break
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
        if not plaintext_found:
            return
        from backend.config import BACKUP_DIR
        Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = Path(BACKUP_DIR) / f"{Path(self.db_path).stem}.{stamp}.plaintext.bak"
        shutil.copy2(self.db_path, backup)
        backup.chmod(0o600)
        print(f"[SECURITY] Encrypting existing data. Plaintext backup: {backup} (delete after verifying)")

    def _migrate_encrypt(self):
        """เข้ารหัสข้อมูลเก่าที่ยังเป็น plaintext (ทำซ้ำได้ปลอดภัย) — สำรองไฟล์ก่อนแตะข้อมูลเสมอ"""
        with self._get_conn() as conn:
            need_sessions = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE patient_key IS NULL "
                "OR (title IS NOT NULL AND title NOT LIKE 'enc1:%') "
                "OR (patient_name IS NOT NULL AND patient_name NOT LIKE 'enc1:%')"
            ).fetchone()[0]
            need_msgs = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE content IS NOT NULL AND content != '' AND content NOT LIKE 'enc1:%'"
            ).fetchone()[0]
            need_sums = conn.execute(
                "SELECT COUNT(*) FROM patient_summaries WHERE summary_json IS NOT NULL AND summary_json NOT LIKE 'enc1:%'"
            ).fetchone()[0]
            if not (need_sessions or need_msgs or need_sums):
                return

            for r in conn.execute("SELECT id, title, patient_name FROM sessions").fetchall():
                name_plain = decrypt(r["patient_name"])
                conn.execute(
                    "UPDATE sessions SET title = ?, patient_name = ?, patient_key = ? WHERE id = ?",
                    (encrypt(decrypt(r["title"])), encrypt(name_plain),
                     blind_index(name_plain) if name_plain else None, r["id"]),
                )
            for r in conn.execute(
                "SELECT id, content FROM messages WHERE content IS NOT NULL AND content != '' AND content NOT LIKE 'enc1:%'"
            ).fetchall():
                conn.execute("UPDATE messages SET content = ? WHERE id = ?", (encrypt(r["content"]), r["id"]))
            for r in conn.execute(
                "SELECT rowid AS rid, summary_json FROM patient_summaries WHERE summary_json NOT LIKE 'enc1:%'"
            ).fetchall():
                conn.execute("UPDATE patient_summaries SET summary_json = ? WHERE rowid = ?", (encrypt(r["summary_json"]), r["rid"]))
            conn.commit()
            conn.execute("VACUUM")

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    patient_name TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    sources TEXT,
                    timestamp TEXT,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
                )
            ''')
            
            # Migration: add token columns if they don't exist
            try:
                conn.execute("ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            
            try:
                conn.execute("ALTER TABLE messages ADD COLUMN completion_tokens INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # Patient summary cache table (Migrated to v2 with username)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS patient_summaries (
                    patient_name TEXT,
                    username TEXT,
                    summary_json TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (patient_name, username)
                )
            ''')
            conn.commit()

            # Auto-migrate patient_summaries: add username if missing
            cols_ps = [row[1] for row in conn.execute("PRAGMA table_info(patient_summaries)").fetchall()]
            if "username" not in cols_ps:
                conn.execute('''
                    CREATE TABLE patient_summaries_v2 (
                        patient_name TEXT,
                        username TEXT,
                        summary_json TEXT,
                        updated_at TEXT,
                        PRIMARY KEY (patient_name, username)
                    )
                ''')
                conn.execute("INSERT INTO patient_summaries_v2 SELECT patient_name, 'admin', summary_json, updated_at FROM patient_summaries")
                conn.execute("DROP TABLE patient_summaries")
                conn.execute("ALTER TABLE patient_summaries_v2 RENAME TO patient_summaries")
                conn.commit()

            # Auto-migrate sessions: add patient_name and username if missing
            cols = [row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()]
            if "patient_name" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN patient_name TEXT")
                conn.execute("UPDATE sessions SET patient_name = title WHERE patient_name IS NULL")
            if "username" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN username TEXT DEFAULT 'admin'")
                conn.execute("UPDATE sessions SET username = 'admin' WHERE username IS NULL")
            if "patient_key" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN patient_key TEXT")
            if "model_id" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN model_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_patient_key ON sessions(username, patient_key)")
            conn.commit()

            # patient_summaries ใช้ blind index เป็นคีย์ (ชื่อที่เข้ารหัสแล้วใช้เป็น PK ไม่ได้)
            cols_ps = [row[1] for row in conn.execute("PRAGMA table_info(patient_summaries)").fetchall()]
            if "patient_key" not in cols_ps:
                conn.execute('''
                    CREATE TABLE patient_summaries_v3 (
                        patient_key TEXT,
                        username TEXT,
                        patient_name TEXT,
                        summary_json TEXT,
                        updated_at TEXT,
                        PRIMARY KEY (patient_key, username)
                    )
                ''')
                for r in conn.execute("SELECT * FROM patient_summaries").fetchall():
                    name = decrypt(r["patient_name"])
                    if not name:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO patient_summaries_v3 VALUES (?, ?, ?, ?, ?)",
                        (blind_index(name), r["username"], encrypt(name), r["summary_json"], r["updated_at"]),
                    )
                conn.execute("DROP TABLE patient_summaries")
                conn.execute("ALTER TABLE patient_summaries_v3 RENAME TO patient_summaries")
                conn.commit()

    # ─── Create ──────────────────────────────────────────────────────────────

    def create_session(self, username: str, title: str = None, patient_name: str = None, model_id: str = None) -> dict:
        session_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        p_name = patient_name or title or NEW_CHAT_TITLE
        title = title or p_name
        
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, patient_name, patient_key, username, model_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, encrypt(title), encrypt(p_name), blind_index(p_name), username, model_id, now, now)
            )
            conn.commit()
            
        return {
            "id": session_id,
            "title": title,
            "patient_name": p_name,
            "username": username,
            "model_id": model_id,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }

    # ─── Read ────────────────────────────────────────────────────────────────

    def get_session(self, session_id: str, username: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ? AND username = ?", (session_id, username)).fetchone()
            if not row:
                return None
                
            session = self._dec_session(row)
            msg_rows = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC, id ASC", (session_id,)).fetchall()
            
            messages = []
            for m in msg_rows:
                messages.append({
                    "role": m["role"],
                    "content": decrypt(m["content"]),
                    "sources": json.loads(m["sources"]) if m["sources"] else [],
                    "timestamp": m["timestamp"],
                    "prompt_tokens": m["prompt_tokens"] if "prompt_tokens" in m.keys() else 0,
                    "completion_tokens": m["completion_tokens"] if "completion_tokens" in m.keys() else 0
                })
            session["messages"] = messages
            return session

    def get_session_model_id(self, session_id: str) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT model_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return row["model_id"] if row else None

    def list_sessions(self, username: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute('''
                SELECT s.*, COUNT(m.id) as message_count 
                FROM sessions s 
                LEFT JOIN messages m ON s.id = m.session_id 
                WHERE s.username = ?
                GROUP BY s.id 
                ORDER BY s.updated_at DESC
            ''', (username,)).fetchall()
            return [self._dec_session(r) for r in rows]

    # ─── Patient-Centric Queries ─────────────────────────────────────────────

    def check_patient_name_exists(self, patient_name: str, username: str) -> bool:
        """ตรวจว่ามี session ที่ใช้ชื่อผู้ป่วยนี้อยู่แล้วหรือไม่สำหรับ user นี้"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE patient_key = ? AND username = ?",
                (blind_index(patient_name), username)
            ).fetchone()
            return row[0] > 0

    def get_sessions_by_patient(self, patient_name: str, username: str) -> list[dict]:
        """ดึง sessions ทั้งหมดของผู้ป่วยคนนี้ สำหรับ user นี้"""
        with self._get_conn() as conn:
            rows = conn.execute('''
                SELECT s.*, COUNT(m.id) as message_count 
                FROM sessions s 
                LEFT JOIN messages m ON s.id = m.session_id 
                WHERE s.patient_key = ? AND s.username = ?
                GROUP BY s.id 
                ORDER BY s.created_at ASC
            ''', (blind_index(patient_name), username)).fetchall()
            return [self._dec_session(r) for r in rows]

    def get_all_patients(self, username: str) -> list[dict]:
        """ดึงรายชื่อผู้ป่วยทั้งหมด (distinct patient_name) พร้อมข้อมูลสรุป สำหรับ user นี้"""
        with self._get_conn() as conn:
            rows = conn.execute('''
                SELECT 
                    MAX(s.patient_name) as patient_name,
                    COUNT(DISTINCT s.id) as session_count,
                    SUM(msg_count) as total_messages,
                    MIN(s.created_at) as first_visit,
                    MAX(s.updated_at) as last_visit
                FROM sessions s
                LEFT JOIN (
                    SELECT session_id, COUNT(*) as msg_count 
                    FROM messages 
                    GROUP BY session_id
                ) mc ON s.id = mc.session_id
                WHERE s.patient_key IS NOT NULL AND s.patient_key != ? AND s.username = ?
                GROUP BY s.patient_key
                ORDER BY MAX(s.updated_at) DESC
            ''', (blind_index(NEW_CHAT_TITLE), username)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["patient_name"] = decrypt(d["patient_name"])
                out.append(d)
            return out

    def get_patient_all_messages(self, patient_name: str, username: str) -> list[dict]:
        """รวม messages จากทุก session ของผู้ป่วยคนนี้ (สำหรับ LLM summary) สำหรับ user นี้"""
        with self._get_conn() as conn:
            rows = conn.execute('''
                SELECT m.role, m.content, m.timestamp, s.title as session_title, s.created_at as session_date
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.patient_key = ? AND s.username = ?
                ORDER BY m.timestamp ASC
            ''', (blind_index(patient_name), username)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["content"] = decrypt(d["content"])
                d["session_title"] = decrypt(d["session_title"])
                out.append(d)
            return out

    # ─── Patient Summary Cache ───────────────────────────────────────────────

    def get_cached_summary(self, patient_name: str, username: str) -> dict | None:
        """ดึง cached summary ของผู้ป่วย สำหรับ user นี้"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM patient_summaries WHERE patient_key = ? AND username = ?",
                (blind_index(patient_name), username)
            ).fetchone()
            if not row:
                return None
            return {
                "patient_name": decrypt(row["patient_name"]),
                "summary": decrypt_json(row["summary_json"]),
                "updated_at": row["updated_at"],
            }

    def save_summary(self, patient_name: str, username: str, summary: dict):
        """บันทึก/อัปเดต cached summary"""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO patient_summaries (patient_key, username, patient_name, summary_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(patient_key, username) DO UPDATE SET 
                    patient_name = excluded.patient_name,
                    summary_json = excluded.summary_json,
                    updated_at = excluded.updated_at
            ''', (blind_index(patient_name), username, encrypt(patient_name), encrypt_json(summary), now))
            conn.commit()

    # ─── Update ──────────────────────────────────────────────────────────────

    def add_message(self, session_id: str, username: str, role: str, content: str, sources: list = None, prompt_tokens: int = 0, completion_tokens: int = 0) -> dict | None:
        with self._get_conn() as conn:
            # Check if session exists and belongs to user
            if not conn.execute("SELECT 1 FROM sessions WHERE id = ? AND username = ?", (session_id, username)).fetchone():
                return None

            now = datetime.now(timezone.utc).isoformat()
            sources_json = json.dumps(sources) if sources else "[]"
            
            conn.execute(
                "INSERT INTO messages (session_id, role, content, sources, timestamp, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, role, encrypt(content), sources_json, now, prompt_tokens, completion_tokens)
            )
            
            # Auto-title
            session = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            new_title = decrypt(session["title"])
            if role == "user" and new_title == NEW_CHAT_TITLE:
                new_title = content[:50] + ("..." if len(content) > 50 else "")
                
            conn.execute(
                "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                (now, encrypt(new_title), session_id)
            )
            
            # Removed auto-delete block. We handle pruning with summarization externally.
            conn.commit()

        return {
            "role": role,
            "content": content,
            "sources": sources or [],
            "timestamp": now,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens
        }

    def get_message_count(self, session_id: str) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)).fetchone()
            return row[0] if row else 0

    def get_raw_message_count(self, session_id: str) -> int:
        """จำนวนข้อความจริง (ไม่รวม summary block ที่ระบบสร้าง) — ใช้ตัดสินรอบ compaction"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role != 'system'",
                (session_id,)
            ).fetchone()
            return row[0] if row else 0

    def count_summary_blocks(self, session_id: str) -> int:
        """จำนวน compaction block (immutable) ที่มีอยู่แล้วใน session"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'system'",
                (session_id,)
            ).fetchone()
            return row[0] if row else 0

    def get_global_token_summary(self, username: str, month: str = None) -> dict:
        """คำนวณ Token รวมทั้งหมดแยกตามแชท พร้อมสรุปผลรวม (กรองตามเดือนได้ เช่น '2023-10') สำหรับ user นี้"""
        with self._get_conn() as conn:
            # Check if columns exist first to avoid errors if somehow not migrated
            try:
                if month:
                    # Filter messages by month string prefix (e.g. '2026-06')
                    rows = conn.execute('''
                        SELECT s.id, s.title, s.patient_name, 
                               SUM(m.prompt_tokens) as total_prompt, 
                               SUM(m.completion_tokens) as total_completion
                        FROM sessions s
                        LEFT JOIN messages m ON s.id = m.session_id AND m.timestamp LIKE ?
                        WHERE s.username = ?
                        GROUP BY s.id
                        HAVING total_prompt > 0 OR total_completion > 0
                        ORDER BY s.updated_at DESC
                    ''', (f"{month}%", username)).fetchall()
                else:
                    rows = conn.execute('''
                        SELECT s.id, s.title, s.patient_name, 
                               SUM(m.prompt_tokens) as total_prompt, 
                               SUM(m.completion_tokens) as total_completion
                        FROM sessions s
                        LEFT JOIN messages m ON s.id = m.session_id
                        WHERE s.username = ?
                        GROUP BY s.id
                        ORDER BY s.updated_at DESC
                    ''', (username,)).fetchall()
                
                total_p = sum((r["total_prompt"] or 0) for r in rows)
                total_c = sum((r["total_completion"] or 0) for r in rows)
                
                return {
                    "total_prompt": total_p,
                    "total_completion": total_c,
                    "sessions": [dict(r) for r in rows]
                }
            except sqlite3.OperationalError:
                return {"total_prompt": 0, "total_completion": 0, "sessions": []}

    def get_oldest_messages(self, session_id: str, limit: int) -> list[dict]:
        """ข้อความจริงที่เก่าที่สุด (ไม่รวม summary block) — block เป็น immutable ห้ามสรุปทับ"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? AND role != 'system' ORDER BY timestamp ASC, id ASC LIMIT ?",
                (session_id, limit)
            ).fetchall()
            return [{"id": r["id"], "role": r["role"], "content": decrypt(r["content"]), "timestamp": r["timestamp"], "prompt_tokens": r["prompt_tokens"] if "prompt_tokens" in r.keys() else 0, "completion_tokens": r["completion_tokens"] if "completion_tokens" in r.keys() else 0} for r in rows]

    def replace_messages_with_summary(self, session_id: str, message_ids_to_delete: list[int], summary_content: str):
        """
        แทนที่ข้อความเก่าด้วย compaction block (role=system) หนึ่งก้อน
        block ใหม่ถูกวางเวลาให้อยู่ "หลัง block เดิมทั้งหมด แต่ก่อนข้อความจริงที่เหลือ"
        เพื่อให้ลำดับ block 1, 2, 3, ... คงที่และไม่สลับกับบทสนทนาปัจจุบัน
        """
        if not message_ids_to_delete:
            return
        with self._get_conn() as conn:
            placeholders = ",".join("?" * len(message_ids_to_delete))
            conn.execute(f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})", [session_id] + message_ids_to_delete)

            row = conn.execute(
                "SELECT timestamp FROM messages WHERE session_id = ? AND role != 'system' ORDER BY timestamp ASC, id ASC LIMIT 1",
                (session_id,)
            ).fetchone()
            if row:
                try:
                    ts = datetime.fromisoformat(row["timestamp"])
                    timestamp_for_summary = (ts - timedelta(milliseconds=1)).isoformat()
                except (ValueError, TypeError):
                    timestamp_for_summary = row["timestamp"]
            else:
                timestamp_for_summary = datetime.now(timezone.utc).isoformat()

            conn.execute(
                "INSERT INTO messages (session_id, role, content, sources, timestamp, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, "system", encrypt(summary_content), "[]", timestamp_for_summary, 0, 0)
            )
            conn.commit()



    def get_history(self, session_id: str, username: str, last_n: int = None) -> list[dict]:
        session = self.get_session(session_id, username)
        if not session:
            return []
        messages = session["messages"]
        if last_n:
            return messages[-last_n:]
        return messages

    def rename_session(self, session_id: str, username: str, new_title: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT patient_name FROM sessions WHERE id = ? AND username = ?", (session_id, username)).fetchone()
            if not row:
                return False
            
            old_name = decrypt(row["patient_name"])
            new_key = blind_index(new_title)
            
            conn.execute(
                "UPDATE sessions SET title = ?, patient_name = ?, patient_key = ?, updated_at = ? WHERE id = ?",
                (encrypt(new_title), encrypt(new_title), new_key, datetime.now(timezone.utc).isoformat(), session_id)
            )
            
            if old_name and old_name != new_title:
                old_key = blind_index(old_name)
                try:
                    conn.execute(
                        "UPDATE patient_summaries SET patient_key = ?, patient_name = ? WHERE patient_key = ? AND username = ?",
                        (new_key, encrypt(new_title), old_key, username),
                    )
                except sqlite3.IntegrityError:
                    conn.execute("DELETE FROM patient_summaries WHERE patient_key = ? AND username = ?", (old_key, username))
            
            conn.commit()
            return True

    def list_recent_chats(self, limit: int = 50) -> list[dict]:
        """แชทล่าสุดของทุกผู้ใช้ (ชื่อผู้ป่วยด้วย เพราะ admin ต้องดูว่าแชทไหนของใคร)"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.patient_name, s.username, s.model_id, s.updated_at,
                       COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "username": r["username"],
                "patient_name": decrypt(r["patient_name"]),
                "model_id": r["model_id"] or "3.1",
                "updated_at": r["updated_at"],
                "message_count": r["message_count"],
            }
            for r in rows
        ]

    # ─── Admin stats (counts only, no PII) ───────────────────────────────────

    def get_security_stats(self) -> dict:
        with self._get_conn() as conn:
            users = [dict(r) for r in conn.execute(
                "SELECT s.username, COUNT(DISTINCT s.id) AS sessions, COUNT(m.id) AS messages, MAX(s.updated_at) AS last_activity "
                "FROM sessions s LEFT JOIN messages m ON m.session_id = s.id GROUP BY s.username"
            )]
            def one(sql):
                return conn.execute(sql).fetchone()[0]

            def _sum_by_model(c):
                out: dict[str, int] = {}
                for mid, n in c.execute("SELECT model_id, COUNT(*) FROM sessions GROUP BY model_id"):
                    key = mid or "3.1"
                    out[key] = out.get(key, 0) + n
                return out
            return {
                "users": users,
                "sessions_total": one("SELECT COUNT(*) FROM sessions"),
                "messages_total": one("SELECT COUNT(*) FROM messages"),
                "messages_encrypted": one("SELECT COUNT(*) FROM messages WHERE content LIKE 'enc1:%'"),
                "messages_plaintext": one("SELECT COUNT(*) FROM messages WHERE content IS NOT NULL AND content != '' AND content NOT LIKE 'enc1:%'"),
                "sessions_by_model": _sum_by_model(conn),
                "sessions_plaintext": one("SELECT COUNT(*) FROM sessions WHERE patient_name IS NOT NULL AND patient_name NOT LIKE 'enc1:%'"),
            }

    # ─── Delete ──────────────────────────────────────────────────────────────

    def delete_session(self, session_id: str, username: str) -> bool:
        with self._get_conn() as conn:
            if not conn.execute("SELECT 1 FROM sessions WHERE id = ? AND username = ?", (session_id, username)).fetchone():
                return False
            # Cascade delete will handle messages
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.commit()
            return True

    def clear_session_messages(self, session_id: str):
        """ลบข้อความทั้งหมดใน session แต่เก็บ session ไว้"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.commit()

    def delete_last_exchange(self, session_id: str):
        """ลบข้อความ user+assistant คู่ล่าสุด (สำหรับ edit message)"""
        with self._get_conn() as conn:
            # Delete last assistant message
            conn.execute(
                "DELETE FROM messages WHERE id = (SELECT id FROM messages WHERE session_id = ? AND role = 'assistant' ORDER BY timestamp DESC LIMIT 1)",
                (session_id,)
            )
            # Delete last user message
            conn.execute(
                "DELETE FROM messages WHERE id = (SELECT id FROM messages WHERE session_id = ? AND role = 'user' ORDER BY timestamp DESC LIMIT 1)",
                (session_id,)
            )
            conn.commit()

    def delete_last_assistant_message(self, session_id: str):
        """ลบข้อความ assistant ล่าสุด (สำหรับ regenerate)"""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM messages WHERE id = (SELECT id FROM messages WHERE session_id = ? AND role = 'assistant' ORDER BY timestamp DESC LIMIT 1)",
                (session_id,)
            )
            conn.commit()

    def clear_all(self):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM patient_summaries")
            conn.commit()
