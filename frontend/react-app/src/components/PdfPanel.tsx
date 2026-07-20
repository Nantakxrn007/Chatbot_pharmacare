import { useEffect, useRef, useState } from 'react';

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
  if (upper.includes('DOSE')) return 'Dose supportive.pdf';
  return source + '.pdf';
}

interface Props {
  target: PdfTarget | null;
  onClose: () => void;
}

export default function PdfPanel({ target, onClose }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const resizerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);

  const isOpen = !!target;

  // Force-reload trick: reset to about:blank, then set the real src on the
  // next tick so #page=N hash always re-triggers the PDF viewer's jump.
  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    if (!target) {
      iframe.src = '';
      return;
    }

    const filename = resolveFilename(target.source);
    const pageNum = (target.page || '').replace(/\D/g, '');
    const hash = pageNum ? `#page=${pageNum}` : '';
    const pdfUrl = `/data/${encodeURI(filename)}?t=${Date.now()}${hash}`;

    iframe.src = 'about:blank';
    const timer = setTimeout(() => {
      iframe.src = pdfUrl;
    }, 50);
    return () => clearTimeout(timer);
  }, [target]);

  useEffect(() => {
    if (!isOpen) setWidth(null);
  }, [isOpen]);

  useEffect(() => {
    const resizer = resizerRef.current;
    const panel = panelRef.current;
    if (!resizer || !panel) return;

    let resizing = false;

    const onMouseDown = () => {
      resizing = true;
      resizer.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      if (iframeRef.current) iframeRef.current.style.pointerEvents = 'none';
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
      document.body.style.cursor = '';
      if (iframeRef.current) iframeRef.current.style.pointerEvents = 'auto';
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

  const pageNum = target ? (target.page || '').replace(/\D/g, '') : '';
  const label = pageNum ? `หน้า ${pageNum}` : 'PDF';
  const title = target ? `📄 ${target.source} (${label})` : '';

  return (
    <aside
      ref={panelRef}
      className={`pdf-panel${isOpen ? ' open' : ''}`}
      style={width ? { width } : undefined}
    >
      <div className="resizer" ref={resizerRef} />
      <div className="pdf-header">
        <h3 title={target?.heading || undefined}>{title || 'เอกสารอ้างอิง'}</h3>
        <button className="pdf-close-btn" onClick={onClose} title="ปิดหน้าต่าง">
          <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <iframe ref={iframeRef} className="pdf-iframe" title="PDF viewer" />
    </aside>
  );
}
