// ตัวอ่าน PDF ที่เรนเดอร์เอง (PDF.js) แทนตัวอ่านในตัวเบราว์เซอร์
// ================================================================================
// เหตุผลที่ต้องเรนเดอร์เอง (ตาม feedback "label ถูกแต่หน้าที่เด้งไปผิด"):
// การฝัง <iframe src="file.pdf#page=N"> พึ่งตัวอ่านของเบราว์เซอร์ ซึ่งเชื่อถือไม่ได้
// ทุกรูปแบบที่ลองแล้ว -- ยืนยันด้วยการทดสอบจริงในเบราว์เซอร์:
//   1) URL ใหม่ทุกครั้ง (?t=Date.now()) -> โหลดตอน iframe ยังกว้าง 0px ตัวอ่านคิดตำแหน่ง
//      ของ #page=N จาก layout ผิด -> ไปหยุดหน้าข้างเคียง (คลาดเคลื่อนมากขึ้นตามเลขหน้า)
//   2) URL คงที่ (?p=N) -> เบราว์เซอร์ไม่โหลดเอกสารใหม่ กลับไป restore ตำแหน่ง scroll เดิม
//      -> ช่องเลขหน้าไม่ตรงกับเนื้อหาที่แสดง / กดซ้ำแล้วค้างเป็นหน้าว่าง
//   3) about:blank ก่อนแล้วโหลด URL เดิม -> ครั้งแรกถูก กดซ้ำแล้วค้างเป็นหน้าว่าง
// และ #page=N ยังถูกอ่านแค่ "ตอนโหลดเอกสารจริง" (เขียน location.hash ไม่มีผล)
//
// เรนเดอร์เองแก้ทั้งหมด: เรารู้แน่ว่ากำลังวาดหน้าไหนเพราะเรียก pdf.getPage(n) ตรง ๆ
// เลขหน้าที่โชว์จึงมาจากหน้าที่วาดจริง ไม่ใช่จากตัวอ่านที่เราควบคุมไม่ได้
// และแสดงครบทุกหน้าตามไฟล์ต้นฉบับ (เลื่อนต่อเนื่อง) ไม่มีการตัดหน้า
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import type { PDFDocumentProxy } from 'pdfjs-dist';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

// ระยะห่างระหว่างหน้า (px ที่ scale 1) -- ใช้คำนวณตำแหน่ง scroll ของแต่ละหน้า
const PAGE_GAP = 12;
const ZOOM_STEPS = [0.5, 0.65, 0.8, 1, 1.25, 1.5, 2, 3];

// ─── คุณภาพการเรนเดอร์ ───────────────────────────────────────────────────────
// วาด canvas ใหญ่กว่าขนาดที่แสดงจริงหลายเท่า (supersampling) แล้วให้เบราว์เซอร์ย่อลง
// -> ตัวหนังสือในตารางที่ตัวเล็ก ๆ คมชัดขึ้นชัดเจน (เดิมวาดที่ devicePixelRatio
//    เท่านั้น = 1:1 บนจอปกติ จึงดูซอฟต์/เบลอ)
// เพดาน: กันชน canvas ของเบราว์เซอร์ (ด้านละ ~16384px, พื้นที่รวมจำกัด) และกันกินแรม
// หน้าที่กำลังมองอยู่ = คมสุด; หน้าข้างเคียงเรนเดอร์หยาบกว่าไว้ล่วงหน้า แล้วอัปเกรด
// เป็นคมสุดเมื่อเลื่อนมาถึง -> ได้ทั้งความคมและไม่กินแรมเกินจำเป็น
const SUPERSAMPLE = 3;          // เท่าของขนาดแสดงผล สำหรับหน้าที่อยู่ในสายตา
const SUPERSAMPLE_NEAR = 1.25;  // สำหรับหน้าข้างเคียงที่เตรียมไว้ล่วงหน้า
const MAX_CANVAS_SIDE = 8192;
// เพดานพื้นที่ canvas ต่อหน้า: กันกรณีซูมสูง (ที่ 300% ขนาดแสดงผลใหญ่อยู่แล้ว
// q ต่ำกว่าก็คมพอ) ไม่ให้ไปกินแรมระดับร้อย MB ต่อหน้า
const MAX_CANVAS_PIXELS = 20e6;
// เก็บ canvas ไว้เท่าที่จำเป็น -- หน้าที่เลื่อนพ้นระยะนี้จะถูกคืนแรม
const KEEP_SCREENS = 1;

interface Props {
  /** URL ของไฟล์ PDF (เสิร์ฟจาก /data/...) */
  url: string;
  /** หน้าที่ต้องการเปิด (1-based); ไม่ระบุ = หน้า 1 */
  page?: number;
  /** เพิ่มค่าทุกครั้งที่ผู้ใช้กดอ้างอิง -- ทำให้ "กดอ้างอิงเดิมซ้ำ" กระโดดกลับไปหน้านั้นอีกครั้ง
   *  (ถ้าดูแค่ page ค่าจะไม่เปลี่ยน effect จึงไม่ทำงาน แล้วค้างอยู่ตำแหน่งที่เลื่อนไป) */
  jumpKey?: number;
  /** แจ้งกลับว่ากำลังแสดงหน้าไหนจริง ๆ (ใช้โชว์ในหัวแผง/ทดสอบได้) */
  onPageChange?: (page: number, total: number) => void;
}

interface PageBox {
  /** ตำแหน่ง top ของหน้านี้ในกรอบเลื่อน (px) */
  top: number;
  width: number;
  height: number;
}

export default function PdfView({ url, page, jumpKey, onPageChange }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  // งานเรนเดอร์ที่ค้างอยู่ของแต่ละหน้า -> ยกเลิกได้เวลาเลื่อนเร็ว ๆ
  const tasksRef = useRef(new Map<number, ReturnType<pdfjs.PDFPageProxy['render']>>());
  // page -> ค่า q (ตัวคูณความละเอียด) ที่วาดไว้ -> รู้ว่าต้องวาดใหม่ให้คมขึ้นเมื่อไร
  const drawnRef = useRef(new Map<number, number>());
  // ขนาดหน้าที่ scale 1 ของทุกหน้า -- อ่านครั้งเดียวตอนโหลดเอกสาร
  // (เดิมเรียก getPage() ทุกหน้าใหม่ทุกครั้งที่เปิด/ปรับความกว้าง ทำให้ URI 72 หน้าช้ามาก)
  const baseRef = useRef<{ w: number; h: number }[]>([]);
  // กันไม่ให้ onScroll ที่เกิดจากการ "กระโดดไปหน้า N" ไปทับ currentPage
  const programmaticRef = useRef(false);

  const [total, setTotal] = useState(0);
  const [boxes, setBoxes] = useState<PageBox[]>([]);
  const [zoomIdx, setZoomIdx] = useState(3); // 1.0
  const [fitWidth, setFitWidth] = useState(true);
  const [current, setCurrent] = useState(page || 1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const scale = ZOOM_STEPS[zoomIdx];

  // ─── โหลดเอกสาร ───────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    drawnRef.current.clear();
    const task = pdfjs.getDocument({ url, disableAutoFetch: false });
    task.promise.then(
      (doc) => {
        if (cancelled) {
          doc.destroy();
          return;
        }
        docRef.current = doc;
        void (async () => {
          const sizes: { w: number; h: number }[] = [];
          for (let n = 1; n <= doc.numPages; n++) {
            const vp = (await doc.getPage(n)).getViewport({ scale: 1 });
            sizes.push({ w: vp.width, h: vp.height });
          }
          if (cancelled) return;
          baseRef.current = sizes;
          setTotal(doc.numPages);
          setLoading(false);
        })();
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'เปิดไฟล์ PDF ไม่ได้');
          setLoading(false);
        }
      }
    );
    return () => {
      cancelled = true;
      task.destroy?.();
      docRef.current?.destroy();
      docRef.current = null;
    };
  }, [url]);

  // ─── วัดขนาดทุกหน้าตาม scale ปัจจุบัน ────────────────────────────────────
  // ทำก่อนวาด เพื่อให้กรอบเลื่อนมีความสูงจริงของทั้งเอกสาร -> การกระโดดไปหน้า N
  // คำนวณจากตำแหน่งจริง ไม่ใช่การเดา (นี่คือจุดที่ตัวอ่านของเบราว์เซอร์พลาด)
  const measure = useCallback(() => {
    const host = scrollRef.current;
    const sizes = baseRef.current;
    if (!host || !sizes.length) return;
    const avail = Math.max(host.clientWidth - 24, 120);
    const out: PageBox[] = [];
    let top = 0;
    // วัดรายหน้าจาก cache -> รองรับไฟล์ที่หน้าไม่เท่ากัน (URI หน้าแรกสูงกว่าหน้าอื่น)
    // โดยไม่ต้องเรียก getPage() ซ้ำ
    for (const base of sizes) {
      const s = fitWidth ? avail / base.w : scale;
      const w = Math.floor(base.w * s);
      const h = Math.floor(base.h * s);
      out.push({ top, width: w, height: h });
      top += h + PAGE_GAP;
    }
    setBoxes(out);
    drawnRef.current.clear();
  }, [fitWidth, scale]);

  useEffect(() => {
    if (!loading && total) measure();
  }, [loading, total, measure]);

  // วัดใหม่เมื่อความกว้างของแผงเปลี่ยน (ลากขอบ / ย่อขยายหน้าต่าง)
  useEffect(() => {
    const host = scrollRef.current;
    if (!host) return;
    let w = host.clientWidth;
    const ro = new ResizeObserver(() => {
      if (Math.abs(host.clientWidth - w) < 2) return;
      w = host.clientWidth;
      measure();
    });
    ro.observe(host);
    return () => ro.disconnect();
  }, [measure]);

  // ─── วาดหน้าที่อยู่ในสายตา (+ หน้าถัดไป/ก่อนหน้า) ────────────────────────
  const drawVisible = useCallback(() => {
    const doc = docRef.current;
    const host = scrollRef.current;
    const wrap = canvasWrapRef.current;
    if (!doc || !host || !wrap || !boxes.length) return;
    const top = host.scrollTop;
    const bottom = top + host.clientHeight;
    const pad = host.clientHeight * KEEP_SCREENS;
    const dpr = window.devicePixelRatio || 1;
    for (let n = 1; n <= boxes.length; n++) {
      const b = boxes[n - 1];
      const canvas = wrap.querySelector<HTMLCanvasElement>(`canvas[data-page="${n}"]`);
      if (!canvas) continue;
      const inView = b.top < bottom && b.top + b.height > top;
      const near = b.top < bottom + pad && b.top + b.height > top - pad;
      if (!near) {
        // เลื่อนพ้นระยะ -> คืนแรมของ canvas ความละเอียดสูง แล้วให้วาดใหม่เมื่อกลับมา
        if (drawnRef.current.has(n)) {
          drawnRef.current.delete(n);
          tasksRef.current.get(n)?.cancel();
          tasksRef.current.delete(n);
          canvas.width = 0;
          canvas.height = 0;
        }
        continue;
      }
      // ขยายให้คมสุดเท่าที่ canvas รับได้ (จำกัดทั้งด้านและพื้นที่รวม)
      let q = (inView ? SUPERSAMPLE : SUPERSAMPLE_NEAR) * dpr;
      q = Math.min(q, MAX_CANVAS_SIDE / b.width, MAX_CANVAS_SIDE / b.height);
      q = Math.min(q, Math.sqrt(MAX_CANVAS_PIXELS / (b.width * b.height)));
      q = Math.max(q, 1);
      const done = drawnRef.current.get(n);
      // วาดแล้วด้วยความละเอียดที่พอ (หรือสูงกว่า) -> ไม่ต้องวาดซ้ำ
      if (done !== undefined && done >= q - 0.01) continue;
      drawnRef.current.set(n, q);
      void (async () => {
        const p = await doc.getPage(n);
        const base = p.getViewport({ scale: 1 });
        const css = b.width / base.width;            // scale ที่แสดงบนหน้าจอ
        const vp = p.getViewport({ scale: css * q });
        canvas.width = Math.round(vp.width);
        canvas.height = Math.round(vp.height);
        const cx = canvas.getContext('2d', { alpha: false });
        if (!cx) return;
        // พื้นขาวก่อนวาด: ปิด alpha แล้วพื้นจะเป็นดำถ้าไม่ล้างก่อน
        cx.fillStyle = '#fff';
        cx.fillRect(0, 0, canvas.width, canvas.height);
        tasksRef.current.get(n)?.cancel();
        const t = p.render({ canvasContext: cx, viewport: vp });
        tasksRef.current.set(n, t);
        try {
          await t.promise;
        } catch {
          // ยกเลิกเพราะเลื่อนผ่านไปแล้ว -> ให้วาดใหม่รอบหน้าได้
          drawnRef.current.delete(n);
        }
      })();
    }
  }, [boxes]);

  useEffect(() => {
    drawVisible();
  }, [drawVisible]);

  // ─── กระโดดไปหน้าที่ถูกอ้าง ───────────────────────────────────────────────
  // ใช้ตำแหน่ง top ที่ "วัดมาแล้วจริง" ของหน้านั้น -> ตรงหน้าเสมอ
  useLayoutEffect(() => {
    const host = scrollRef.current;
    if (!host || !boxes.length) return;
    const want = Math.min(Math.max(page || 1, 1), boxes.length);
    programmaticRef.current = true;
    host.scrollTop = boxes[want - 1].top;
    setCurrent(want);
    // ปล่อย flag หลัง scroll event รอบนี้ผ่านไป
    const id = window.setTimeout(() => {
      programmaticRef.current = false;
    }, 120);
    return () => window.clearTimeout(id);
  }, [page, jumpKey, boxes, url]);

  useEffect(() => {
    if (total) onPageChange?.(current, total);
  }, [current, total, onPageChange]);

  const onScroll = () => {
    drawVisible();
    if (programmaticRef.current || !boxes.length) return;
    const host = scrollRef.current;
    if (!host) return;
    // หน้าที่กินพื้นที่ในสายตามากที่สุด = หน้าที่ผู้ใช้กำลังดู
    const top = host.scrollTop;
    const bottom = top + host.clientHeight;
    let best = 1;
    let bestOverlap = -1;
    for (let n = 1; n <= boxes.length; n++) {
      const b = boxes[n - 1];
      const overlap = Math.min(bottom, b.top + b.height) - Math.max(top, b.top);
      if (overlap > bestOverlap) {
        bestOverlap = overlap;
        best = n;
      }
    }
    setCurrent(best);
  };

  const goTo = (n: number) => {
    const host = scrollRef.current;
    if (!host || !boxes.length) return;
    const want = Math.min(Math.max(n, 1), boxes.length);
    programmaticRef.current = true;
    host.scrollTop = boxes[want - 1].top;
    setCurrent(want);
    window.setTimeout(() => {
      programmaticRef.current = false;
    }, 120);
  };

  return (
    <div className="pdfv">
      <div className="pdfv-bar">
        <button type="button" onClick={() => goTo(current - 1)} disabled={current <= 1} title="หน้าก่อน">‹</button>
        <span className="pdfv-page">
          <input
            type="number"
            value={current}
            min={1}
            max={total || 1}
            onChange={(e) => goTo(Number(e.target.value))}
          />
          <span className="pdfv-total">/ {total || '-'}</span>
        </span>
        <button type="button" onClick={() => goTo(current + 1)} disabled={!total || current >= total} title="หน้าถัดไป">›</button>
        <span className="pdfv-sep" />
        <button type="button" onClick={() => { setFitWidth(false); setZoomIdx((i) => Math.max(0, i - 1)); }} title="ย่อ">−</button>
        <button
          type="button"
          className={fitWidth ? 'on' : undefined}
          onClick={() => setFitWidth((v) => !v)}
          title="พอดีความกว้าง"
        >
          {fitWidth ? 'พอดีกว้าง' : `${Math.round(scale * 100)}%`}
        </button>
        <button type="button" onClick={() => { setFitWidth(false); setZoomIdx((i) => Math.min(ZOOM_STEPS.length - 1, i + 1)); }} title="ขยาย">+</button>
        <span className="pdfv-sep" />
        <a href={url} target="_blank" rel="noreferrer" title="เปิดไฟล์เต็มในแท็บใหม่">เปิดไฟล์</a>
      </div>

      <div className="pdfv-scroll" ref={scrollRef} onScroll={onScroll}>
        {loading && <div className="pdfv-msg">กำลังเปิดเอกสาร…</div>}
        {error && <div className="pdfv-msg pdfv-err">เปิดเอกสารไม่ได้: {error}</div>}
        <div
          className="pdfv-pages"
          ref={canvasWrapRef}
          style={{ height: boxes.length ? boxes[boxes.length - 1].top + boxes[boxes.length - 1].height : 0 }}
        >
          {boxes.map((b, i) => (
            <div
              key={i}
              className="pdfv-pagebox"
              style={{ top: b.top, width: b.width, height: b.height }}
            >
              <canvas data-page={i + 1} style={{ width: b.width, height: b.height }} />
              <span className="pdfv-num">{i + 1}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
