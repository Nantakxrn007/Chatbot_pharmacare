import type {
  ChatModelOption,
  AdminChat,
  AdminOverview,
  AuditEvent,
  UserProfile,
  Drug,
  Me,
  Patient,
  PatientSession,
  PatientSummaryResponse,
  Session,
  TestCase,
  TestCaseResult,
  TokenSummary,
} from '../types';

export function getToken(): string | null {
  return localStorage.getItem('token');
}

export function authHeaders(): Record<string, string> {
  return {
    Authorization: `Bearer ${getToken()}`,
    'Content-Type': 'application/json',
  };
}

export function clearAuth() {
  localStorage.clear();
}

export async function fetchMe(): Promise<Me> {
  const r = await fetch('/api/me', { headers: authHeaders() });
  if (!r.ok) throw new Error('unauthorized');
  return r.json();
}

export async function login(username: string, password: string) {
  const r = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.detail || 'เข้าสู่ระบบไม่สำเร็จ');
  }
  return r.json();
}

export async function logout() {
  await fetch('/api/logout', { method: 'POST' });
}

export async function checkPatientName(name: string): Promise<boolean> {
  const r = await fetch(`/api/patients/check-name?name=${encodeURIComponent(name)}`, {
    headers: authHeaders(),
  });
  const data = await r.json();
  return !!data.exists;
}

export async function createSession(patientName: string, modelId?: string): Promise<Session> {
  const r = await fetch('/api/sessions', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ patient_name: patientName, title: patientName, model_id: modelId }),
  });
  return r.json();
}

export async function listChatModels(): Promise<{ default: string; models: ChatModelOption[] }> {
  const r = await fetch('/api/models', { headers: authHeaders() });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export async function downloadPatientHistory(patientName: string): Promise<void> {
  const r = await fetch(`/api/patients/${encodeURIComponent(patientName)}/export`, { headers: authHeaders() });
  if (!r.ok) throw new Error(String(r.status));
  const data = await r.json();

  // กัน Excel ตีความเซลล์ที่ขึ้นต้นด้วย = + - @ เป็นสูตร
  const cell = (v: unknown) => {
    let t = String(v ?? '');
    if (/^[=+\-@\t\r]/.test(t)) t = `'${t}`;
    return `"${t.replace(/"/g, '""')}"`;
  };
  const header = ['patient', 'session_id', 'session_title', 'model', 'session_created_at', 'message_time', 'role', 'content'];
  const rows: string[] = [header.join(',')];
  for (const s of data.sessions as Array<{
    session_id: string; title: string; model: string; created_at: string;
    messages: Array<{ role: string; content: string; timestamp: string }>;
  }>) {
    for (const m of s.messages) {
      rows.push([data.patient_name, s.session_id, s.title, s.model, s.created_at, m.timestamp, m.role, m.content].map(cell).join(','));
    }
  }

  const blob = new Blob(['\ufeff' + rows.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `chat-history-${patientName}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function listSessions(): Promise<Session[]> {
  const r = await fetch('/api/sessions', { headers: authHeaders() });
  if (!r.ok) return [];
  return r.json();
}

export async function getSession(id: string): Promise<Session> {
  const r = await fetch(`/api/sessions/${id}`, { headers: authHeaders() });
  return r.json();
}

export async function deleteSession(id: string) {
  await fetch(`/api/sessions/${id}`, { method: 'DELETE', headers: authHeaders() });
}

export async function renameSession(id: string, title: string) {
  await fetch(`/api/sessions/${id}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify({ title }),
  });
}

export async function searchSessions(q: string): Promise<Session[] | null> {
  const r = await fetch(`/api/sessions/search?q=${encodeURIComponent(q)}`, { headers: authHeaders() });
  if (!r.ok) return null;
  return r.json();
}

export async function fetchTokenSummary(month?: string): Promise<TokenSummary> {
  const url = month ? `/api/tokens/summary?month=${encodeURIComponent(month)}` : '/api/tokens/summary';
  const r = await fetch(url, { headers: authHeaders() });
  return r.json();
}

export function streamChat(path: '/api/chat/stream' | '/api/chat/edit' | '/api/chat/regenerate', body: object) {
  return fetch(path, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
}

export async function listPatients(): Promise<Patient[]> {
  const r = await fetch('/api/patients', { headers: authHeaders() });
  if (!r.ok) throw new Error('ไม่สามารถโหลดข้อมูลได้');
  return r.json();
}

export async function getPatientSummary(name: string): Promise<PatientSummaryResponse> {
  const r = await fetch(`/api/patients/${encodeURIComponent(name)}/summary`, { headers: authHeaders() });
  if (!r.ok) throw new Error('ไม่พบข้อมูลผู้ป่วย');
  return r.json();
}

export async function generatePatientSummary(name: string): Promise<PatientSummaryResponse> {
  const r = await fetch(`/api/patients/${encodeURIComponent(name)}/summary`, {
    method: 'POST',
    headers: authHeaders(),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || 'เกิดข้อผิดพลาด');
  }
  return r.json();
}

export async function getPatientSessions(name: string): Promise<PatientSession[]> {
  const r = await fetch(`/api/patients/${encodeURIComponent(name)}/sessions`, { headers: authHeaders() });
  if (!r.ok) throw new Error('ไม่พบข้อมูลผู้ป่วยนี้');
  return r.json();
}

export async function getDrugs(): Promise<Drug[]> {
  const r = await fetch('/api/drugs', { headers: authHeaders() });
  if (!r.ok) throw new Error('ไม่สามารถโหลดรายชื่อยาได้');
  const data = await r.json();
  if (!Array.isArray(data)) throw new Error('รูปแบบข้อมูลยาไม่ถูกต้อง');
  return data.filter((d): d is Drug => typeof d?.name === 'string' && d.name.trim() !== '');
}

export async function getTestCases(): Promise<TestCase[]> {
  const r = await fetch('/api/testcases', { headers: authHeaders() });
  if (!r.ok) throw new Error('ไม่สามารถโหลด test cases ได้');
  return r.json();
}

export async function runTestCase(tc: TestCase): Promise<TestCaseResult> {
  const r = await fetch('/api/testcases/run-one', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(tc),
  });
  return r.json();
}

export async function fetchAdminOverview(hours = 24): Promise<AdminOverview> {
  const r = await fetch(`/api/admin/overview?hours=${hours}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export async function fetchAuditEvents(params: Record<string, string>): Promise<AuditEvent[]> {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v));
  const r = await fetch(`/api/admin/audit?${qs}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export async function fetchAdminChats(): Promise<AdminChat[]> {
  const r = await fetch('/api/admin/chats?limit=50', { headers: authHeaders() });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export async function fetchProfile(username?: string): Promise<UserProfile> {
  const qs = username ? `?username=${encodeURIComponent(username)}` : '';
  const r = await fetch(`/api/profile${qs}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export async function saveProfile(body: { display_name: string; department: string }, username?: string): Promise<UserProfile> {
  const qs = username ? `?username=${encodeURIComponent(username)}` : '';
  const r = await fetch(`/api/profile${qs}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.detail || 'บันทึกไม่สำเร็จ');
  }
  return r.json();
}

export async function adminRequest(method: 'POST', path: string, body?: unknown): Promise<void> {
  const r = await fetch(path, { method, headers: authHeaders(), body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.detail || `เกิดข้อผิดพลาด (${r.status})`);
  }
}
