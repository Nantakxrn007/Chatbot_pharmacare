import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import * as XLSX from 'xlsx';
import { generatePatientSummary, getPatientSessions, getPatientSummary } from '../lib/api';
import type { PatientSession, PatientSummaryData, RiskLevel } from '../types';
import AlertModal from '../components/AlertModal';
import '../styles/patient.css';

function fmtDate(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return (
    d.toLocaleDateString('th-TH', { year: 'numeric', month: 'short', day: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })
  );
}

function fmtDateShort(iso?: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('th-TH', { year: 'numeric', month: 'short', day: 'numeric' });
}

const riskLabels: Record<RiskLevel, string> = {
  low: '🟢 ความเสี่ยงต่ำ',
  medium: '🟡 ความเสี่ยงปานกลาง',
  high: '🔴 ความเสี่ยงสูง',
  critical: '🚨 วิกฤต — ต้องส่งต่อ',
};

const riskClasses: Record<RiskLevel, string> = {
  low: 'pt-risk-low',
  medium: 'pt-risk-medium',
  high: 'pt-risk-high',
  critical: 'pt-risk-critical',
};

export default function PatientDetailPage() {
  const { name } = useParams<{ name: string }>();
  const patientName = name || '';
  const navigate = useNavigate();
  const mainRef = useRef<HTMLDivElement>(null);

  const [firstVisit, setFirstVisit] = useState<string | null>(null);
  const [lastVisit, setLastVisit] = useState<string | null>(null);
  const [sessionCount, setSessionCount] = useState(0);
  const [summary, setSummary] = useState<PatientSummaryData | null>(null);
  const [summaryUpdatedAt, setSummaryUpdatedAt] = useState<string | null>(null);
  const [sessions, setSessions] = useState<PatientSession[] | null>(null);
  const [generating, setGenerating] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login', { replace: true });
      return;
    }
    document.title = `${patientName} — ประวัติผู้ป่วย — PharmaCare AI`;

    getPatientSummary(patientName)
      .then((data) => {
        setFirstVisit(data.first_visit);
        setLastVisit(data.last_visit);
        setSessionCount(data.session_count);
        if (data.has_summary && data.summary) {
          setSummary(data.summary);
          setSummaryUpdatedAt(data.summary_updated_at);
        }
      })
      .catch((e) => console.error(e));

    getPatientSessions(patientName)
      .then(setSessions)
      .catch(() => setSessions([]));
  }, [patientName, navigate]);

  const handleGenerateSummary = async () => {
    setGenerating(true);
    try {
      const data = await generatePatientSummary(patientName);
      setSummary(data.summary);
      setSummaryUpdatedAt(data.summary_updated_at);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'เกิดข้อผิดพลาดในการสร้างสรุป');
    } finally {
      setGenerating(false);
    }
  };

  const goToSession = (sessionId: string) => {
    navigate(`/?session=${sessionId}`);
  };

  const exportPDF = async () => {
    const element = mainRef.current;
    if (!element) return;
    setExporting(true);
    // Wait a tick so the exporting-pdf styles/hidden controls apply before capture.
    await new Promise((r) => setTimeout(r, 50));
    try {
      // html2pdf.js (html2canvas) draws Thai text glyph-by-glyph on a <canvas>
      // and doesn't handle combining vowel/tone marks — they render detached
      // from their base consonant. html-to-image instead serializes the DOM
      // into an SVG <foreignObject> and lets the browser's own text engine
      // draw it, so Thai (and any other complex script) comes out correct.
      const { toJpeg } = await import('html-to-image');
      const { jsPDF } = await import('jspdf');

      // .pt-container (this element) is centered with `margin: 0 auto` inside
      // a full-width parent. html-to-image doesn't always resolve that
      // centering correctly when cloning the node into an SVG <foreignObject>
      // — it can end up drawing the content shifted right, cut off at the
      // capture's right edge with blank space on the left. Pinning an exact
      // width/height and zeroing the margin on the clone removes that
      // ambiguity: the clone is always exactly as wide as its own content,
      // nothing to center.
      const captureWidth = element.scrollWidth;
      const captureHeight = element.scrollHeight;

      // Record each card's vertical span (in source pixels, relative to the
      // container) before capturing. The PDF is built by slicing one tall
      // image at fixed page-height intervals — with no awareness of card
      // boundaries, a slice can land mid-card, splitting it across two
      // pages (shows up as a hard seam plus torn/duplicated text at the
      // cut). We use these spans below to nudge each page break into the
      // gap before a card instead of through it.
      const cardTopOffset = element.getBoundingClientRect().top;
      const cardRangesPx = Array.from(element.querySelectorAll<HTMLElement>('.pt-card')).map((card) => {
        const r = card.getBoundingClientRect();
        return { top: r.top - cardTopOffset, bottom: r.bottom - cardTopOffset };
      });

      const dataUrl = await toJpeg(element, {
        quality: 0.98,
        backgroundColor: '#ffffff',
        pixelRatio: 2,
        width: captureWidth,
        height: captureHeight,
        style: { margin: '0', transform: 'none' },
      });

      const img = new Image();
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve();
        img.onerror = () => reject(new Error('โหลดภาพสำหรับสร้าง PDF ไม่สำเร็จ'));
        img.src = dataUrl;
      });

      const pdf = new jsPDF({ unit: 'in', format: 'a4', orientation: 'portrait' });
      const margin = 0.4;
      const pageWidth = 8.27;
      const pageHeight = 11.69;
      const usableWidth = pageWidth - margin * 2;
      const usableHeight = pageHeight - margin * 2;

      const imgWidthIn = usableWidth;
      const imgHeightIn = (img.height / img.width) * imgWidthIn;

      // Map the recorded card spans from source CSS pixels into the same
      // inches the page-break math below works in.
      const scale = imgHeightIn / captureHeight;
      const cardRangesIn = cardRangesPx.map((r) => ({ top: r.top * scale, bottom: r.bottom * scale }));

      // A naive break at exactly `position + usableHeight` doesn't know
      // where cards start and end — if it lands inside one, pull it back
      // to that card's top so the whole card moves to the next page
      // instead of being sliced in half.
      const findSafeBreak = (naive: number): number => {
        const capped = Math.min(naive, imgHeightIn);
        for (const range of cardRangesIn) {
          if (capped > range.top + 0.02 && capped < range.bottom - 0.02) {
            return range.top > position ? range.top : capped;
          }
        }
        return capped;
      };

      let heightLeft = imgHeightIn;
      let position = 0;

      pdf.addImage(dataUrl, 'JPEG', margin, margin - position, imgWidthIn, imgHeightIn);
      let nextBreak = findSafeBreak(position + usableHeight);
      heightLeft -= nextBreak - position;
      position = nextBreak;

      while (heightLeft > 0) {
        pdf.addPage();
        pdf.addImage(dataUrl, 'JPEG', margin, margin - position, imgWidthIn, imgHeightIn);
        nextBreak = findSafeBreak(position + usableHeight);
        heightLeft -= nextBreak - position;
        position = nextBreak;
      }

      pdf.save(`PharmaCare_Summary_${patientName}.pdf`);
    } finally {
      setExporting(false);
    }
  };

  const exportExcel = () => {
    if (!summary) return;
    const wb = XLSX.utils.book_new();

    const ws1 = XLSX.utils.aoa_to_sheet([
      ['หัวข้อ', 'รายละเอียด'],
      ['ชื่อผู้ป่วย', patientName],
      ['ความเสี่ยง', summary.risk_assessment?.level || 'N/A'],
      ['รายละเอียดความเสี่ยง', summary.risk_assessment?.description || ''],
      ['ภาพรวม', summary.overall_summary || ''],
    ]);
    ws1['!cols'] = [{ wch: 25 }, { wch: 100 }];
    XLSX.utils.book_append_sheet(wb, ws1, 'ข้อมูลทั่วไป');

    const listsData: string[][] = [['โรค/อาการ', 'ยาที่ได้รับ', 'ประวัติแพ้ยา']];
    const maxLen = Math.max(
      (summary.conditions || []).length,
      (summary.medications_given || []).length,
      (summary.allergies || []).length
    );
    for (let i = 0; i < maxLen; i++) {
      listsData.push([
        (summary.conditions || [])[i] || '',
        (summary.medications_given || [])[i] || '',
        (summary.allergies || [])[i] || '',
      ]);
    }
    const ws2 = XLSX.utils.aoa_to_sheet(listsData);
    ws2['!cols'] = [{ wch: 40 }, { wch: 40 }, { wch: 40 }];
    XLSX.utils.book_append_sheet(wb, ws2, 'ข้อมูลการรักษา');

    const timelineData: string[][] = [['วันที่', 'รายละเอียด']];
    (summary.timeline || []).forEach((t) => timelineData.push([t.date, t.summary]));
    const ws3 = XLSX.utils.aoa_to_sheet(timelineData);
    ws3['!cols'] = [{ wch: 20 }, { wch: 80 }];
    XLSX.utils.book_append_sheet(wb, ws3, 'ลำดับเวลา');

    const recData: string[][] = [['คำแนะนำ']];
    (summary.recommendations || []).forEach((r) => recData.push([r]));
    const ws4 = XLSX.utils.aoa_to_sheet(recData);
    ws4['!cols'] = [{ wch: 120 }];
    XLSX.utils.book_append_sheet(wb, ws4, 'คำแนะนำ');

    XLSX.writeFile(wb, `PharmaCare_Summary_${patientName}.xlsx`);
  };

  const initial = (patientName || '?')[0].toUpperCase();
  const risk = summary?.risk_assessment || {};
  const riskLevel = risk.level || 'low';

  return (
    <div className={`patient-page${exporting ? ' pt-exporting-pdf' : ''}`}>
      {!exporting && (
        <nav className="pt-top-nav">
          <div className="pt-nav-left">
            <Link to="/" className="pt-back-btn">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 18l-6-6 6-6" />
              </svg>
              กลับแชท
            </Link>
            <div>
              <div className="pt-nav-title">
                <svg width="17" height="17" fill="none" stroke="#1fae86" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                  <rect x="8" y="2" width="8" height="4" rx="1" />
                  <path d="M9 4H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-3" />
                </svg>
                ประวัติผู้ป่วย
              </div>
              <div className="pt-nav-subtitle">Patient Dashboard</div>
            </div>
          </div>
          <Link to="/patients" className="pt-back-btn">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
              <circle cx="9" cy="7" r="4" />
            </svg>
            รายชื่อทั้งหมด
          </Link>
        </nav>
      )}

      <div className="pt-container" id="mainContent" ref={mainRef}>
        <div className="pt-patient-header pt-fade-in">
          <div className="pt-patient-avatar">{initial}</div>
          <div className="pt-patient-info">
            <div className="pt-patient-name">{patientName}</div>
            <div className="pt-patient-meta">
              <span className="pt-meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                เริ่มปรึกษา: {fmtDateShort(firstVisit)}
              </span>
              <span className="pt-meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                ล่าสุด: {fmtDateShort(lastVisit)}
              </span>
              <span className="pt-meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                {sessionCount} ครั้ง
              </span>
            </div>
          </div>
        </div>

        {!exporting && (
          <div className="pt-card pt-card-full" style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
              <div>
                <div className="pt-card-title">
                  <svg width="15" height="15" fill="none" stroke="#1fae86" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                    <rect x="8" y="2" width="8" height="4" rx="1" />
                    <path d="M9 4H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-3" />
                  </svg>
                  AI สรุปประวัติผู้ป่วย
                </div>
                <div className="pt-update-info">
                  {summaryUpdatedAt ? `อัปเดตล่าสุด: ${fmtDate(summaryUpdatedAt)}` : 'ยังไม่ได้สร้างสรุป'}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                {summary && (
                  <>
                    <button className="pt-export-btn pt-export-btn-pdf" onClick={exportPDF}>
                      <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M14 2v6h6" />
                      </svg>
                      โหลด PDF
                    </button>
                    <button className="pt-export-btn pt-export-btn-excel" onClick={exportExcel}>
                      <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M14 2v6h6" />
                      </svg>
                      โหลด Excel
                    </button>
                  </>
                )}
                <button className="pt-generate-btn" disabled={generating} onClick={handleGenerateSummary}>
                  <svg width="13" height="13" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M12 2l1.8 5.6L19 9.4l-5.2 1.8L12 17l-1.8-5.8L5 9.4l5.2-1.8z" />
                  </svg>
                  <span>{summary ? 'อัปเดตสรุป' : 'สร้างสรุป AI'}</span>
                </button>
              </div>
            </div>
            <div className={`pt-loading-bar${generating ? ' show' : ''}`}>
              <div className="pt-loading-spinner" />
              <div className="pt-loading-text">กำลังประมวลผล AI Summary... อาจใช้เวลาสักครู่</div>
            </div>
          </div>
        )}

        {summary && (
          <div>
            <div className="pt-card pt-card-full" style={{ marginBottom: '1rem' }}>
              {summary.data_sufficient === false && (
                <div className="pt-data-note">ข้อมูลยังไม่เพียงพอ — สรุปนี้อ้างอิงจากข้อมูลเบื้องต้นที่มีอยู่เท่านั้น</div>
              )}
              <div className="pt-card-title">การประเมินความเสี่ยง</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
                <span className={`pt-risk-badge ${riskClasses[riskLevel] || 'pt-risk-low'}`}>
                  {riskLabels[riskLevel] || riskLevel}
                </span>
              </div>
              <p style={{ fontSize: '0.85rem', color: '#475569', lineHeight: 1.6, marginBottom: '0.25rem' }}>
                {risk.description || ''}
              </p>
              {!!risk.factors?.length && (
                <div className="pt-risk-factors">
                  {risk.factors.map((f, i) => (
                    <div className="pt-risk-factor" key={i}>
                      <span className={`pt-risk-factor-dot ${riskLevel}`} />
                      {f}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="pt-cards-grid">
              <div className="pt-card pt-fade-in">
                <div className="pt-card-title">โรค/อาการที่พบ</div>
                <div className="pt-tag-list">
                  {summary.conditions?.length ? (
                    summary.conditions.map((c, i) => <span className="pt-tag pt-tag-condition" key={i}>{c}</span>)
                  ) : (
                    <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>ไม่พบข้อมูล</span>
                  )}
                </div>
              </div>
              <div className="pt-card pt-fade-in">
                <div className="pt-card-title">ยาที่แนะนำ/จ่ายแล้ว</div>
                <div className="pt-tag-list">
                  {summary.medications_given?.length ? (
                    summary.medications_given.map((m, i) => <span className="pt-tag pt-tag-med" key={i}>{m}</span>)
                  ) : (
                    <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>ไม่พบข้อมูล</span>
                  )}
                </div>
              </div>
              <div className="pt-card pt-fade-in">
                <div className="pt-card-title">ประวัติแพ้ยา</div>
                <div className="pt-tag-list">
                  {summary.allergies?.length ? (
                    summary.allergies.map((a, i) => <span className="pt-tag pt-tag-allergy" key={i}>{a}</span>)
                  ) : (
                    <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>ไม่มีประวัติแพ้ยา</span>
                  )}
                </div>
              </div>
              <div className="pt-card pt-fade-in" style={{ display: 'flex', flexDirection: 'column' }}>
                <div className="pt-card-title">สถิติ</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', flex: 1 }}>
                  <div style={{ textAlign: 'center', padding: '0.5rem', borderRadius: 8, background: '#f8fafc' }}>
                    <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#10b981' }}>{(summary.conditions || []).length}</div>
                    <div style={{ fontSize: '0.68rem', color: '#94a3b8' }}>อาการ</div>
                  </div>
                  <div style={{ textAlign: 'center', padding: '0.5rem', borderRadius: 8, background: '#f8fafc' }}>
                    <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#0891b2' }}>{(summary.medications_given || []).length}</div>
                    <div style={{ fontSize: '0.68rem', color: '#94a3b8' }}>รายการยา</div>
                  </div>
                </div>
              </div>
            </div>

            <div className="pt-card pt-card-full" style={{ marginBottom: '1rem' }}>
              <div className="pt-card-title">สรุปภาพรวม</div>
              <p className="pt-summary-text">{summary.overall_summary || 'ไม่มีข้อมูล'}</p>
            </div>

            <div className="pt-card pt-card-full" style={{ marginBottom: '1rem' }}>
              <div className="pt-card-title">ลำดับเวลา</div>
              {summary.timeline?.length ? (
                <div className="pt-timeline">
                  {summary.timeline.map((t, i) => (
                    <div className="pt-timeline-item pt-fade-in" key={i}>
                      <div className="pt-timeline-dot" />
                      <div className="pt-timeline-date">{t.date}</div>
                      <div className="pt-timeline-text">{t.summary}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <p style={{ color: '#94a3b8', fontSize: '0.85rem' }}>ไม่มีข้อมูลลำดับเวลา</p>
              )}
            </div>

            <div className="pt-card pt-card-full" style={{ marginBottom: '1.5rem' }}>
              <div className="pt-card-title">คำแนะนำการติดตาม</div>
              {summary.recommendations?.length ? (
                <ul className="pt-rec-list">
                  {summary.recommendations.map((r, i) => (
                    <li className="pt-rec-item" key={i}>
                      <svg className="pt-rec-icon" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                        <path d="M9 12l2 2 4-4" />
                        <circle cx="12" cy="12" r="9" />
                      </svg>
                      <span>{r}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p style={{ color: '#94a3b8', fontSize: '0.85rem' }}>ไม่มีคำแนะนำ</p>
              )}
            </div>
          </div>
        )}

        <div className="pt-card pt-card-full">
          <div className="pt-card-title">
            <svg width="15" height="15" fill="none" stroke="#1fae86" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
              <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
            </svg>
            ประวัติการสนทนา
          </div>
          <div>
            {sessions === null ? null : sessions.length === 0 ? (
              <div className="pt-empty-state">
                <p>ยังไม่มีประวัติการสนทนา</p>
              </div>
            ) : (
              sessions.map((s) => (
                <a
                  key={s.id}
                  className="pt-session-link"
                  href="/"
                  onClick={(e) => {
                    e.preventDefault();
                    goToSession(s.id);
                  }}
                >
                  <div className="pt-session-link-icon">{(s.title || '?').charAt(0).toUpperCase()}</div>
                  <div style={{ flex: 1 }}>
                    <div className="pt-session-link-title">{s.title}</div>
                    <div className="pt-session-link-meta">{fmtDate(s.created_at)} · {s.message_count || 0} ข้อความ</div>
                  </div>
                  <svg width="14" height="14" fill="none" stroke="#94a3b8" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                  </svg>
                </a>
              ))
            )}
          </div>
        </div>
      </div>

      <AlertModal open={errorMsg !== null} title="เกิดข้อผิดพลาด" message={errorMsg || ''} onClose={() => setErrorMsg(null)} />
    </div>
  );
}
