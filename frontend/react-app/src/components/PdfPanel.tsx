import { useEffect, useRef, useState } from 'react';
import PdfView from './PdfView';

export interface PdfTarget {
  source: string;
  page: string;
  type: string;
  heading: string;
}

function resolveFilename(source: string): string {
  const upper = (source || '').toUpperCase();
  if (upper.includes('AAFP')) return 'AAFP_2022_Original.pdf';
  if (upper.includes('URI')) return 'P2_URI.pdf';
  if (upper.includes('DOSE')) return 'Dose supportive new.pdf';
  return source + '.pdf';
}

interface Props {
  target: PdfTarget | null;
  onClose: () => void;
}

export default function PdfPanel({ target, onClose }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const resizerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  // หน้าที่ "กำลังแสดงจริง" ที่ตัวอ่านรายงานกลับมา -- โชว์คู่กับหน้าที่ถูกอ้าง
  // เพื่อให้เห็นทันทีถ้าสองค่านี้ไม่ตรงกัน (อาการที่เคยเจอ)
  const [shown, setShown] = useState<{ page: number; total: number } | null>(null);
  // ChatPage สร้าง target object ใหม่ทุกคลิก -> นับเป็น "คำขอใหม่" ได้แม้เลขหน้าเดิม
  const [req, setReq] = useState(0);

  const isOpen = !!target;
  const filename = target ? resolveFilename(target.source) : '';
  const pageNum = target ? (target.page || '').replace(/\D/g, '') : '';

  useEffect(() => {
    if (!isOpen) {
      setWidth(null);
      setShown(null);
    }
  }, [isOpen]);

  useEffect(() => {
    if (target) setReq((r) => r + 1);
  }, [target]);

  useEffect(() => {
    const resizer = resizerRef.current;
    const panel = panelRef.current;
    if (!resizer || !panel) return;

    let resizing = false;

    const onMouseDown = () => {
      resizing = true;
      resizer.classList.add('dragging');
      panel.classList.add('resizing');
      document.body.style.cursor = 'col-resize';
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!resizing) return;
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth > 300 && newWidth < window.innerWidth - 300) {
        setWidth(newWidth);
      }
    };
    const onMouseUp = () => {
      if (!resizing) return;
      resizing = false;
      resizer.classList.remove('dragging');
      panel.classList.remove('resizing');
      document.body.style.cursor = '';
    };

    resizer.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    return () => {
      resizer.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  const label = pageNum ? `หน้า ${pageNum}` : 'PDF';
  const title = target ? `📄 ${target.source} (${label})` : '';
  // ไม่ตรงกัน = เปิดผิดหน้า ต้องเห็นชัด ไม่ใช่ปล่อยให้ผู้ใช้จับได้เอง
  const mismatch = !!(pageNum && shown && shown.page !== Number(pageNum));

  return (
    <aside
      ref={panelRef}
      className={`pdf-panel${isOpen ? ' open' : ''}`}
      style={width ? { width } : undefined}
    >
      <div className="resizer" ref={resizerRef} />
      <div className="pdf-header">
        <h3 title={target?.heading || undefined}>{title || 'เอกสารอ้างอิง'}</h3>
        {mismatch && (
          <span className="pdf-mismatch" title="หน้าที่แสดงไม่ตรงกับหน้าที่อ้าง">
            กำลังแสดงหน้า {shown?.page}
          </span>
        )}
        <button className="pdf-close-btn" onClick={onClose} title="ปิดหน้าต่าง">
          <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      {isOpen && (
        <PdfView
          key={filename}
          url={`/data/${encodeURI(filename)}`}
          page={pageNum ? Number(pageNum) : 1}
          jumpKey={req}
          onPageChange={(page, total) => setShown({ page, total })}
        />
      )}
    </aside>
  );
}
