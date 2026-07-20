"""
FastAPI Server - PharmaCare AI
=====================================
Pharmacy Chatbot API with RAG (Retrieval-Augmented Generation)
Patient-centric: แต่ละ session เชื่อมกับชื่อผู้ป่วย

Endpoints:
  Auth:
    POST /api/login               -> login with username/password
    GET  /api/me                  -> get current user info
    POST /api/logout              -> logout

  Chat:
    POST /api/chat               -> send question, get answer with sources
    POST /api/chat/stream        -> streaming chat
    POST /api/chat/regenerate    -> regenerate last answer

  Sessions:
    GET  /api/sessions           -> list all sessions
    POST /api/sessions           -> create new session (with patient_name)
    GET  /api/sessions/{id}      -> get session history
    DELETE /api/sessions/{id}    -> delete session
    PATCH /api/sessions/{id}     -> rename session
    DELETE /api/sessions/{id}/messages  -> clear messages
    GET  /api/sessions/search    -> search sessions

  Patients:
    GET  /api/patients                  -> list all patients
    GET  /api/patients/{name}/sessions  -> get all sessions for a patient
    GET  /api/patients/{name}/summary   -> get cached AI summary
    POST /api/patients/{name}/summary   -> generate/update AI summary
    GET  /api/patients/check-name       -> check if patient name exists

  Test Cases:
    GET  /api/testcases          -> get test cases from CSV
    POST /api/testcases/run-one  -> run single test case

  System:
    GET  /api/health             -> health check

Run:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""

import csv
import re
import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
import json

from backend.rag_engine import generate_answer, evaluate_answer, evaluate_answer_llm, generate_answer_stream, summarize_history
from backend.session_manager import SessionManager
from backend.semantic_memory import semantic_memory
from backend.patient_summary import generate_patient_summary
from backend.auth import verify_credentials, create_token, verify_token, get_user_display_name
from backend.config import (
    PROJECT_ROOT, FRONTEND_DIR, DATA_DIR, TEST_CASE_CSV, CHAT_HISTORY_DB, DRUGS_JSON,
    RECENT_WINDOW, COMPACT_THRESHOLD, COMPACT_BATCH, SUMMARY_BLOCK_MAX,
    MEMORY_MIN_SIMILARITY, MEMORY_RECALL_TOP_K, MEMORY_MIN_SESSION_MESSAGES,
)
from datetime import datetime, timezone

BASE_DIR = PROJECT_ROOT

def prune_and_summarize(session_id: str, username: str):
    """
    Compaction แบบ immutable block: เมื่อข้อความจริงเกิน threshold สรุปก้อนเก่าสุดเป็น
    "[สรุปช่วงที่ N]" หนึ่งก้อน -- block เดิมไม่ถูกนำมาสรุปซ้ำ (กัน summary-of-summary drift)
    """
    count = sessions.get_raw_message_count(session_id)
    if count > COMPACT_THRESHOLD:
        oldest = sessions.get_oldest_messages(session_id, limit=COMPACT_BATCH)
        summary = summarize_history(oldest)
        if not summary or summary.startswith("ไม่สามารถ"):
            return
        block_no = sessions.count_summary_blocks(session_id) + 1
        ids_to_delete = [m["id"] for m in oldest]
        sessions.replace_messages_with_summary(
            session_id, ids_to_delete, f"[สรุปช่วงที่ {block_no} ของแชทนี้ (สรุปโดยระบบ)]\n{summary}"
        )


_TH_EN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[฀-๿]{2,}")


def _select_summary_blocks(blocks: list[dict], question: str, cap: int = SUMMARY_BLOCK_MAX) -> list[dict]:
    """
    เลือก compaction block ที่เกี่ยวข้องกับคำถาม (Memory Retriever แบบเบา):
    block น้อยกว่า cap ส่งทั้งหมด; ถ้ามาก ให้คะแนนจาก token ทับซ้อนกับคำถาม + ความใหม่
    """
    if len(blocks) <= cap:
        return blocks
    q_tokens = set(_TH_EN_TOKEN_RE.findall((question or "").lower()))
    scored = []
    for idx, b in enumerate(blocks):
        b_tokens = set(_TH_EN_TOKEN_RE.findall((b.get("content") or "").lower()))
        overlap = len(q_tokens & b_tokens)
        scored.append((overlap, idx, b))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chosen = scored[:cap]
    chosen.sort(key=lambda x: x[1])          # คงลำดับเวลาเดิมตอนส่งเข้า LLM
    return [b for _, _, b in chosen]

# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 60)
    print("  PharmaCare AI - Pharmacy Chatbot")
    print("  RAG-powered by Gemini + Qdrant")
    print("  Open in browser: http://localhost:8899")
    print("  (Docker maps host 8899 → container uvicorn :8000)")
    print("=" * 60)
    yield
    print("\n[SHUTDOWN] Server stopped.")


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="PharmaCare AI",
    description="RAG-based Pharmacy Chatbot API",
    version="2.0.0",
    lifespan=lifespan,
)

# Session Manager
sessions = SessionManager(db_path=str(CHAT_HISTORY_DB), max_messages_per_session=50)

# ─── Static Files ────────────────────────────────────────────────────────────

FRONTEND_DIR.mkdir(exist_ok=True)

if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

# React frontend (Vite build output). Only mounted once it's been built
# (`npm run build` inside frontend/react-app) so a missing dist/ folder never
# breaks server startup.
REACT_DIST_DIR = FRONTEND_DIR / "react-app" / "dist"
REACT_APP_ENABLED = (REACT_DIST_DIR / "index.html").exists()
if REACT_APP_ENABLED:
    app.mount("/assets", StaticFiles(directory=str(REACT_DIST_DIR / "assets")), name="react-assets")


# ─── Auth Dependency ─────────────────────────────────────────────────────────

# Endpoints that don't require auth
PUBLIC_PATHS = {"/api/login", "/api/health", "/login", "/", "/testcase", "/patients", "/patient"}

async def get_current_user(request: Request) -> str:
    """Extract and verify JWT token from Authorization header or cookie"""
    token = None
    
    # Try Authorization header first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    
    # Try cookie
    if not token:
        token = request.cookies.get("token")
    
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    username = verify_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    return username


# ─── Models ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: list[dict]
    chunks_used: int

class CreateSessionRequest(BaseModel):
    title: str | None = None
    patient_name: str | None = None

class RenameSessionRequest(BaseModel):
    title: str

class RegenerateRequest(BaseModel):
    session_id: str

class EditMessageRequest(BaseModel):
    session_id: str
    message: str


# ─── Pages (React SPA) ───────────────────────────────────────────────────────
# Client-side routing (react-router-dom) picks the page from the URL, so every
# page route returns the same index.html; only enabled once dist/ has been
# built (see REACT_APP_ENABLED above).

def _serve_react_app():
    if not REACT_APP_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="React frontend not built yet. Run: cd frontend/react-app && npm install && npm run build",
        )
    return FileResponse(str(REACT_DIST_DIR / "index.html"))

@app.get("/")
async def home():
    return _serve_react_app()

@app.get("/login")
async def login_page():
    return _serve_react_app()

@app.get("/testcase")
async def testcase_page():
    return _serve_react_app()

@app.get("/patients")
async def patients_page():
    return _serve_react_app()

@app.get("/patient/{patient_name}")
async def patient_page(patient_name: str):
    return _serve_react_app()


# ─── Auth API ────────────────────────────────────────────────────────────────

@app.post("/api/login")
async def login(req: LoginRequest):
    if not verify_credentials(req.username, req.password):
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    
    token = create_token(req.username)
    display_name = get_user_display_name(req.username)
    
    response = JSONResponse({
        "status": "ok",
        "token": token,
        "username": req.username,
        "display_name": display_name,
    })
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        max_age=86400,
        samesite="lax",
    )
    return response

@app.get("/api/me")
async def get_me(username: str = Depends(get_current_user)):
    display_name = get_user_display_name(username)
    return {
        "username": username,
        "display_name": display_name,
    }

@app.post("/api/logout")
async def logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("token")
    return response


# ─── Helper ──────────────────────────────────────────────────────────────────

def _assemble_history(session_id: str, username: str, question: str) -> list[dict]:
    """
    ประกอบ history สำหรับ LLM ตามลำดับ:
      1) compaction blocks (immutable) ที่เกี่ยวข้องกับคำถาม (สูงสุด SUMMARY_BLOCK_MAX)
      2) semantic recall ของข้อความเก่าที่หลุด recent window (เฉพาะแชทยาว + similarity ถึงเกณฑ์)
      3) recent window ล่าสุด (ไม่รวมข้อความ user ปัจจุบันที่เพิ่งบันทึก)
    """
    session = sessions.get_session(session_id, username)
    messages = session.get("messages", []) if session else []

    blocks = [m for m in messages if m["role"] == "system"]
    normal = [m for m in messages if m["role"] != "system"]
    recent = normal[-RECENT_WINDOW:]

    combined: list[dict] = []
    seen_contents: set[str] = set(m["content"] for m in recent)

    for b in _select_summary_blocks(blocks, question):
        combined.append({"role": "system", "content": b["content"]})
        seen_contents.add(b["content"])

    # semantic recall เฉพาะเมื่อ recent window ไม่ครอบคลุมทั้งแชทแล้ว (แชทสั้นไม่ต้องเสีย latency)
    if len(normal) > MEMORY_MIN_SESSION_MESSAGES:
        semantic_history = semantic_memory.search_memory(session_id, question, top_k=MEMORY_RECALL_TOP_K)
        for m in semantic_history:
            if m.get("similarity", 0.0) < MEMORY_MIN_SIMILARITY:
                continue
            if m["content"] in seen_contents:
                continue
            combined.append({"role": m["role"], "content": m["content"]})
            seen_contents.add(m["content"])

    combined.extend({"role": m["role"], "content": m["content"]} for m in recent[:-1])
    return combined


def _remember_async(session_id: str, role: str, content: str) -> None:
    """เขียนลง semantic memory ใน background thread (embed คือ network call ~0.3-1s ไม่ควรบล็อกคำตอบ)"""
    threading.Thread(
        target=semantic_memory.add_to_memory,
        args=(session_id, role, content, datetime.now(timezone.utc).isoformat()),
        daemon=True,
    ).start()


def _process_message_and_get_history(session_id: str, message: str, username: str) -> list[dict]:
    _remember_async(session_id, "user", message)

    sessions.add_message(session_id, username, "user", message)
    prune_and_summarize(session_id, username)
    return _assemble_history(session_id, username, message)


# ─── Chat API ────────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, username: str = Depends(get_current_user)):
    """Send a question, get RAG-powered answer with source citations"""

    # Auto-create session
    if not req.session_id or not sessions.get_session(req.session_id, username):
        session = sessions.create_session(username)
        session_id = session["id"]
    else:
        session_id = req.session_id

    combined_history = _process_message_and_get_history(session_id, req.message, username)

    # RAG: search + generate
    try:
        result = await asyncio.to_thread(
            generate_answer,
            question=req.message,
            history=combined_history,
            top_k=5,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG Error: {str(e)}")

    # Save assistant message
    sessions.add_message(
        session_id, username, "assistant",
        result["answer"],
        sources=result["sources"]
    )
    _remember_async(session_id, "assistant", result["answer"])

    return ChatResponse(
        session_id  = session_id,
        answer      = result["answer"],
        sources     = result["sources"],
        chunks_used = result["chunks_used"],
    )

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, username: str = Depends(get_current_user)):
    """Streaming endpoint for chat"""
    if not req.session_id or not sessions.get_session(req.session_id, username):
        session = sessions.create_session(username)
        session_id = session["id"]
    else:
        session_id = req.session_id

    combined_history = _process_message_and_get_history(session_id, req.message, username)

    async def event_generator():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        
        full_answer = ""
        sources = []
        chunks_used = 0
        prompt_tokens = 0
        completion_tokens = 0

        try:
            async for chunk in generate_answer_stream(req.message, combined_history, top_k=5):
                # We yield exactly the data generated
                yield f"data: {chunk}\n"

                # We need to capture the full answer and sources to save to DB
                chunk_data = json.loads(chunk)
                if chunk_data.get("type") == "done":
                    full_answer = chunk_data.get("full_answer", "")
                    sources = chunk_data.get("sources", [])
                    usage = chunk_data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

            sessions.add_message(session_id, username, "assistant", full_answer, sources=sources, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            _remember_async(session_id, "assistant", full_answer)
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── Edit Last Message ──────────────────────────────────────────────────────

@app.post("/api/chat/edit")
async def edit_last_message(req: EditMessageRequest, username: str = Depends(get_current_user)):
    """Edit the last user message: delete the last user+assistant pair, then re-ask"""
    session = sessions.get_session(req.session_id, username)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Delete last user + assistant messages from SQLite
    sessions.delete_last_exchange(req.session_id)
    
    # Now re-process with new message via streaming
    combined_history = _process_message_and_get_history(req.session_id, req.message, username)
    
    async def event_generator():
        yield f"data: {json.dumps({'type': 'session', 'session_id': req.session_id})}\n\n"
        
        full_answer = ""
        sources = []
        prompt_tokens = 0
        completion_tokens = 0

        try:
            async for chunk in generate_answer_stream(req.message, combined_history, top_k=5):
                yield f"data: {chunk}\n"
                chunk_data = json.loads(chunk)
                if chunk_data.get("type") == "done":
                    full_answer = chunk_data.get("full_answer", "")
                    sources = chunk_data.get("sources", [])
                    usage = chunk_data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

            sessions.add_message(req.session_id, username, "assistant", full_answer, sources=sources, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            _remember_async(req.session_id, "assistant", full_answer)
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── Regenerate ──────────────────────────────────────────────────────────────

@app.post("/api/chat/regenerate")
async def regenerate(req: RegenerateRequest, username: str = Depends(get_current_user)):
    """Regenerate the last assistant answer"""
    session = sessions.get_session(req.session_id, username)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    messages = session.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages to regenerate")
    
    # Find the last user message
    last_user_msg = None
    for msg in reversed(messages):
        if msg["role"] == "user":
            last_user_msg = msg["content"]
            break
    
    if not last_user_msg:
        raise HTTPException(status_code=400, detail="No user message found")
    
    # Delete the last assistant message
    sessions.delete_last_assistant_message(req.session_id)

    combined_history = _assemble_history(req.session_id, username, last_user_msg)

    async def event_generator():
        yield f"data: {json.dumps({'type': 'session', 'session_id': req.session_id})}\n\n"
        
        full_answer = ""
        sources = []
        prompt_tokens = 0
        completion_tokens = 0

        try:
            async for chunk in generate_answer_stream(last_user_msg, combined_history, top_k=5):
                yield f"data: {chunk}\n"
                chunk_data = json.loads(chunk)
                if chunk_data.get("type") == "done":
                    full_answer = chunk_data.get("full_answer", "")
                    sources = chunk_data.get("sources", [])
                    usage = chunk_data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

            sessions.add_message(req.session_id, username, "assistant", full_answer, sources=sources, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            _remember_async(req.session_id, "assistant", full_answer)
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── Session API ─────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_all_sessions(username: str = Depends(get_current_user)):
    return sessions.list_sessions(username)

@app.post("/api/sessions")
async def create_new_session(req: CreateSessionRequest = None, username: str = Depends(get_current_user)):
    patient_name = req.patient_name if req else None
    title = req.title or patient_name
    session = sessions.create_session(username, title=title, patient_name=patient_name)
    return session

@app.get("/api/sessions/search")
async def search_sessions(q: str = "", username: str = Depends(get_current_user)):
    """Search sessions by title"""
    all_sessions = sessions.list_sessions(username)
    if not q.strip():
        return all_sessions
    q_lower = q.lower()
    return [s for s in all_sessions if q_lower in s.get("title", "").lower()]

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, username: str = Depends(get_current_user)):
    session = sessions.get_session(session_id, username)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, username: str = Depends(get_current_user)):
    if sessions.delete_session(session_id, username):
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="Session not found")

@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameSessionRequest, username: str = Depends(get_current_user)):
    if sessions.rename_session(session_id, username, req.title):
        return {"status": "renamed", "session_id": session_id, "title": req.title}
    raise HTTPException(status_code=404, detail="Session not found")

@app.delete("/api/sessions/{session_id}/messages")
async def clear_session_messages(session_id: str, username: str = Depends(get_current_user)):
    """Clear all messages in a session but keep the session itself"""
    session = sessions.get_session(session_id, username)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    with sessions._get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()
    
    return {"status": "cleared", "session_id": session_id}


# ─── Tokens ──────────────────────────────────────────────────────────────────

@app.get("/api/tokens/summary")
async def get_token_summary(month: str = None, username: str = Depends(get_current_user)):
    """Get global token usage summary across all chats"""
    return sessions.get_global_token_summary(username, month)


# ─── Test Case API ───────────────────────────────────────────────────────────

@app.get("/api/drugs")
async def get_drugs(username: str = Depends(get_current_user)):
    if not DRUGS_JSON.exists():
        raise HTTPException(status_code=404, detail="drugs.json not found")
    with open(DRUGS_JSON, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/testcases")
async def get_testcases(username: str = Depends(get_current_user)):
    csv_path = TEST_CASE_CSV
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="test_case.csv not found")

    cases = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cases.append({
                "id"          : row.get("id", ""),
                "input"       : row.get("input", ""),
                "expectation" : row.get("expectation", ""),
                "case"        : row.get("case", ""),
            })
    return cases


@app.post("/api/testcases/run-one")
async def run_one_testcase(req: dict, username: str = Depends(get_current_user)):
    question    = req.get("input", "")
    expectation = req.get("expectation", "")
    case_id     = req.get("id", "")

    if not question:
        raise HTTPException(status_code=400, detail="Missing input")

    # Generate answer
    try:
        result = await asyncio.to_thread(
            generate_answer,
            question=question,
            top_k=5,
        )
        prediction = result["answer"]
        sources    = result["sources"]
    except Exception as e:
        prediction = f"Error: {str(e)}"
        sources    = []

    # Evaluate cosine similarity
    try:
        cosine = await asyncio.to_thread(
            evaluate_answer,
            prediction=prediction,
            expectation=expectation,
        )
    except Exception as e:
        cosine = 0.0

    # Evaluate LLM score
    try:
        llm_eval = await asyncio.to_thread(
            evaluate_answer_llm,
            prediction=prediction,
            expectation=expectation,
        )
        llm_score = llm_eval.get("score", 0)
        llm_reasoning = llm_eval.get("reasoning", "")
    except Exception as e:
        llm_score = 0
        llm_reasoning = f"Error: {str(e)}"

    return {
        "id"          : case_id,
        "input"       : question,
        "expectation" : expectation,
        "prediction"  : prediction,
        "sources"     : sources,
        "cosine"      : round(cosine, 4),
        "llm_score"   : llm_score,
        "llm_reasoning": llm_reasoning,
        "pass"        : llm_score >= 3,
    }

# ─── Patient API ─────────────────────────────────────────────────────────────

@app.get("/api/patients/check-name")
async def check_patient_name(name: str = "", username: str = Depends(get_current_user)):
    """ตรวจว่าชื่อผู้ป่วยนี้มีอยู่แล้วหรือไม่"""
    if not name.strip():
        return {"exists": False}
    exists = sessions.check_patient_name_exists(name.strip(), username)
    return {"exists": exists, "name": name.strip()}

@app.get("/api/patients")
async def list_patients(username: str = Depends(get_current_user)):
    """ดึงรายชื่อผู้ป่วยทั้งหมด"""
    patients = sessions.get_all_patients(username)
    # เพิ่มข้อมูล cached summary ถ้ามี
    for p in patients:
        cached = sessions.get_cached_summary(p["patient_name"], username)
        p["has_summary"] = cached is not None
        p["summary_updated_at"] = cached["updated_at"] if cached else None
        if cached and cached.get("summary"):
            risk = cached["summary"].get("risk_assessment", {})
            p["risk_level"] = risk.get("level", "low")
        else:
            p["risk_level"] = None
    return patients

@app.get("/api/patients/{patient_name}/sessions")
async def get_patient_sessions(patient_name: str, username: str = Depends(get_current_user)):
    """ดึง sessions ทั้งหมดของผู้ป่วย"""
    patient_sessions = sessions.get_sessions_by_patient(patient_name, username)
    if not patient_sessions:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลผู้ป่วยนี้")
    return patient_sessions

@app.get("/api/patients/{patient_name}/summary")
async def get_patient_summary(patient_name: str, username: str = Depends(get_current_user)):
    """ดึง cached AI summary ของผู้ป่วย (ไม่เรียก LLM)"""
    cached = sessions.get_cached_summary(patient_name, username)
    patient_sessions = sessions.get_sessions_by_patient(patient_name, username)
    
    return {
        "patient_name": patient_name,
        "session_count": len(patient_sessions),
        "first_visit": patient_sessions[0]["created_at"] if patient_sessions else None,
        "last_visit": patient_sessions[-1]["updated_at"] if patient_sessions else None,
        "has_summary": cached is not None,
        "summary": cached["summary"] if cached else None,
        "summary_updated_at": cached["updated_at"] if cached else None,
    }

@app.post("/api/patients/{patient_name}/summary")
async def generate_or_update_summary(patient_name: str, username: str = Depends(get_current_user)):
    """สร้างหรืออัปเดต AI summary ของผู้ป่วย (เรียก LLM)"""
    # ดึงข้อมูลทั้งหมดของผู้ป่วย
    all_messages = sessions.get_patient_all_messages(patient_name, username)
    
    if not all_messages:
        raise HTTPException(status_code=404, detail="ไม่พบประวัติการสนทนาของผู้ป่วยนี้")

    # เรียก LLM สร้าง summary
    try:
        summary = await asyncio.to_thread(
            generate_patient_summary,
            patient_name=patient_name,
            messages=all_messages,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"เกิดข้อผิดพลาดในการสร้างสรุป: {str(e)}")

    # Cache summary
    sessions.save_summary(patient_name, username, summary)

    return {
        "patient_name": patient_name,
        "summary": summary,
        "summary_updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "PharmaCare AI",
        "version": "2.0.0",
    }
