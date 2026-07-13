"""
Session Manager — จัดการ chat sessions ลง SQLite
รองรับ patient-centric sessions (เชื่อมกับชื่อผู้ป่วย)
"""

import sqlite3
import uuid
import json
from datetime import datetime

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
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

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
            conn.commit()

    # ─── Create ──────────────────────────────────────────────────────────────

    def create_session(self, username: str, title: str = None, patient_name: str = None) -> dict:
        session_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        p_name = patient_name or title or "แชทใหม่"
        title = title or p_name
        
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, patient_name, username, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, title, p_name, username, now, now)
            )
            conn.commit()
            
        return {
            "id": session_id,
            "title": title,
            "patient_name": p_name,
            "username": username,
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
                
            session = dict(row)
            msg_rows = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,)).fetchall()
            
            messages = []
            for m in msg_rows:
                messages.append({
                    "role": m["role"],
                    "content": m["content"],
                    "sources": json.loads(m["sources"]) if m["sources"] else [],
                    "timestamp": m["timestamp"],
                    "prompt_tokens": m["prompt_tokens"] if "prompt_tokens" in m.keys() else 0,
                    "completion_tokens": m["completion_tokens"] if "completion_tokens" in m.keys() else 0
                })
            session["messages"] = messages
            return session

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
            return [dict(r) for r in rows]

    # ─── Patient-Centric Queries ─────────────────────────────────────────────

    def check_patient_name_exists(self, patient_name: str, username: str) -> bool:
        """ตรวจว่ามี session ที่ใช้ชื่อผู้ป่วยนี้อยู่แล้วหรือไม่สำหรับ user นี้"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE patient_name = ? AND username = ?",
                (patient_name, username)
            ).fetchone()
            return row[0] > 0

    def get_sessions_by_patient(self, patient_name: str, username: str) -> list[dict]:
        """ดึง sessions ทั้งหมดของผู้ป่วยคนนี้ สำหรับ user นี้"""
        with self._get_conn() as conn:
            rows = conn.execute('''
                SELECT s.*, COUNT(m.id) as message_count 
                FROM sessions s 
                LEFT JOIN messages m ON s.id = m.session_id 
                WHERE s.patient_name = ? AND s.username = ?
                GROUP BY s.id 
                ORDER BY s.created_at ASC
            ''', (patient_name, username)).fetchall()
            return [dict(r) for r in rows]

    def get_all_patients(self, username: str) -> list[dict]:
        """ดึงรายชื่อผู้ป่วยทั้งหมด (distinct patient_name) พร้อมข้อมูลสรุป สำหรับ user นี้"""
        with self._get_conn() as conn:
            rows = conn.execute('''
                SELECT 
                    s.patient_name,
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
                WHERE s.patient_name IS NOT NULL AND s.patient_name != 'แชทใหม่' AND s.username = ?
                GROUP BY s.patient_name
                ORDER BY MAX(s.updated_at) DESC
            ''', (username,)).fetchall()
            return [dict(r) for r in rows]

    def get_patient_all_messages(self, patient_name: str, username: str) -> list[dict]:
        """รวม messages จากทุก session ของผู้ป่วยคนนี้ (สำหรับ LLM summary) สำหรับ user นี้"""
        with self._get_conn() as conn:
            rows = conn.execute('''
                SELECT m.role, m.content, m.timestamp, s.title as session_title, s.created_at as session_date
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.patient_name = ? AND s.username = ?
                ORDER BY m.timestamp ASC
            ''', (patient_name, username)).fetchall()
            return [dict(r) for r in rows]

    # ─── Patient Summary Cache ───────────────────────────────────────────────

    def get_cached_summary(self, patient_name: str, username: str) -> dict | None:
        """ดึง cached summary ของผู้ป่วย สำหรับ user นี้"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM patient_summaries WHERE patient_name = ? AND username = ?",
                (patient_name, username)
            ).fetchone()
            if not row:
                return None
            return {
                "patient_name": row["patient_name"],
                "summary": json.loads(row["summary_json"]),
                "updated_at": row["updated_at"],
            }

    def save_summary(self, patient_name: str, username: str, summary: dict):
        """บันทึก/อัปเดต cached summary"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO patient_summaries (patient_name, username, summary_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(patient_name, username) DO UPDATE SET 
                    summary_json = excluded.summary_json,
                    updated_at = excluded.updated_at
            ''', (patient_name, username, json.dumps(summary, ensure_ascii=False), now))
            conn.commit()

    # ─── Update ──────────────────────────────────────────────────────────────

    def add_message(self, session_id: str, username: str, role: str, content: str, sources: list = None, prompt_tokens: int = 0, completion_tokens: int = 0) -> dict | None:
        with self._get_conn() as conn:
            # Check if session exists and belongs to user
            if not conn.execute("SELECT 1 FROM sessions WHERE id = ? AND username = ?", (session_id, username)).fetchone():
                return None

            now = datetime.now().isoformat()
            sources_json = json.dumps(sources) if sources else "[]"
            
            conn.execute(
                "INSERT INTO messages (session_id, role, content, sources, timestamp, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, role, content, sources_json, now, prompt_tokens, completion_tokens)
            )
            
            # Auto-title
            session = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            new_title = session["title"]
            if role == "user" and session["title"] == "แชทใหม่":
                new_title = content[:50] + ("..." if len(content) > 50 else "")
                
            conn.execute(
                "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                (now, new_title, session_id)
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
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?", (session_id, limit)).fetchall()
            return [{"id": r["id"], "role": r["role"], "content": r["content"], "timestamp": r["timestamp"], "prompt_tokens": r["prompt_tokens"] if "prompt_tokens" in r.keys() else 0, "completion_tokens": r["completion_tokens"] if "completion_tokens" in r.keys() else 0} for r in rows]

    def replace_messages_with_summary(self, session_id: str, message_ids_to_delete: list[int], summary_content: str):
        if not message_ids_to_delete:
            return
        with self._get_conn() as conn:
            # Delete old messages
            placeholders = ",".join("?" * len(message_ids_to_delete))
            conn.execute(f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})", [session_id] + message_ids_to_delete)
            
            # Find the timestamp of the earliest remaining message to place summary before it
            row = conn.execute("SELECT timestamp FROM messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT 1", (session_id,)).fetchone()
            timestamp_for_summary = row["timestamp"] if row else datetime.now().isoformat()
            
            conn.execute(
                "INSERT INTO messages (session_id, role, content, sources, timestamp, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, "system", f"[สรุปเนื้อหาก่อนหน้า]\n{summary_content}", "[]", timestamp_for_summary, 0, 0)
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
            
            old_name = row["patient_name"]
            
            conn.execute(
                "UPDATE sessions SET title = ?, patient_name = ?, updated_at = ? WHERE id = ?",
                (new_title, new_title, datetime.now().isoformat(), session_id)
            )
            
            if old_name and old_name != new_title:
                # Update cache so the summary moves to the new name
                try:
                    conn.execute("UPDATE patient_summaries SET patient_name = ? WHERE patient_name = ? AND username = ?", (new_title, old_name, username))
                except sqlite3.IntegrityError:
                    # If the new name already exists, we can optionally delete the old one or merge. 
                    # For simplicity, we just delete the old orphaned summary cache.
                    conn.execute("DELETE FROM patient_summaries WHERE patient_name = ? AND username = ?", (old_name, username))
            
            conn.commit()
            return True

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
