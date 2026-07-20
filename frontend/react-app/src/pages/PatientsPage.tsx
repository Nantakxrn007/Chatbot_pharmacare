import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { listPatients } from '../lib/api';
import type { Patient, RiskLevel } from '../types';
import '../styles/patients.css';

function fmtDate(iso?: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('th-TH', { year: 'numeric', month: 'short', day: 'numeric' });
}

const riskLabels: Record<RiskLevel, string> = {
  low: 'ต่ำ',
  medium: 'ปานกลาง',
  high: 'สูง',
  critical: 'วิกฤต',
};

const riskClasses: Record<RiskLevel, string> = {
  low: 'pp-card-risk-low',
  medium: 'pp-card-risk-medium',
  high: 'pp-card-risk-high',
  critical: 'pp-card-risk-critical',
};

export default function PatientsPage() {
  const navigate = useNavigate();
  const [patients, setPatients] = useState<Patient[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login', { replace: true });
      return;
    }
    listPatients()
      .then(setPatients)
      .catch(() => setError(true));
  }, [navigate]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (!q) return patients;
    return patients.filter((p) => (p.patient_name || '').toLowerCase().includes(q));
  }, [patients, query]);

  const totalSessions = patients.reduce((sum, p) => sum + (p.session_count || 0), 0);
  const withSummary = patients.filter((p) => p.has_summary).length;

  return (
    <div className="patients-page">
      <nav className="pp-top-nav">
        <Link to="/" className="pp-back-btn">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 18l-6-6 6-6" />
          </svg>
          กลับแชท
        </Link>
        <div>
          <div className="pp-nav-title">
            <svg width="17" height="17" fill="none" stroke="#1fae86" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
              <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 00-3-3.87" />
              <path d="M16 3.13a4 4 0 010 7.75" />
            </svg>
            รายชื่อผู้ป่วย
          </div>
          <div className="pp-nav-subtitle">Patient Directory</div>
        </div>
      </nav>

      <div className="pp-container">
        <div className="pp-stats-bar">
          <div className="pp-stat-card">
            <div className="pp-stat-value" style={{ color: '#1fae86' }}>{patients.length || 0}</div>
            <div className="pp-stat-label">ผู้ป่วยทั้งหมด</div>
          </div>
          <div className="pp-stat-card">
            <div className="pp-stat-value" style={{ color: '#2f6fbf' }}>{totalSessions || 0}</div>
            <div className="pp-stat-label">ครั้งที่ปรึกษา</div>
          </div>
          <div className="pp-stat-card">
            <div className="pp-stat-value" style={{ color: '#b5790a' }}>{withSummary || 0}</div>
            <div className="pp-stat-label">มี AI สรุปแล้ว</div>
          </div>
        </div>

        <div className="pp-search-section">
          <div className="pp-search-wrapper">
            <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <circle cx="11" cy="11" r="7" />
              <path strokeLinecap="round" strokeWidth="2" d="m21 21-4.3-4.3" />
            </svg>
            <input
              type="text"
              className="pp-search-input"
              placeholder="ค้นหาชื่อผู้ป่วย..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        </div>

        <div className="pp-patient-grid">
          {error ? (
            <div className="pp-empty-state" style={{ gridColumn: '1/-1' }}>
              <p>ไม่สามารถโหลดข้อมูลได้</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="pp-empty-state" style={{ gridColumn: '1/-1' }}>
              <h3>ยังไม่มีผู้ป่วยในระบบ</h3>
              <p>กลับไปหน้าแชทแล้วกดปุ่ม "เคสใหม่" เพื่อเริ่มบันทึกประวัติผู้ป่วย</p>
            </div>
          ) : (
            filtered.map((p, idx) => {
              const initial = (p.patient_name || '?')[0].toUpperCase();
              return (
                <Link
                  key={p.patient_name}
                  className="pp-patient-card pp-fade-in"
                  to={`/patient/${encodeURIComponent(p.patient_name)}`}
                  style={{ animationDelay: `${idx * 0.05}s` }}
                >
                  <div className="pp-card-header">
                    <div className="pp-card-avatar">{initial}</div>
                    <div>
                      <div className="pp-card-name">{p.patient_name}</div>
                      <div className="pp-card-visits">{p.session_count || 0} ครั้งที่ปรึกษา</div>
                    </div>
                  </div>
                  <div className="pp-card-stats">
                    <span className="pp-card-stat">
                      <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
                      </svg>
                      {p.total_messages || 0} ข้อความ
                    </span>
                    <span className="pp-card-stat">
                      <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <rect x="3" y="4" width="18" height="18" rx="2" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M16 2v4M8 2v4M3 10h18" />
                      </svg>
                      เริ่ม {fmtDate(p.first_visit)}
                    </span>
                  </div>
                  <div className="pp-card-footer">
                    <span className="pp-card-date">ล่าสุด: {fmtDate(p.last_visit)}</span>
                    <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                      {p.risk_level && (
                        <span className={`pp-card-risk ${riskClasses[p.risk_level] || ''}`}>
                          {riskLabels[p.risk_level] || p.risk_level}
                        </span>
                      )}
                      <span className={`pp-summary-badge${p.has_summary ? '' : ' pp-no-summary-badge'}`}>
                        {p.has_summary ? 'มี AI สรุป' : 'ยังไม่สรุป'}
                      </span>
                    </div>
                  </div>
                </Link>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
