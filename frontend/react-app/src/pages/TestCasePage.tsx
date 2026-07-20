import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getTestCases, runTestCase } from '../lib/api';
import { renderTestCaseMarkdown } from '../lib/testcaseMarkdown';
import type { TestCase, TestCaseResult } from '../types';
import AlertModal from '../components/AlertModal';
import ConfirmModal from '../components/ConfirmModal';
import '../styles/testcase.css';

const RESULTS_STORAGE_KEY = 'pharmacare_test_results';

function loadStoredResults(): Record<string, TestCaseResult> {
  try {
    const saved = localStorage.getItem(RESULTS_STORAGE_KEY);
    return saved ? JSON.parse(saved) : {};
  } catch {
    return {};
  }
}

function caseColorClass(tcCase: string): string {
  if (tcCase === 'ง่าย') return 'tc-case-easy';
  if (tcCase === 'ปานกลาง' || tcCase === 'กลาง') return 'tc-case-mid';
  if (tcCase === 'ยาก') return 'tc-case-hard';
  return 'tc-case-other';
}

function escapeCsv(str: unknown): string {
  if (str === null || str === undefined) return '';
  let s = String(str);
  s = s.replace(/"/g, '""');
  if (s.search(/("|,|\n)/g) >= 0) s = `"${s}"`;
  return s;
}

export default function TestCasePage() {
  const navigate = useNavigate();
  const [testCases, setTestCases] = useState<TestCase[]>([]);
  const [results, setResults] = useState<Record<string, TestCaseResult>>({});
  const [loadError, setLoadError] = useState<string | null>(null);

  const [isRunning, setIsRunning] = useState(false);
  const shouldStopRef = useRef(false);

  const [filterCase, setFilterCase] = useState('all');
  const [filterResult, setFilterResult] = useState('all');

  const [progress, setProgress] = useState<{ current: number; total: number; eta: string; text: string } | null>(null);

  const [detailId, setDetailId] = useState<string | null>(null);
  const [runningSingleId, setRunningSingleId] = useState<string | null>(null);
  const [alertMsg, setAlertMsg] = useState<string | null>(null);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login', { replace: true });
      return;
    }
    setResults(loadStoredResults());
    getTestCases()
      .then(setTestCases)
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, [navigate]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDetailId(null);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  const filteredCases = useMemo(() => {
    return testCases.filter((tc) => {
      if (filterCase !== 'all' && tc.case !== filterCase) return false;
      if (filterResult !== 'all') {
        const r = results[tc.id];
        if (filterResult === 'pass' && (!r || !r.pass)) return false;
        if (filterResult === 'fail' && (!r || r.pass)) return false;
        if (filterResult === 'pending' && r) return false;
      }
      return true;
    });
  }, [testCases, filterCase, filterResult, results]);

  const stats = useMemo(() => {
    const completed = filteredCases.filter((tc) => results[tc.id]);
    const passed = completed.filter((tc) => results[tc.id]?.pass);
    const failed = completed.filter((tc) => results[tc.id] && !results[tc.id].pass);
    const cosines = completed.map((tc) => results[tc.id]?.cosine || 0);
    const avgCosine = cosines.length > 0 ? cosines.reduce((a, b) => a + b, 0) / cosines.length : 0;
    return {
      total: filteredCases.length,
      pass: passed.length,
      fail: failed.length,
      avgCosine: avgCosine > 0 ? avgCosine.toFixed(4) : '—',
    };
  }, [filteredCases, results]);

  const pendingCount = filteredCases.filter((tc) => !results[tc.id]).length;
  const runBtnText =
    pendingCount > 0 && pendingCount < filteredCases.length
      ? `รันต่อ (${pendingCount} เคสที่เหลือ)`
      : 'รัน Test Cases ทั้งหมด';

  const saveResults = (next: Record<string, TestCaseResult>) => {
    localStorage.setItem(RESULTS_STORAGE_KEY, JSON.stringify(next));
  };

  const runSingleCase = async (tc: TestCase): Promise<TestCaseResult> => {
    let result: TestCaseResult;
    try {
      result = await runTestCase(tc);
    } catch (e) {
      result = {
        id: tc.id,
        input: tc.input,
        expectation: tc.expectation,
        prediction: `Error: ${e instanceof Error ? e.message : String(e)}`,
        cosine: 0,
        llm_score: 0,
        pass: false,
      };
    }
    setResults((prev) => {
      const next = { ...prev, [tc.id]: result };
      saveResults(next);
      return next;
    });
    return result;
  };

  const updateProgress = (current: number, total: number, startTime: number, textOverride?: string) => {
    const pct = total > 0 ? (current / total) * 100 : 0;
    let eta = '—';
    if (current > 0) {
      const elapsed = (Date.now() - startTime) / 1000;
      const avgTime = elapsed / current;
      const remaining = (total - current) * avgTime;
      const min = Math.floor(remaining / 60);
      const sec = Math.floor(remaining % 60);
      eta = `${min}m ${sec}s`;
    }
    setProgress({ current, total, eta, text: textOverride || `กำลังรัน... ${current}/${total}` });
    void pct;
  };

  const delayWithProgress = async (seconds: number, current: number, total: number, startTime: number) => {
    for (let s = seconds; s > 0; s--) {
      if (shouldStopRef.current) break;
      setProgress((prev) => ({ ...(prev || { current, total, eta: '—' }), text: `รอ ${s} วิ ป้องกัน Rate Limit... (${current}/${total})` }));
      await new Promise((r) => setTimeout(r, 1000));
    }
    updateProgress(current, total, startTime);
  };

  const runAllTests = async () => {
    if (isRunning) return;
    setIsRunning(true);
    shouldStopRef.current = false;

    const startTime = Date.now();
    const casesToRun = filteredCases.filter((tc) => !results[tc.id]);
    let ranAny = false;

    if (casesToRun.length === 0) {
      performReset();
      const all = filteredCases;
      for (let i = 0; i < all.length; i++) {
        if (shouldStopRef.current) break;
        updateProgress(i, all.length, startTime);
        await runSingleCase(all[i]);
        ranAny = true;
        if (i < all.length - 1 && !shouldStopRef.current) {
          await delayWithProgress(15, i + 1, all.length, startTime);
        }
      }
      updateProgress(all.length, all.length, startTime);
    } else {
      for (let i = 0; i < casesToRun.length; i++) {
        if (shouldStopRef.current) break;
        updateProgress(i, casesToRun.length, startTime);
        await runSingleCase(casesToRun[i]);
        ranAny = true;
        if (i < casesToRun.length - 1 && !shouldStopRef.current) {
          await delayWithProgress(15, i + 1, casesToRun.length, startTime);
        }
      }
      updateProgress(casesToRun.length, casesToRun.length, startTime);
    }

    setIsRunning(false);
    if (!shouldStopRef.current && ranAny) {
      downloadCSV();
    }
  };

  const stopTests = () => {
    shouldStopRef.current = true;
  };

  const runSingleCaseWithUI = async (id: string) => {
    if (isRunning) {
      setAlertMsg('กรุณารอให้การรันทั้งหมดเสร็จสิ้นหรือกดหยุดก่อน');
      return;
    }
    const tc = testCases.find((t) => t.id === id);
    if (!tc) return;

    setResults((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });

    setRunningSingleId(id);
    setProgress({ current: 1, total: 1, eta: '—', text: `กำลังรันเคส ${id}...` });

    await runSingleCase(tc);

    setProgress({ current: 1, total: 1, eta: '—', text: `รันเคส ${id} เสร็จสมบูรณ์` });
    setTimeout(() => {
      setRunningSingleId(null);
      setProgress(null);
    }, 3000);
  };

  const performReset = () => {
    setResults({});
    saveResults({});
    setProgress(null);
  };

  const requestReset = () => setResetConfirmOpen(true);

  const downloadCSV = () => {
    if (!testCases.length) return;
    const headers = ['ID', 'Input', 'Case', 'Expectation', 'Prediction', 'Cosine Similarity', 'LLM Score', 'Pass', 'LLM Reasoning'];
    const rows = [headers.join(',')];

    for (const tc of testCases) {
      const r = results[tc.id];
      const row = [
        tc.id,
        escapeCsv(tc.input),
        escapeCsv(tc.case),
        escapeCsv(tc.expectation),
        escapeCsv(r?.prediction ?? ''),
        r?.cosine ?? '',
        r?.llm_score ?? '',
        r ? (r.pass ? 'PASS' : 'FAIL') : 'PENDING',
        escapeCsv(r?.llm_reasoning ?? ''),
      ];
      rows.push(row.join(','));
    }

    const csvString = '﻿' + rows.join('\n');
    const blob = new Blob([csvString], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `test_results_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const accuracyGroups = useMemo(() => {
    const groups: Record<string, { total: number; pass: number; cosines: number[] }> = {};
    testCases.forEach((tc) => {
      const caseType = tc.case || 'ไม่ระบุ';
      if (!groups[caseType]) groups[caseType] = { total: 0, pass: 0, cosines: [] };
      groups[caseType].total++;
      const r = results[tc.id];
      if (r) {
        if (r.pass) groups[caseType].pass++;
        groups[caseType].cosines.push(r.cosine);
      }
    });
    return groups;
  }, [testCases, results]);

  const hasAnyResults = Object.keys(results).length > 0;
  const groupColors: Record<string, string> = {
    ง่าย: '#1fae86',
    กลาง: '#d9a441',
    ปานกลาง: '#d9a441',
    ยาก: '#c05f54',
  };

  const detailTc = detailId ? testCases.find((t) => t.id === detailId) : null;
  const detailResult = detailId ? results[detailId] : null;

  return (
    <div className="tc-page">
      <header className="tc-header">
        <div className="tc-header-left">
          <div className="tc-header-icon">
            <span className="material-symbols-rounded" style={{ fontSize: 18, color: '#fff', fontVariationSettings: "'FILL' 1" }}>local_pharmacy</span>
          </div>
          <div>
            <div className="tc-header-title">Test Case Dashboard</div>
            <div className="tc-header-subtitle">ประเมินความแม่นยำของระบบ</div>
          </div>
        </div>
        <Link to="/" className="tc-btn tc-btn-outline">
          <svg width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
            <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
          </svg>
          กลับหน้า Chat
        </Link>
      </header>

      <main className="tc-main">
        <div className="tc-stats-bar">
          <div className="tc-stat-card">
            <div className="tc-stat-label">Total Cases</div>
            <div className="tc-stat-value" style={{ color: '#1a2e2a' }}>{testCases.length || 0}</div>
          </div>
          <div className="tc-stat-card">
            <div className="tc-stat-label">Pass (LLM ≥3)</div>
            <div className="tc-stat-value" style={{ color: '#1fae86' }}>{stats.pass}</div>
          </div>
          <div className="tc-stat-card">
            <div className="tc-stat-label">Fail (LLM &lt;3)</div>
            <div className="tc-stat-value" style={{ color: '#b0453b' }}>{stats.fail}</div>
          </div>
          <div className="tc-stat-card">
            <div className="tc-stat-label">Avg Cosine</div>
            <div className="tc-stat-value" style={{ color: '#2f6fbf' }}>{stats.avgCosine}</div>
          </div>
        </div>

        <div className="tc-controls">
          <div className="tc-controls-left">
            <button onClick={runAllTests} disabled={isRunning} className="tc-btn tc-btn-primary">
              <svg width="13" height="13" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" viewBox="0 0 24 24">
                <path d="M12 5v14M5 12h14" />
              </svg>
              <span>{runBtnText}</span>
            </button>
            {isRunning && (
              <button onClick={stopTests} className="tc-btn tc-btn-danger">
                <svg width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
                </svg>
                หยุด
              </button>
            )}
            <button onClick={downloadCSV} className="tc-btn tc-btn-outline">
              <svg width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                <path d="M12 4v12M7 11l5 5 5-5" />
                <path d="M4 20h16" />
              </svg>
              Export CSV
            </button>
            <button onClick={requestReset} className="tc-btn tc-btn-outline">
              <svg width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                <path d="M23 4v6h-6" />
                <path d="M1 20v-6h6" />
                <path d="M3.5 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.5 15" />
              </svg>
              รีเซ็ต
            </button>
          </div>
          <div className="tc-controls-right">
            <select className="tc-select" value={filterCase} onChange={(e) => setFilterCase(e.target.value)}>
              <option value="all">ทุก Case</option>
              <option value="ง่าย">ง่าย</option>
              <option value="กลาง">กลาง</option>
              <option value="ปานกลาง">ปานกลาง</option>
              <option value="ยาก">ยาก</option>
            </select>
            <select className="tc-select" value={filterResult} onChange={(e) => setFilterResult(e.target.value)}>
              <option value="all">ทุกผลลัพธ์</option>
              <option value="pass">Pass</option>
              <option value="fail">Fail</option>
              <option value="pending">รอรัน</option>
            </select>
          </div>
        </div>

        {progress && (
          <div className="tc-progress-card">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ fontSize: 12, color: '#4a6660' }}>{progress.text}</span>
              <span style={{ fontSize: 12, color: '#8aaba5' }}>ETA: {progress.eta}</span>
            </div>
            <div className="tc-progress-bar-track">
              <div
                className="tc-progress-bar-fill"
                style={{ width: `${progress.total > 0 ? (progress.current / progress.total) * 100 : 0}%` }}
              />
            </div>
          </div>
        )}

        <div className="tc-table-card">
          <div style={{ overflowX: 'auto' }}>
            <table className="tc-table">
              <thead>
                <tr>
                  <th style={{ width: 48 }}>#</th>
                  <th>Input (คำถาม)</th>
                  <th style={{ width: 80 }}>Case</th>
                  <th style={{ width: 96, textAlign: 'center' }}>Cosine</th>
                  <th style={{ width: 96, textAlign: 'center' }}>LLM Score</th>
                  <th style={{ width: 80, textAlign: 'center' }}>Result</th>
                  <th style={{ width: 64, textAlign: 'center' }}>Detail</th>
                </tr>
              </thead>
              <tbody>
                {loadError ? (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: '3rem 0', color: '#b0453b' }}>ไม่สามารถโหลด test cases ได้: {loadError}</td></tr>
                ) : testCases.length === 0 ? (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: '3rem 0', color: '#8aaba5' }}>กำลังโหลด test cases...</td></tr>
                ) : filteredCases.length === 0 ? (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: '3rem 0', color: '#8aaba5' }}>ไม่มี test case ที่ตรงตามเงื่อนไข</td></tr>
                ) : (
                  filteredCases.map((tc) => {
                    const r = results[tc.id];
                    const cosine = r ? r.cosine : null;
                    const llmScore = r ? r.llm_score : null;
                    const pass = r ? r.pass : null;
                    return (
                      <tr key={tc.id} className="tc-fade-in">
                        <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{tc.id}</td>
                        <td>
                          <p style={{ fontSize: 12, lineHeight: 1.5, margin: 0, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                            {tc.input.substring(0, 120)}{tc.input.length > 120 ? '...' : ''}
                          </p>
                        </td>
                        <td>
                          <span className={`tc-case-badge ${caseColorClass(tc.case)}`}>{tc.case || '—'}</span>
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          {cosine !== null ? (
                            <span className={`${cosine >= 0.79 ? 'tc-score-high' : cosine >= 0.7 ? 'tc-score-mid' : 'tc-score-low'}`} style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 600 }}>{cosine.toFixed(4)}</span>
                          ) : (
                            <span style={{ fontSize: 12, color: '#c3d0cb' }}>—</span>
                          )}
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          {llmScore !== null ? (
                            <span className={`${llmScore >= 4 ? 'tc-score-high' : llmScore >= 3 ? 'tc-score-mid' : 'tc-score-low'}`} style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 600 }}>{llmScore}/5</span>
                          ) : (
                            <span style={{ fontSize: 12, color: '#c3d0cb' }}>—</span>
                          )}
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          {pass === true ? (
                            <span className="tc-result-badge tc-result-pass">✓ Pass</span>
                          ) : pass === false ? (
                            <span className="tc-result-badge tc-result-fail">✗ Fail</span>
                          ) : (
                            <span className="tc-result-pending">รอรัน</span>
                          )}
                        </td>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                            <button
                              onClick={() => runSingleCaseWithUI(tc.id)}
                              disabled={runningSingleId === tc.id}
                              className="tc-row-icon-btn"
                              title="รันใหม่"
                            >
                              <svg width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                                <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                              </svg>
                            </button>
                            <button onClick={() => setDetailId(tc.id)} className="tc-row-icon-btn" title="ดูรายละเอียด">
                              <svg width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                                <path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                <path d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                              </svg>
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {hasAnyResults && (
          <div className="tc-accuracy-card">
            <div className="tc-accuracy-title">สรุปผลตาม Case Difficulty</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {Object.entries(accuracyGroups).map(([type, data]) => {
                const accuracy = data.total > 0 ? (data.pass / data.total) * 100 : 0;
                const avgCos = data.cosines.length > 0 ? data.cosines.reduce((a, b) => a + b, 0) / data.cosines.length : 0;
                const color = groupColors[type] || '#8aaba5';
                return (
                  <div className="tc-fade-in" key={type}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ fontSize: 12, fontWeight: 500, color: '#1a2e2a' }}>{type} ({data.total} cases)</span>
                      <span style={{ fontSize: 11, color: '#8aaba5' }}>Accuracy: {accuracy.toFixed(1)}% | Avg Cosine: {avgCos.toFixed(4)}</span>
                    </div>
                    <div className="tc-accuracy-bar-track">
                      <div className="tc-accuracy-bar-fill" style={{ width: `${accuracy}%`, background: color }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </main>

      {detailId && (
        <div className="tc-modal-overlay">
          <div className="tc-modal-backdrop" onClick={() => setDetailId(null)} />
          <div className="tc-modal">
            <div className="tc-modal-header">
              <div className="tc-modal-title">Test Case #{detailId} — {detailTc?.case || ''}</div>
              <button onClick={() => setDetailId(null)} className="tc-modal-close">
                <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                  <path d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="tc-modal-body">
              <div>
                <div className="tc-block-label">Input (คำถาม)</div>
                <div className="tc-block tc-block-neutral">{detailTc?.input || ''}</div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div>
                  <div className="tc-block-label">
                    <svg width="14" height="14" fill="none" stroke="#1fae86" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                      <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    Expectation (คาดหวัง)
                  </div>
                  <div
                    className="tc-block tc-block-expect"
                    style={{ maxHeight: 256, overflowY: 'auto' }}
                    dangerouslySetInnerHTML={{ __html: renderTestCaseMarkdown(detailTc?.expectation || '') }}
                  />
                </div>

                {detailResult ? (
                  <div>
                    <div className="tc-block-label">
                      <svg width="14" height="14" fill="none" stroke="#2f6fbf" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                        <path d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                      </svg>
                      Prediction (คำตอบ AI)
                    </div>
                    <div
                      className="tc-block tc-block-predict"
                      style={{ maxHeight: 256, overflowY: 'auto' }}
                      dangerouslySetInnerHTML={{ __html: renderTestCaseMarkdown(detailResult.prediction || '') }}
                    />
                  </div>
                ) : (
                  <div>
                    <div className="tc-block-label">Prediction</div>
                    <div className="tc-block tc-block-empty">ยังไม่ได้รัน</div>
                  </div>
                )}
              </div>

              {detailResult && (
                <>
                  <div className="tc-metric-grid">
                    <div className="tc-metric-card">
                      <div className="tc-metric-label">LLM Score (เต็ม 5)</div>
                      <div className={`tc-metric-value ${detailResult.llm_score >= 4 ? 'tc-score-high' : detailResult.llm_score >= 3 ? 'tc-score-mid' : 'tc-score-low'}`}>{detailResult.llm_score || '0'}/5</div>
                    </div>
                    <div className="tc-metric-card">
                      <div className="tc-metric-label">Cosine Similarity</div>
                      <div className={`tc-metric-value ${detailResult.cosine >= 0.79 ? 'tc-score-high' : detailResult.cosine >= 0.7 ? 'tc-score-mid' : 'tc-score-low'}`}>{detailResult.cosine?.toFixed(4) || '—'}</div>
                    </div>
                    <div className="tc-metric-card">
                      <div className="tc-metric-label">Result (Pass=LLM≥3)</div>
                      <div className="tc-metric-value" style={{ color: detailResult.pass ? '#1fae86' : '#b0453b' }}>{detailResult.pass ? '✓ PASS' : '✗ FAIL'}</div>
                    </div>
                  </div>

                  <div className="tc-reasoning-block">
                    <div className="tc-block-label">
                      <svg width="14" height="14" fill="none" stroke="#8a6fbf" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                        <path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      เหตุผลจาก LLM (LLM Reasoning)
                    </div>
                    <p className="tc-reasoning-text">{detailResult.llm_reasoning || 'ไม่มีเหตุผล'}</p>
                  </div>

                  {!!detailResult.sources?.length && (
                    <div>
                      <div className="tc-block-label">Sources Used</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {detailResult.sources.map((s, i) => (
                          <span key={i} className="tc-source-pill">📄 {s.source} p.{s.page}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      <AlertModal open={alertMsg !== null} message={alertMsg || ''} onClose={() => setAlertMsg(null)} />
      <ConfirmModal
        open={resetConfirmOpen}
        title="รีเซ็ตผลลัพธ์ทั้งหมด?"
        description="ผลการรัน test case ทั้งหมดที่บันทึกไว้จะถูกล้าง ไม่สามารถกู้คืนได้"
        confirmLabel="รีเซ็ต"
        danger
        onClose={() => setResetConfirmOpen(false)}
        onConfirm={() => {
          setResetConfirmOpen(false);
          performReset();
        }}
      />
    </div>
  );
}
