import { useEffect, useState } from 'react';
import '../styles/toolsPanel.css';
import { getDrugs } from '../lib/api';

const REFERENCES = [
  { source: 'AAFP 2022', desc: 'แนวทาง URI (อเมริกา)' },
  { source: 'Thai URI guideline', desc: 'แนวทาง URI เด็ก (ไทย)' },
  { source: 'Dose supportive', desc: 'ขนาดยาสนับสนุนการรักษา' },
];

// Used only if /api/drugs fails to load (network error, server down, etc.)
// so the calculator still works instead of showing an empty dropdown.
const FALLBACK_DRUGS = ['Amoxicillin', 'Penicillin V', 'Cephalexin', 'Clindamycin'];

interface Props {
  onOpenReference: (source: string, page: string, type: string, heading: string) => void;
  onSendMessage: (text: string) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

export default function ToolsPanel({ onOpenReference, onSendMessage, collapsed, onToggleCollapsed, mobileOpen, onCloseMobile }: Props) {
  const [drug, setDrug] = useState('');
  const [drugSearch, setDrugSearch] = useState('');
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [weight, setWeight] = useState('');
  const [drugNames, setDrugNames] = useState<string[] | null>(null);
  const [drugsFailed, setDrugsFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getDrugs()
      .then((drugs) => {
        if (cancelled) return;
        setDrugNames(drugs.map((d) => d.name).sort((a, b) => a.localeCompare(b)));
      })
      .catch(() => {
        if (cancelled) return;
        setDrugNames([...FALLBACK_DRUGS].sort((a, b) => a.localeCompare(b)));
        setDrugsFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredDrugNames = (drugNames || []).filter((d) =>
    d.toLowerCase().includes(drugSearch.trim().toLowerCase())
  );

  const selectDrug = (name: string) => {
    setDrug(name);
    setDrugSearch(name);
    setShowSuggestions(false);
  };

  const calculate = () => {
    if (!drug.trim() || !weight.trim()) return;
    onSendMessage(`คำนวณขนาดยา ${drug} สำหรับผู้ป่วยน้ำหนัก ${weight} กก.`);
  };

  return (
    <aside className={`tools-panel${collapsed ? ' collapsed' : ''}${mobileOpen ? ' mobile-open' : ''}`}>
      <button
        className="tp-toggle-btn"
        onClick={mobileOpen ? onCloseMobile : onToggleCollapsed}
        title={collapsed ? 'เปิดแผงเครื่องมือ' : 'ปิดแผงเครื่องมือ'}
      >
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={collapsed ? 'M15 19l-7-7 7-7' : 'M9 5l7 7-7 7'} />
        </svg>
      </button>

      <div className="tp-content">
      <div className="tp-label">REFERENCE</div>
      {REFERENCES.map((ref) => (
        <div key={ref.source} className="tp-ref-item" onClick={() => onOpenReference(ref.source, '', 'internal', '')}>
          <span className="material-symbols-rounded">picture_as_pdf</span>
          <div>
            <div className="tp-ref-title">{ref.source}</div>
            <div className="tp-ref-desc">{ref.desc}</div>
          </div>
        </div>
      ))}

      <div className="tp-label">DRUG CALCULATOR</div>
      <div className="tp-field-label">ยา</div>
      <div className="tp-combobox">
        <input
          className="tp-input"
          type="text"
          placeholder={drugNames === null ? 'กำลังโหลด...' : 'ค้นหายา...'}
          value={drugSearch}
          disabled={drugNames === null}
          onChange={(e) => {
            setDrugSearch(e.target.value);
            setDrug('');
            setShowSuggestions(true);
          }}
          onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
        />
        {showSuggestions && filteredDrugNames.length > 0 && (
          <ul className="tp-combobox-list">
            {filteredDrugNames.map((d) => (
              <li key={d} onMouseDown={() => selectDrug(d)}>
                {d}
              </li>
            ))}
          </ul>
        )}
        {showSuggestions && drugNames !== null && filteredDrugNames.length === 0 && (
          <ul className="tp-combobox-list">
            <li className="tp-combobox-empty">ไม่พบยาที่ค้นหา</li>
          </ul>
        )}
      </div>
      {drugsFailed && (
        <div className="tp-drug-warning">ไม่สามารถโหลดรายชื่อยาล่าสุดได้ กำลังแสดงรายการสำรอง</div>
      )}
      <div className="tp-field-label">น้ำหนัก (kg)</div>
      <input
        className="tp-input"
        type="number"
        placeholder="เช่น 20"
        value={weight}
        onChange={(e) => setWeight(e.target.value)}
      />
      <button className="tp-calc-btn" disabled={!drug.trim() || !weight.trim()} onClick={calculate}>
        <span className="material-symbols-rounded">calculate</span>
        คำนวณขนาดยา
      </button>
      </div>
    </aside>
  );
}
