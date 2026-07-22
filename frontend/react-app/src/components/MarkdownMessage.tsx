import { useEffect, useRef } from 'react';
import { marked } from 'marked';

marked.setOptions({ breaks: true, gfm: true });

function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

// Matches "โอกาสสูง", "โอกาส: สูง", "โอกาส: **สูง**" etc — the backend
// sometimes bolds just the level word, which (if handled after marked.parse)
// lands in a *separate* DOM text node from "โอกาส:" and silently breaks a
// DOM-based regex match. Doing this before marked.parse() sidesteps that
// entirely, the same way the [Ref: ...] parsing below does.
// "สูงมาก" must be listed before "สูง" (and "ปานกลางถึงสูง" before "ปานกลาง")
// so the longer word matches first — otherwise the alternation grabs "สูง"
// alone and leaves "มาก" as unrendered trailing text.
// Group 1 ("เป็น"/"จะเป็น" + optional colon/spacing) and group 2 (a short
// run-up description, e.g. a diagnosis name) let this also catch phrasing
// like "มีโอกาสเป็น GABHS Pharyngitis สูง" — not just the direct "โอกาส: สูง".
// The run-up is capped at 100 chars with no period so it can't bleed into
// an unrelated later sentence that happens to contain a level word.
const PROBABILITY_PATTERN =
  /โอกาส((?:เป็น|จะเป็น)?\s*:?\s*)([^\n.]{0,100}?)\*{0,2}(สูงมาก|ปานกลางถึงสูง|ปานกลาง|กลาง|สูง|ต่ำ)\*{0,2}/g;
const PROBABILITY_CLASS: Record<string, string> = {
  สูงมาก: 'ai-prob-badge ai-prob-vhigh',
  สูง: 'ai-prob-badge ai-prob-high',
  ปานกลางถึงสูง: 'ai-prob-badge ai-prob-high',
  ปานกลาง: 'ai-prob-badge ai-prob-mid',
  กลาง: 'ai-prob-badge ai-prob-mid',
  ต่ำ: 'ai-prob-badge ai-prob-low',
};

// Dosage amounts (e.g. "500 mg", "1,000 mg", "325-650 mg", "80-90 มก./กก./วัน")
// get a light highlight chip so they stand out from the surrounding
// instructions. Uses a lookahead instead of \b to mark the end of the unit —
// \b only recognizes ASCII word chars, so it silently never matches after a
// Thai unit like "มก." (no ASCII/non-ASCII transition for it to detect).
const DOSE_PATTERN = /\d[\d,.]*(?:\s*-\s*\d[\d,.]*)?\s*(?:mg|mcg|mL|ml|IU|g|หน่วย|มก\.?|มล\.?)(?![a-zA-Zก-๙])/gi;

/**
 * Parses inline [Ref: AAFP, Page: 4] / [Ref: ความรู้ทั่วไป... อ้างอิงจาก UpToDate]
 * markers into clickable source tags carrying data-* attributes (read by a
 * single delegated onClick in the parent), then renders the rest as markdown.
 */
// Some answers write section headers as a whole bold line ("**1. สรุปอาการ**"
// or, without a leading number, "**ข้อควรระวัง (Red Flags):**") instead of a
// real "### " heading — marked then renders that as a plain <strong> inside
// a <p>, which the heading-badge/card styling never sees. Promoting any
// bold-only line (nothing else on the line) to a real heading first means
// every answer format gets the same numbered-card treatment, no matter how
// the backend wrote it or whether it numbered the section.
const BOLD_ONLY_LINE_PATTERN = /^\*\*([^\n*]+)\*\*\s*$/gm;

// A bold-only line like "**คะแนนรวม: 5 คะแนน**" is a result readout, not a
// section title — it just happens to also be a whole bold line. Skip
// promoting anything shaped like "label: <number>" so only real headings
// (no trailing number after a colon) get the numbered-card treatment.
const SCORE_LINE_PATTERN = /:\s*\d/;

// A bullet's "main point -- เหตุผล: ..." reasoning clause reads as one run-on
// line — break it onto its own line before rendering so it's visually
// separated instead of crammed after the dash.
const REASON_SEPARATOR_PATTERN = /\s*--\s*(?=\*{0,2}เหตุผล)/g;

// Dose bullets ("Paracetamol: <intro/concentration options> โดยขนาดยา...")
// cram the actual dosing numbers onto the end of an already-long sentence —
// break onto a new line right before the dosing clause so the numbers are
// easy to spot instead of buried mid-paragraph. Deliberately narrow (exact
// phrases, not a bare "โดย") to avoid breaking unrelated sentences that
// happen to contain the very common word "โดย".
const DOSE_CLAUSE_SEPARATOR_PATTERN = /\s+(?=โดยขนาดยา|โดยต้องคำนวณ|โดยต้องใช้)/g;

// "ข้อควรระวัง" / "ห้าม..." are flagged red wherever they appear inline.
// IMPORTANT: this only wraps the bare word — it must NOT consume any
// surrounding "**". The backend sometimes bolds just the word ("**ห้าม**")
// but sometimes bolds a whole phrase around it ("**ห้ามใช้ยาเอง...**" or
// "**ข้อควรระวัง:**"). Eating the "**" next to the word desyncs marked's
// bold pairing whenever the bold span extends past the word — the opening
// "**" gets consumed here while its matching closing "**" (now orphaned)
// gets rendered as a literal "**" by marked. Leaving all "**" untouched and
// only wrapping the word lets marked's own bold parser pair them correctly;
// our span just ends up nested inside <strong> when the source was bold.
const CAUTION_PATTERN = /ข้อควรระวัง/g;
const PROHIBIT_PATTERN = /ห้าม/g;

function renderMd(text: string): string {
  if (!text) return '';
  try {
    let processed = text.replace(BOLD_ONLY_LINE_PATTERN, (match, inner: string) =>
      SCORE_LINE_PATTERN.test(inner) ? match : `### ${inner}`
    );
    processed = processed.replace(REASON_SEPARATOR_PATTERN, '\n');
    processed = processed.replace(DOSE_CLAUSE_SEPARATOR_PATTERN, '\n');

    processed = processed.replace(
      PROBABILITY_PATTERN,
      (_match, lead: string, runup: string, level: string) => {
        const cls = PROBABILITY_CLASS[level] || 'ai-prob-badge';
        if (!runup.trim()) {
          // Direct mention ("โอกาส: สูง" / "โอกาสสูง") — compact badge.
          return `<span class="${cls}">โอกาส: ${escapeHtml(level)}</span>`;
        }
        // Level word appears after a run-up description (e.g. a diagnosis
        // name) — keep that text as-is and just highlight the level word.
        return `โอกาส${lead}${runup}<span class="${cls}">${escapeHtml(level)}</span>`;
      }
    );

    processed = processed.replace(DOSE_PATTERN, (match) => `<span class="ai-dose-highlight">${match}</span>`);

    processed = processed.replace(CAUTION_PATTERN, '<span class="ai-caution-text">ข้อควรระวัง</span>');
    processed = processed.replace(PROHIBIT_PATTERN, '<span class="ai-caution-text">ห้าม</span>');

    processed = processed.replace(/\[Ref:\s*(.*?)\]/gi, (_match, content: string) => {
      if (content.includes('ความรู้ทั่วไป') || content.includes('อ้างอิงจาก')) {
        const extMatch = content.match(/อ้างอิงจาก\s*(.*)/);
        const source = extMatch ? extMatch[1].trim() : content;
        return `<span class="inline-source-tag" data-source="${escapeHtml(source)}" data-type="external">🌐 ${escapeHtml(source)}</span>`;
      }

      let source = content;
      let page = '';
      // Backend emits both "AAFP, หน้า 5, Table 3" and "AAFP หน้า 5" (no
      // comma) — take everything before the page marker as the source name
      // rather than requiring a leading comma.
      const pageMatch = content.match(/(?:page|หน้า|p\.)\s*:?\s*(\d+)/i);
      if (pageMatch && pageMatch.index !== undefined) {
        page = pageMatch[1];
        source = content.slice(0, pageMatch.index).trim().replace(/,$/, '').trim();
      }

      return `<span class="inline-source-tag" data-source="${escapeHtml(source)}" data-page="${page}" data-type="internal">📄 ${escapeHtml(source)}${page ? ' p.' + page : ''}</span>`;
    });

    return marked.parse(processed) as string;
  } catch {
    return text;
  }
}

// Every topic heading in an answer gets the same sequential numbered-circle
// badge (1, 2, 3…), whatever the backend prefixed it with — an explicit
// "N. " counts toward the running number, an emoji (e.g. "### 📊 ประเมิน")
// or plain text just gets stripped and renumbered in order of appearance.
const RED_FLAG_PATTERN = /red\s*flags?|สัญญาณเตือน|ข้อควรระวัง/i;
const NOTE_HEADING_PATTERN = /ข้อซักถาม|หมายเหตุ/;

function applyHeadingBadges(root: HTMLElement) {
  let counter = 0;
  root.querySelectorAll('h1, h2, h3, h4').forEach((heading) => {
    counter += 1;
    let text = heading.textContent || '';
    // Strip a leading "N." or hierarchical "3a."/"3b." prefix — the backend
    // sometimes numbers sub-sections that way, which the plain \d+\. version
    // of this regex missed entirely, leaving the old prefix sitting next to
    // our own renumbered badge (e.g. "④ 3a. ยาปฏิชีวนะ").
    text = text.replace(/^(\d+[a-zA-Z]?)\.\s*/, '').replace(/^[\p{Extended_Pictographic}‍️]+\s*/u, '');
    if (RED_FLAG_PATTERN.test(text)) {
      heading.classList.add('ai-heading-warning');
    } else if (NOTE_HEADING_PATTERN.test(text)) {
      heading.classList.add('ai-heading-note');
    }
    heading.innerHTML = `<span class="ai-num-badge">${counter}</span><span>${escapeHtml(text)}</span>`;
  });
}

// Bold lead-in labels like "ข้อซักถามเพิ่มเติมเพื่อความปลอดภัย:" or
// "หมายเหตุสำคัญ:" get a light-blue tint instead of whatever color they'd
// otherwise inherit (green section-header, or plain dark text) — set as an
// inline style so it wins regardless of which CSS rule would apply.
const NOTE_LABEL_PATTERN = /ข้อซักถาม|หมายเหตุ/;

function applyNoteHighlights(root: HTMLElement) {
  root.querySelectorAll('strong').forEach((el) => {
    if (NOTE_LABEL_PATTERN.test(el.textContent || '')) {
      el.style.color = '#2f6fbf';
    }
  });
}

interface Props {
  content: string;
  onOpenSource: (source: string, page: string, type: string, heading: string) => void;
  className?: string;
}

export default function MarkdownMessage({ content, onOpenSource, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const handler = (e: MouseEvent) => {
      const target = (e.target as HTMLElement).closest('.inline-source-tag') as HTMLElement | null;
      if (!target) return;
      onOpenSource(
        target.dataset.source || '',
        target.dataset.page || '',
        target.dataset.type || '',
        target.dataset.heading || ''
      );
    };
    el.addEventListener('click', handler);
    return () => el.removeEventListener('click', handler);
  }, [onOpenSource]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    applyHeadingBadges(el);
    applyNoteHighlights(el);
  }, [content]);

  return (
    <div ref={ref} className={className} dangerouslySetInnerHTML={{ __html: renderMd(content) }} />
  );
}
