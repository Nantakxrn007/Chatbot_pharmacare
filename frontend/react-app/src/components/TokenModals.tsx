import { useEffect, useState } from 'react';
import { fetchTokenSummary } from '../lib/api';
import type { TokenSummary } from '../types';

const USD_TO_THB = 35;
const PROMPT_RATE_PER_M = 0.25;
const COMPLETION_RATE_PER_M = 1.5;

function costThb(prompt: number, completion: number): number {
  return ((prompt / 1_000_000) * PROMPT_RATE_PER_M + (completion / 1_000_000) * COMPLETION_RATE_PER_M) * USD_TO_THB;
}

interface CurrentProps {
  open: boolean;
  onClose: () => void;
  promptTokens: number;
  completionTokens: number;
}

export function TokenSummaryModal({ open, onClose, promptTokens, completionTokens }: CurrentProps) {
  const total = promptTokens + completionTokens;
  const cost = costThb(promptTokens, completionTokens);

  return (
    <div className={`modal-overlay${open ? ' show' : ''}`}>
      <div className="modal">
        <h3>💰 สรุป Token (แชทปัจจุบัน)</h3>
        <p style={{ fontSize: '0.85rem', color: '#64748b' }}>
          (อิงตามเรท Gemini 3.1 Flash-Lite : $0.25/1M Input, $1.50/1M Output | อัตราแลกเปลี่ยน 35 บาท/USD)
        </p>
        <div style={{ background: '#f8fafc', padding: 15, borderRadius: 8, marginTop: 15, border: '1px solid #e2e8f0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <span>📥 ข้อมูลเข้า (Prompt) :</span>
            <strong>{promptTokens.toLocaleString()} Tokens</strong>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <span>📤 ข้อมูลออก (Completion) :</span>
            <strong>{completionTokens.toLocaleString()} Tokens</strong>
          </div>
          <hr style={{ border: 'none', borderTop: '1px solid #e2e8f0', margin: '10px 0' }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontWeight: 600, color: '#10b981' }}>
            <span>รวมทั้งหมด :</span>
            <strong>{total.toLocaleString()} Tokens</strong>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '1.1rem', fontWeight: 700, color: '#f59e0b', marginTop: 5 }}>
            <span>ประมาณการค่าใช้จ่าย :</span>
            <strong>฿{cost.toFixed(4)}</strong>
          </div>
        </div>
        <div className="modal-actions" style={{ justifyContent: 'center', marginTop: 20 }}>
          <button className="modal-btn modal-btn-ok" onClick={onClose}>ปิดหน้าต่าง</button>
        </div>
      </div>
    </div>
  );
}

interface GlobalProps {
  open: boolean;
  onClose: () => void;
}

function currentMonthValue(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

export function GlobalTokenModal({ open, onClose }: GlobalProps) {
  const [month, setMonth] = useState(currentMonthValue());
  const [data, setData] = useState<TokenSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(false);
    fetchTokenSummary(month)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, month]);

  const totalTokens = data ? data.total_prompt + data.total_completion : 0;
  const totalCost = data ? costThb(data.total_prompt, data.total_completion) : 0;

  return (
    <div className={`modal-overlay${open ? ' show' : ''}`}>
      <div className="modal" style={{ maxWidth: 600, width: '90%', maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}>
        <div className="flex justify-between items-center mb-4">
          <div className="flex items-center gap-3">
            <h3 className="m-0 text-xl font-bold text-gray-800">📊 ภาพรวม Token ทั้งระบบ</h3>
            <input
              type="month"
              value={month}
              onChange={(e) => setMonth(e.target.value)}
              className="text-sm px-2 py-1 border border-gray-300 rounded-md focus:outline-none focus:border-emerald-500 text-gray-600 bg-white shadow-sm cursor-pointer"
            />
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="flex justify-between items-center bg-gradient-to-r from-emerald-50 to-teal-50 p-4 rounded-xl border border-emerald-100 mb-4">
          <div>
            <div className="text-sm text-gray-500 mb-1">Token รวมทั้งหมด</div>
            <div className="text-2xl font-bold text-emerald-700">
              {loading ? 'กำลังโหลด...' : totalTokens.toLocaleString()}
            </div>
          </div>
          <div className="text-right">
            <div className="text-sm text-gray-500 mb-1">ค่าใช้จ่ายประมาณการ</div>
            <div className="text-2xl font-bold text-orange-500">฿{totalCost.toFixed(4)}</div>
          </div>
        </div>

        <h4 className="text-sm font-semibold text-gray-700 mb-2">รายละเอียดแยกตามแชท (ล่าสุด)</h4>
        <div className="overflow-y-auto flex-1 border border-gray-200 rounded-lg bg-white" style={{ minHeight: 200 }}>
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-gray-500 bg-gray-50 border-b border-gray-200 sticky top-0">
              <tr>
                <th className="px-4 py-2 font-medium">แชท (คนไข้)</th>
                <th className="px-4 py-2 font-medium text-right">Tokens</th>
                <th className="px-4 py-2 font-medium text-right">Cost (THB)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading && (
                <tr><td colSpan={3} className="px-4 py-6 text-center text-gray-400">กำลังโหลดข้อมูล...</td></tr>
              )}
              {!loading && error && (
                <tr><td colSpan={3} className="px-4 py-6 text-center text-red-500">เกิดข้อผิดพลาดในการโหลดข้อมูล</td></tr>
              )}
              {!loading && !error && (!data?.sessions?.length) && (
                <tr><td colSpan={3} className="px-4 py-6 text-center text-gray-400">ยังไม่มีข้อมูลการใช้งานในเดือนนี้</td></tr>
              )}
              {!loading && !error && data?.sessions?.map((s, i) => {
                const rowTokens = (s.total_prompt || 0) + (s.total_completion || 0);
                const rowCost = costThb(s.total_prompt || 0, s.total_completion || 0);
                return (
                  <tr key={i} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3 font-medium text-gray-800">{s.patient_name || s.title || 'แชทใหม่'}</td>
                    <td className="px-4 py-3 text-right text-emerald-600 font-semibold">{rowTokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right text-gray-600">฿{rowCost.toFixed(4)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
