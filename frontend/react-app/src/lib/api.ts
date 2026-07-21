import type {
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

export async function createSession(patientName: string): Promise<Session> {
  const r = await fetch('/api/sessions', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ patient_name: patientName, title: patientName }),
  });
  return r.json();
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
