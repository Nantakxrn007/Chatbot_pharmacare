import { useEffect, useRef } from 'react';
import { marked } from 'marked';
import { DRUG_NAME_PATTERN } from '../lib/drugNames';

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
  /โอกาส((?:เป็น|จะเป็น)?\s*:?\s*)([^\n.]{0,100}?)\*{0,2}(สูงมาก|ปานกลางถึงสูง|ปานกลาง|กลาง|สูง|ต่ำมาก|ต่ำ)\*{0,2}/g;
// สูงมาก/สูง are both "high" red and ต่ำ/ต่ำมาก are both "low" green, but
// each pair now gets a slightly different shade (vhigh deeper than high,
// low deeper than vlow) so the two aren't *visually* identical — the dot
// count is still what actually tells them apart at a glance.
const PROBABILITY_CLASS: Record<string, string> = {
  สูงมาก: 'ai-prob-badge ai-prob-vhigh',
  สูง: 'ai-prob-badge ai-prob-high',
  ปานกลางถึงสูง: 'ai-prob-badge ai-prob-high',
  ปานกลาง: 'ai-prob-badge ai-prob-mid',
  กลาง: 'ai-prob-badge ai-prob-mid',
  ต่ำ: 'ai-prob-badge ai-prob-low',
  ต่ำมาก: 'ai-prob-badge ai-prob-vlow',
};

// Dot count per level, highest tier first, out of a 5-dot bar — even the
// lowest tier (ต่ำมาก) still gets 1 dot, never 0. A likelihood the AI is
// still listing as a differential is never "nothing," so an empty dot bar
// would read wrong regardless of how unlikely it is.
const PROBABILITY_DOTS: Record<string, number> = {
  สูงมาก: 5,
  สูง: 4,
  ปานกลางถึงสูง: 4,
  ปานกลาง: 3,
  กลาง: 3,
  ต่ำ: 2,
  ต่ำมาก: 1,
};
const PROBABILITY_DOT_TOTAL = 5;

// Dosage amounts (e.g. "500 mg", "1,000 mg", "325-650 mg", "80-90 มก./กก./วัน")
// get a light highlight chip so they stand out from the surrounding
// instructions. Uses a lookahead instead of \b to mark the end of the unit —
// \b only recognizes ASCII word chars, so it silently never matches after a
// Thai unit like "มก." (no ASCII/non-ASCII transition for it to detect).
// The trailing group also pulls in a "/kg/day"-style compound rate (English
// or Thai, e.g. "mg/kg/day", "มก./กก./วัน") so the bold doesn't stop dead in
// the middle of the unit — highlighting just "80-90 mg" and leaving the
// "/kg/day" that completes it in plain weight reads as a rendering glitch.
// A leading "ไม่เกิน" (max-dose ceiling, e.g. "ไม่เกิน 75 mg/kg/day") is
// pulled in the same way — it's part of reading the number correctly, not
// decoration, so it gets highlighted along with the number rather than
// sitting in plain text right next to a bolded value.
const DOSE_PATTERN =
  /(?:ไม่เกิน\s*)?\d[\d,.]*(?:\s*-\s*\d[\d,.]*)?\s*(?:mg|mcg|mL|ml|IU|g|หน่วย|มก\.?|มล\.?)(?:\s*\/\s*[a-zA-Zก-๙.]+){0,2}(?![a-zA-Zก-๙])/gi;

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

// A bold-only line like "**คะแนนรวม: 5 คะแนน**" or "**คะแนนรวม = 5**" is a
// result readout, not a section title — it just happens to also be a whole
// bold line. Skip promoting anything shaped like "label: <number>" or
// "label = <number>" so only real headings (no trailing number after a
// colon/equals) get the numbered-card treatment.
const SCORE_LINE_PATTERN = /[:=]\s*\d/;

// The Modified Centor/McIsaac score-tool label ("**Modified Centor
// (McIsaac) Score:**", "**Modified Centor (McIsaac) -- ...**") reads as
// part of the scoring readout right above its bullet checklist, not its own
// topic — keep it inline instead of promoting it to a boxed heading card.
const SCORE_TOOL_LABEL_PATTERN = /Centor|McIsaac/i;

// The backend usually bolds section sub-labels ("**3a. ยาปฏิชีวนะ...**") so
// BOLD_ONLY_LINE_PATTERN promotes them to a heading — but sometimes writes
// the exact same line with no bold markup at all ("3a. ยาปฏิชีวนะ
// (Antibiotics):"), which then falls through as a plain paragraph with no
// heading badge and no ai-section-sublabel styling. Catch that plain form
// too, own-line only, so "3a."/"3b." always get promoted the same way
// regardless of whether the backend happened to bold it this time.
//
// The letter suffix (3a/3b, not just "3") is REQUIRED here — the backend
// sometimes numbers drug categories as a plain ordinal list instead
// ("1. ยาแก้ปวด/ลดไข้...", "2. ยาพ่นบรรเทาอาการเจ็บคอ...", no letter). Without
// this restriction that list would get intercepted here (before marked.js
// ever sees it as a real "1." list marker) and wrongly promoted to a
// top-level "3a./3b."-style heading with its orange bar, even though it's
// just an ordinary numbered sub-category one level down — this was the
// "layout keeps changing between answers" bug: which format wins is purely
// down to how the backend happened to punctuate the list that generation.
const PLAIN_NUMBERED_TREATMENT_LABEL_PATTERN = /^(\d+[a-zA-Z]\.\s*ยา[^\n*]{0,80}?:?)[ \t]*$/gm;

// Drug-category sub-labels ("ยาปฏิชีวนะ (Antibiotics):", "ยาตามอาการ
// (Symptomatic Treatment):", "ยาแก้ปวด (Analgesics):", ...) are always
// nested *inside* the "การรักษาด้วยยา" section, never their own topic — but
// the backend writes them as a bold-only line (or sometimes a real "### "
// heading) too, so without this they'd fragment into their own numbered
// cards. Generalized to "starts with ยา" (the Thai word for medicine)
// rather than a fixed list of exact labels — every drug-category name
// starts this way, whereas real section titles (สรุปอาการ, ข้อควรระวัง,
// สัญญาณเตือน, ...) never do, so this covers new label wording the backend
// hasn't used yet without needing another patch each time one shows up.
const TREATMENT_SUBLABEL_PATTERN = /^ยา\S/;

// Detects the *deferred* symptomatic-drug-category answer (category names
// only, no doses yet), so the chat can offer a "อยากรู้ว่ายาภายในร้านมีอะไร
// บ้าง" follow-up chip under it — and only under that answer, not under the
// detailed one the chip itself leads to.
//
// A bare "ยาแก้ปวด/ลดไข้ (Analgesic/Antipyretic)" category-header line shows
// up in BOTH answers — the deferred one (nothing follows it but the next
// category) and the detailed follow-up (the same header, now followed by
// named drugs + doses) — so matching on that header alone used to fire the
// chip again under the follow-up it had just produced. The one line that's
// exclusive to the deferred answer is its mandatory closing invite sentence
// (backend is instructed to always end with this, and never once actual
// drug names/doses are already given), so match on that instead of trying
// to tell the two header shapes apart. The backend doesn't reproduce that
// sentence verbatim every time ("...คือตัวไหนบ้าง พร้อมขนาดยา สามารถถามต่อ
// ได้เลยครับ" vs "...ชื่อยาที่มีในร้าน พร้อมขนาดยาตามน้ำหนักตัว สามารถถามต่อ
// ได้เลยครับ") — anchor on the phrase common to every variant seen so far
// instead of the exact wording in between.
const SYMPTOMATIC_INVITE_LINE_PATTERN = /ยาที่มีในร้าน[^\n]{0,60}สามารถถามต่อได้/;

export function hasSymptomaticDrugAdvice(content: string): boolean {
  const text = content || '';
  return SYMPTOMATIC_INVITE_LINE_PATTERN.test(text);
}

// Detects a history-taking answer (AI asking for missing info before it'll
// commit to a diagnosis/treatment plan), so the chat can offer a one-click
// "ไม่มีข้อมูลเพิ่มเติมแล้ว ตอบได้เลย" chip instead of making the pharmacist
// type that out by hand every time they don't have (or don't want to look
// up) the item being asked about. Matches both wordings the backend uses:
// the deterministic gate's fixed heading ("ข้อมูลที่ต้องซักเพิ่มเติมก่อนสรุป
// การรักษา", from symptomatic_gateway.py's ASK_HEADING constant) and the
// free-text variant the LLM writes when it answers in full but still flags
// gaps ("ข้อมูลที่ควรซักเพิ่มเติม", from rag_engine.py's SYSTEM_PROMPT).
const HISTORY_QUESTION_PATTERN = /ข้อมูลที่(?:ต้อง|ควร)ซักเพิ่มเติม/;

export function hasHistoryQuestion(content: string): boolean {
  const text = content || '';
  return HISTORY_QUESTION_PATTERN.test(text);
}

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

// A dosing bullet that offers an alternative regimen ("...500 mg ทุก 8
// ชั่วโมง หรือ 875 mg ทุก 12 ชั่วโมง...") crams both options onto one line —
// break before that "หรือ" so each option reads on its own line. Only
// triggers right after a dose/time unit (mg, ชั่วโมง, วัน, เม็ด, ...), not
// on a bare "หรือ" — "หรือ" is one of the most common words in Thai, so an
// unguarded match would wrongly split unrelated sentences (worse, it would
// split *inside* an unrelated "(เช่น A หรือ B)" list, which never precedes
// a dose unit and so is correctly left alone by this same guard). The unit
// is often immediately followed by a "[Ref: ...]" citation before "หรือ"
// ("...ชั่วโมง [Ref: Dose, page 11] หรือ...") — the optional group lets the
// lookbehind see past that citation without consuming (and losing) it.
const DOSE_ALTERNATIVE_SEPARATOR_PATTERN =
  /(?<=(?:ชั่วโมง|วัน|เม็ด|mg|mL|ml|มก\.|มล\.)(?:\s*\[Ref:[^\]]*\])?)\s+(?=หรือ)/g;

// A bullet that lists several drug *choices* in one run-on sentence
// ("...เช่น **Ibuprofen** 200-400 mg [Ref: Dose, หน้า 10], **Diclofenac**
// 100-150 mg/วัน [Ref: Dose, หน้า 12], หรือ **Naproxen** ...") reads as one
// dense wall — a plain line break wasn't enough (feedback: "ก็ต้องขึ้น
// บรรทัดใหม่สิ ยังไม่มี bullet ข้างหน้าเลย"), each choice needs to be its
// own list item. Mark the boundary here — right after the previous drug's
// "[Ref: ...]" citation and before the next bolded drug name (with or
// without a leading "หรือ") — and before the *first* choice too (right
// after "เช่น") with an invisible sentinel character that survives
// marked.parse() as a plain text node; splitDrugChoiceListItems() below
// then cuts the rendered <li> into separate sibling <li>s at each sentinel,
// so the split happens after parsing (safe across whatever inline
// formatting marked produced) rather than by cutting the markdown source
// itself.
const CHOICE_SPLIT_MARKER = '';
// The comma before "หรือ **Drug**" isn't always there — "...ชั่วโมง)
// [Ref: Dose, หน้า 8] หรือ **Ibuprofen**..." separates choices with just
// "หรือ" and no comma just as often as with one, so the comma has to be
// optional here rather than required. IMPORTANT: the gap can only be
// horizontal whitespace, never a newline — an earlier version allowed \s
// (which matches newlines too) and that let a citation at the end of one
// paragraph pair up with an unrelated bold run at the start of the *next*
// paragraph across the blank line between them, wrongly pulling an
// unrelated heading into the same drug bullet as a "choice".
const MULTI_DRUG_CHOICE_SEPARATOR_PATTERN = /(?<=\])[ \t]*,?[ \t]*(?=(?:หรือ[ \t]+)?\*\*)/g;
// Same horizontal-whitespace-only constraint as above — "เช่น"/"แนะนำ" must
// be directly followed by the bold drug name on the *same* line, not just
// somewhere earlier in the text with a paragraph break in between.
const FIRST_DRUG_CHOICE_SEPARATOR_PATTERN = /(?<=เช่น|แนะนำ)[ \t]+(?=\*\*)/g;

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
// การตัดสินใจ "ไม่จ่ายยาปฏิชีวนะ" คือข้อสรุปที่เภสัชกรต้องเห็นทันที (และเป็นแกนของ RDU/
// antibiotic stewardship) — ทำเป็นชิปแดงเต็มวลี ไม่ใช่แค่คำว่า "ห้าม" คำเดียว เพื่อให้กวาดตา
// เจอในคำตอบยาว ๆ ได้เลย. ต้องแทนก่อน marked.parse() เหมือน pattern อื่น และ "ห้ามกิน **"
// ที่ครอบอยู่ (ไม่งั้น bold ของ marked จะเพี้ยน) — จึงจับเฉพาะตัววลี ไม่แตะ ** รอบข้าง.
const NO_ANTIBIOTIC_PATTERN =
  /(?:ยัง)?ไม่(?:มีความจำเป็น|จำเป็น|แนะนำ|ควร)(?:\s*(?:ต้อง|ให้|จ่าย|ใช้|เริ่ม))*\s*(?:ยา)?(?:ปฏิชีวนะ|ต้านจุลชีพ)|ไม่(?:\s*(?:ต้อง|ให้|จ่าย|ใช้))+(?:ยา)?(?:ปฏิชีวนะ|ต้านจุลชีพ)/gi;
// Bare "ข้อควรระวัง" also fired on mentions that aren't an actual warning
// (e.g. a closing "...สามารถสอบถามเพิ่มเติมเกี่ยวกับข้อควรระวังในโรค
// ประจำตัว..." invitation to ask more, not a warning itself) — a real
// warning is always written as a "ข้อควรระวัง:" label introducing one, so
// require the colon.
const CAUTION_PATTERN = /ข้อควรระวัง(?=\s*:)/g;
// Bare "ห้าม" used to fire on every occurrence, including ones that aren't a
// real instruction (e.g. "เป็นข้อห้ามในผู้ป่วยโรคไต") — with a genuine
// warning nearly every line in a drug-heavy answer, that painted the whole
// answer red and buried the ones that actually mattered. Only highlight it
// right before an action verb ("ห้ามใช้", "ห้ามให้", "ห้ามจ่าย", ...), which
// is what an actual prohibition reads like.
//
// But that alone still fires on "ไม่มีข้อห้ามใช้" ("has no contraindication
// for use" — reassuring, the opposite of a warning) since "ห้ามใช้" is a
// literal substring of "ข้อห้ามใช้" — exclude it when directly preceded by a
// negation ("ไม่มีข้อ...", "ไม่มี...", "ไม่ห้าม...").
const PROHIBIT_PATTERN = /(?<!ไม่มีข้อ)(?<!ไม่มี)(?<!ไม่)ห้าม(?=ใช้|ให้|จ่าย|กิน|รับประทาน|เริ่ม)/g;

// The backend tags the opening case-classification line with an internal
// category number ("เคสผู้ป่วยใหม่ (ประเภท 2)") — meaningful to us, not to
// whoever's reading the answer, since there's no legend anywhere explaining
// what type 1/2/3 mean. Drop just the "(ประเภท N)" parenthetical, keep the
// rest of the sentence.
const CASE_TYPE_LABEL_PATTERN = /\s*\(ประเภท\s*\d+\)/g;

// Highlight a fever mention in "สรุปอาการ" — the fact most likely to get
// missed on a quick read. Requires a negation guard ("ไม่มีไข้" is
// reassuring, not an alert) and either a severity word (ไข้สูง/ไข้ต่ำ, even
// with no reading attached) or an actual number — a bare "มีไข้" with
// neither isn't worth flagging.
const FEVER_PATTERN =
  /(?<!ไม่\s*)ไข้(?:(?:สูง|ต่ำ)(?:\s*\(?\s*\d+(?:\.\d+)?\s*(?:°|องศา)(?:เซลเซียส)?\)?)?|\s*\(?\s*\d+(?:\.\d+)?\s*(?:°|องศา)(?:เซลเซียส)?\)?)/g;

// Drug names read as plain body text while their dose sits in a bold chip
// right next to them, so the name — the thing a pharmacist actually scans a
// long answer for — is the hardest part to find. Give every drug/active-
// ingredient name a dark bold treatment so "Paracetamol 10-15 mg/kg/dose"
// reads as one emphasized unit.
//
// Only applied to text OUTSIDE "[...]" blocks, "<...>" tags and bare URLs: a
// "[Ref: ... อ้างอิงจาก https://.../amoxicillin-dosing]" marker carries drug
// names inside a URL, and wrapping one in a span there would corrupt the link
// (and the data-* attributes the earlier passes already emitted).
const PROTECTED_SPANS = /(\[[^\]]*\]|<[^>]*>|https?:\/\/[^\s)\]]+)/;

function highlightOutsideMarkers(text: string, pattern: RegExp, cls: string): string {
  return text
    .split(new RegExp(PROTECTED_SPANS.source, 'g'))
    .map((seg, i) => (i % 2 === 1 ? seg : seg.replace(pattern, (m) => `<span class="${cls}">${m}</span>`)))
    .join('');
}

function renderMd(text: string): string {
  if (!text) return '';
  try {
    let processed = text.replace(CASE_TYPE_LABEL_PATTERN, '');
    processed = processed.replace(BOLD_ONLY_LINE_PATTERN, (match, inner: string) =>
      SCORE_LINE_PATTERN.test(inner) || TREATMENT_SUBLABEL_PATTERN.test(inner) || SCORE_TOOL_LABEL_PATTERN.test(inner)
        ? match
        : `### ${inner}`
    );
    processed = processed.replace(PLAIN_NUMBERED_TREATMENT_LABEL_PATTERN, (match, inner: string) =>
      `### ${inner}`
    );
    // Use a literal "<br>" here, not "\n" — these three patterns can fire
    // *inside* a deeply-nested list item's continuation text (e.g. a
    // "ขนาด: ..." sub-bullet, itself nested under a drug under a category).
    // A raw "\n" has no indentation, so CommonMark's list-continuation rule
    // sees a dedented line and ends the list right there — the rest of the
    // answer (every drug after that point) then falls out of the list
    // entirely, rendering as plain paragraphs with literal "*" characters
    // instead of bullets. "<br>" is inline raw HTML, passed through by
    // marked without ever touching block-level list parsing, so it can't
    // break list nesting no matter how deep the match sits.
    processed = processed.replace(REASON_SEPARATOR_PATTERN, '<br>');
    processed = processed.replace(DOSE_CLAUSE_SEPARATOR_PATTERN, '<br>');
    processed = processed.replace(DOSE_ALTERNATIVE_SEPARATOR_PATTERN, '<br>');
    // A space is kept after the marker (not butted directly against "**")
    // — marked's emphasis flanking rules can otherwise fail to open bold on
    // a "**" run sitting immediately after this control character, silently
    // leaving the literal "**Drug**" unrendered as plain text.
    processed = processed.replace(FIRST_DRUG_CHOICE_SEPARATOR_PATTERN, `${CHOICE_SPLIT_MARKER} `);
    processed = processed.replace(MULTI_DRUG_CHOICE_SEPARATOR_PATTERN, `${CHOICE_SPLIT_MARKER} `);

    processed = processed.replace(
      PROBABILITY_PATTERN,
      (_match, lead: string, runup: string, level: string) => {
        const cls = PROBABILITY_CLASS[level] || 'ai-prob-badge';
        if (!runup.trim()) {
          // Direct mention ("โอกาส: สูง" / "โอกาสสูง") — compact badge.
          return `<span class="${cls}" data-prob-level="${escapeHtml(level)}">โอกาส: ${escapeHtml(level)}</span>`;
        }
        // Level word appears after a run-up description (e.g. a diagnosis
        // name) — keep that text as-is and just highlight the level word.
        return `โอกาส${lead}${runup}<span class="${cls}" data-prob-level="${escapeHtml(level)}">${escapeHtml(level)}</span>`;
      }
    );

    processed = processed.replace(DOSE_PATTERN, (match) => `<span class="ai-dose-highlight">${match}</span>`);

    processed = processed.replace(
      NO_ANTIBIOTIC_PATTERN,
      (m) => `<span class="ai-no-atb">${m}</span>`
    );
    processed = processed.replace(CAUTION_PATTERN, '<span class="ai-caution-text">ข้อควรระวัง</span>');
    processed = processed.replace(PROHIBIT_PATTERN, '<span class="ai-caution-text">ห้าม</span>');

    // After the dose/caution passes (so their spans are already tags this skips)
    // and before "[Ref: ...]" becomes HTML, while the markers are still "[...]".
    processed = highlightOutsideMarkers(processed, DRUG_NAME_PATTERN, 'ai-drug-highlight');

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

// "ในทางปฏิบัติจริง (บริบทร้านยาไทย)" is the backend's Expert Opinion block —
// what a Thai pharmacist actually does at the counter, as opposed to what the
// (foreign) guideline says. Pharmacists need to spot that distinction at a
// glance, so the whole block gets a dark-green card of its own rather than
// blending into the surrounding treatment text.
const EXPERT_HEADING_PATTERN = /ปฏิบัติจริง|expert opinion|rdu/i;
// Heading of the "ask history first" reply, so it reads as a question block.
const ASK_HEADING_PATTERN = /ซักเพิ่มเติม|ซักประวัติ/;

// Best-effort emoji per heading topic, matched by keyword — purely
// decorative, so an unmatched heading just renders without one instead of
// breaking anything.
const HEADING_EMOJI: [RegExp, string][] = [
  [RED_FLAG_PATTERN, '🚨'],
  [EXPERT_HEADING_PATTERN, '💡'],
  [ASK_HEADING_PATTERN, '❓'],
  [/วินิจฉัย/, '🩺'],
  [/สรุปอาการ|อาการ(สำคัญ|หลัก)?ที่พบ|อาการนำ/, '📋'],
  [/รักษาด้วยยา|การใช้ยา(ปฏิชีวนะ|ตามอาการ)?|ยาที่แนะนำ|ยาที่ให้|ยาที่จ่าย/, '💊'],
  [/รักษาและการจัดการ|การจัดการ(เร่งด่วน)?/, '🚑'],
  [/ขนาดยา|dose/i, '⚖️'],
  [/แพ้ยา|ประวัติแพ้/, '⚠️'],
  [/ดูแลตัวเอง|การดูแลรักษาเบื้องต้น|คำแนะนำ(การดูแล|ทั่วไป)/, '🏠'],
  [/เฝ้าระวัง|ติดตามอาการ/, '👀'],
  [/ข้อซักถาม/, '❓'],
  [/หมายเหตุ/, '📌'],
  [/ส่งต่อ|พบแพทย์|refer|ไปโรงพยาบาลทันที|เหตุผล.*โรงพยาบาล/, '🏥'],
  [/ระหว่างการเดินทาง|เดินทางไปโรงพยาบาล/, '🚗'],
];

const DEFAULT_HEADING_EMOJI = '📍';

function pickHeadingEmoji(text: string): string {
  const match = HEADING_EMOJI.find(([pattern]) => pattern.test(text));
  return match ? match[1] : DEFAULT_HEADING_EMOJI;
}

function applyHeadingBadges(root: HTMLElement) {
  root.querySelectorAll('h1, h2, h3, h4').forEach((heading) => {
    let text = heading.textContent || '';
    // Strip a leading "N." or hierarchical "3a."/"3b." prefix — the backend
    // sometimes numbers sub-sections that way, which the plain \d+\. version
    // of this regex missed entirely, leaving the old prefix sitting next to
    // our own renumbered badge (e.g. "④ 3a. ยาปฏิชีวนะ"). A numbered prefix
    // *with a letter* ("3a.", "3b.") marks this as a top-level treatment
    // section, not a finer drug-category label like "ยาแก้ปวด/ลดไข้" —
    // remember that before stripping it, since applyDrugCategoryLabels
    // can't tell the two apart from the stripped text alone (both start
    // with "ยา") and shouldn't give a whole section its own drug-category
    // card treatment. The letter is required: a *bare* digit ("1. ยาแก้ปวด
    // /ลดไข้...", "2. ยาพ่นบรรเทาอาการ...") is the backend numbering ordinary
    // drug-category items as a plain list, not a "3a./3b." section — treating
    // that as a section too gave it the wrong (orange, top-level) styling.
    const hadSectionNumber = /^\d+[a-zA-Z]\./.test(text.trim());
    text = text
      .replace(/^(\d+[a-zA-Z]?)\.\s*/, '')
      .replace(/^[\p{Extended_Pictographic}‍️]+\s*/u, '')
      // The backend sometimes writes the category glyph ("■ ยาแก้ปวด...")
      // as a literal "### " heading instead of a plain paragraph — "■" isn't
      // an Extended_Pictographic emoji so the strip above leaves it sitting
      // in front of "ยา", which fails the TREATMENT_SUBLABEL_PATTERN test
      // below and lets the whole thing fall through to a real heading (pin
      // badge + boxed card) instead of the plain drug-category bullet.
      .replace(/^[■▪◾]\s*/, '');
    // The backend sometimes writes these sub-labels as a real "### " heading
    // instead of a bold-only line, which skips the raw-text exclusion above
    // entirely — catch it here too so it demotes to inline bold regardless
    // of which form the answer used.
    if (TREATMENT_SUBLABEL_PATTERN.test(text)) {
      const p = document.createElement('p');
      const strong = document.createElement('strong');
      strong.textContent = text;
      p.appendChild(strong);
      if (hadSectionNumber) p.classList.add('ai-section-sublabel');
      heading.replaceWith(p);
      return;
    }
    if (EXPERT_HEADING_PATTERN.test(text)) {
      heading.classList.add('ai-heading-expert');
    } else if (RED_FLAG_PATTERN.test(text)) {
      heading.classList.add('ai-heading-warning');
    } else if (NOTE_HEADING_PATTERN.test(text) || ASK_HEADING_PATTERN.test(text)) {
      heading.classList.add('ai-heading-note');
    }
    const emoji = pickHeadingEmoji(text);
    heading.innerHTML = `<span class="ai-heading-emoji">${emoji}</span><span>${escapeHtml(text)}</span>`;
  });
}

// Wraps a treatment section-sublabel ("3a. ยาปฏิชีวนะ...", "3b.
// ยาตามอาการ...") and everything under it — Guideline text, Expert Opinion
// block, dose table — up to the next heading or the next section-sublabel,
// into one indented block. Without this, only the label itself (and its
// orange bar) sit next to the bar; the body text underneath stayed flush
// with the rest of the answer, reading as less nested than it actually is
// under "3. การรักษาด้วยยา".
function applySectionSublabelIndent(root: HTMLElement) {
  root.querySelectorAll('p.ai-section-sublabel').forEach((label) => {
    if (label.closest('.ai-section-block')) return;
    const parent = label.parentNode;
    if (!parent) return;

    const block = document.createElement('div');
    block.className = 'ai-section-block';
    parent.insertBefore(block, label);
    block.appendChild(label);

    let node = block.nextElementSibling;
    while (node && !/^H[1-4]$/.test(node.tagName) && !node.classList.contains('ai-section-sublabel')) {
      const next = node.nextElementSibling;
      block.appendChild(node);
      node = next;
    }
  });
}

// Wraps the Expert Opinion heading and everything under it (up to the next
// heading, or the next drug-category sub-label, which belongs to the regular
// treatment section) into one green card carrying a label. Runs after
// applyHeadingBadges so the heading already has its class/emoji; moving the
// nodes into the wrapper keeps them inside `root`, so the later passes
// (diagnosis card, symptom highlights) still find them.
function applyExpertBlocks(root: HTMLElement) {
  root.querySelectorAll('h1, h2, h3, h4').forEach((heading) => {
    if (!EXPERT_HEADING_PATTERN.test(heading.textContent || '')) return;
    if (heading.closest('.ai-expert-block')) return;
    const parent = heading.parentNode;
    if (!parent) return;

    const block = document.createElement('div');
    block.className = 'ai-expert-block';
    const tag = document.createElement('div');
    tag.className = 'ai-expert-tag';
    tag.textContent = 'Expert Opinion · แนวปฏิบัติจริงหน้าร้าน';
    parent.insertBefore(block, heading);
    block.appendChild(tag);
    block.appendChild(heading);

    // The sub-label break below is what ends the block — but when the answer's
    // very first line under the heading *is* such a label ("**ยาทางเลือกแรก
    // (First-line):**", "**ยาปฏิชีวนะที่พิจารณาจ่าย:**"), it fired before
    // anything had been absorbed and the card rendered as a green frame with
    // the heading and nothing else, its content sitting outside (reported from
    // production). The label can only mean "the regular treatment section
    // resumes here" once the block actually has a body, so never break on the
    // first node — a card with one paragraph too many beats an empty one.
    let node = block.nextElementSibling;
    let absorbed = 0;
    while (node && !/^H[1-4]$/.test(node.tagName)) {
      const next = node.nextElementSibling;
      const label = node.querySelector(':scope > strong:first-child');
      if (absorbed > 0 && label && TREATMENT_SUBLABEL_PATTERN.test(label.textContent || '')) break;
      block.appendChild(node);
      absorbed += 1;
      node = next;
    }
  });

  // Same block written inline ("**ในทางปฏิบัติจริง (บริบทร้านยาไทย):** ...")
  // instead of as its own heading line — tint that element in place.
  root.querySelectorAll('p, li').forEach((el) => {
    if (el.closest('.ai-expert-block')) return;
    const label = el.querySelector(':scope > strong:first-child');
    if (label && EXPERT_HEADING_PATTERN.test(label.textContent || '')) {
      el.classList.add('ai-expert-inline');
    }
  });
}

// The backend writes a drug-category heading as "■ **label**" — a literal
// glyph character, not real markdown list syntax — so it renders as a plain
// <p>■ <strong>label</strong></p>, a sibling *before* the <ul> of drug
// items, not a list item itself. (The drug items below it are a real flat
// <ul>, all at the same level — no nesting to key off of either.) Tag it by
// its actual text instead (same "starts with ยา" check the backend's own
// wording follows — a category label always starts with "ยา", a drug/
// product name never does), and drop the leading glyph text node since the
// CSS heading-bar supplies its own visual marker.
const CATEGORY_LEAD_GLYPH_PATTERN = /^[■▪◾]\s*$/;

function applyDrugCategoryLabels(root: HTMLElement) {
  const tagIfCategory = (container: HTMLElement, strong: HTMLElement | null) => {
    if (container.classList.contains('ai-section-sublabel')) return;
    if (!strong || !TREATMENT_SUBLABEL_PATTERN.test(strong.textContent || '')) return;
    container.classList.add('ai-drug-category');
    const lead = container.firstChild;
    if (lead && lead.nodeType === Node.TEXT_NODE && CATEGORY_LEAD_GLYPH_PATTERN.test(lead.textContent || '')) {
      container.removeChild(lead);
    }
  };

  root.querySelectorAll('p').forEach((p) => {
    // A "loose" list (one with a blank line between items — common once a
    // category has its own sub-list of drugs under it) makes marked wrap
    // each <li>'s own text in a <p>, so this same paragraph is *also* the
    // first child of an <li> that the loop below will tag. Tagging both
    // gave the <li> its native bullet *and* this inner <p> its own "•"
    // pseudo-element — two dots stacked in front of one label. The <li>
    // loop already reaches this label's text via the "> p:first-child >
    // strong" fallback, so skip it here and let that be the only tag.
    if (p.parentElement?.tagName === 'LI' && p.parentElement.firstElementChild === p) return;
    tagIfCategory(p, p.querySelector(':scope > strong:first-child'));
  });
  root.querySelectorAll('li').forEach((li) => {
    tagIfCategory(li, li.querySelector(':scope > strong:first-child, :scope > p:first-child > strong:first-child'));
  });
}

// Cuts a rendered <li> into separate sibling <li>s at each CHOICE_SPLIT_MARKER
// (inserted by FIRST/MULTI_DRUG_CHOICE_SEPARATOR_PATTERN above) — done here,
// post-parse, so the split works regardless of what inline markup (bold,
// dose-highlight spans, ref tags, ...) marked produced around it, rather
// than trying to cut the markdown source itself. Walks the <li>'s direct
// children, starting a new group every time a text node containing the
// marker is found (splitting *within* that text node too, since the marker
// sits in running prose, never inside a nested element).
function splitDrugChoiceListItems(root: HTMLElement) {
  root.querySelectorAll('li').forEach((li) => {
    if (!(li.textContent || '').includes(CHOICE_SPLIT_MARKER)) return;
    const groups: ChildNode[][] = [[]];
    li.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE && (node.textContent || '').includes(CHOICE_SPLIT_MARKER)) {
        const parts = (node.textContent || '').split(CHOICE_SPLIT_MARKER);
        parts.forEach((part, i) => {
          if (i > 0) groups.push([]);
          if (part) groups[groups.length - 1].push(document.createTextNode(part));
        });
      } else {
        groups[groups.length - 1].push(node);
      }
    });
    // A group with only whitespace/punctuation left over (e.g. a trailing
    // ", " after the split point was consumed) isn't a real choice — drop it
    // rather than leaving a stray empty bullet.
    const realGroups = groups.filter((nodes) => nodes.some((n) => (n.textContent || '').trim()));
    if (realGroups.length < 2) return;
    const parent = li.parentNode;
    if (!parent) return;
    // The first fragment keeps the lead-in sentence ("กลุ่ม NSAIDs
    // (ทางเลือก): ใช้กรณี Paracetamol ไม่เพียงพอ เช่น") and reads as the
    // regular top-level bullet it always was; every fragment after that is
    // one of the choices split out of it, so those are indented with a
    // hollow-circle marker to read as sub-choices under that lead-in rather
    // than more top-level drug entries.
    realGroups.forEach((nodes, i) => {
      const newLi = document.createElement('li');
      if (i > 0) newLi.classList.add('ai-choice-item');
      nodes.forEach((n) => newLi.appendChild(n));
      parent.insertBefore(newLi, li);
    });
    parent.removeChild(li);
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

// A fever reading only gets highlighted inside the "สรุปอาการ" (symptom
// summary) section — the same word shows up harmlessly elsewhere in the
// answer (dose notes, red-flag lists), and highlighting those too just adds
// noise where it isn't a new fact worth a second look.
function applySymptomHighlights(root: HTMLElement) {
  root.querySelectorAll('h1, h2, h3, h4').forEach((heading) => {
    if (!/สรุปอาการ/.test(heading.textContent || '')) return;
    let node = heading.nextElementSibling;
    while (node && !/^H[1-4]$/.test(node.tagName)) {
      node.innerHTML = node.innerHTML.replace(FEVER_PATTERN, (m) => `<span class="ai-alert-highlight">${m}</span>`);
      node = node.nextElementSibling;
    }
  });
}

// The AI's "การวินิจฉัยเบื้องต้น" (initial diagnosis) list stays exactly as
// the AI wrote it (plain badge, no dot/dot-bar clutter) — the dot + 4-dot
// probability bar only shows up in a separate recap card appended below the
// list, so the original answer text is never touched.
const PROB_LEVELS = ['vhigh', 'high', 'mid', 'low', 'vlow'] as const;

function buildDotBar(level: (typeof PROB_LEVELS)[number], count: number): HTMLElement {
  const bar = document.createElement('span');
  bar.className = 'ai-prob-dotbar';
  for (let i = 0; i < PROBABILITY_DOT_TOTAL; i++) {
    const d = document.createElement('span');
    d.className = `ai-prob-dotbar-item${i < count ? ` filled ai-prob-dot-${level}` : ''}`;
    bar.appendChild(d);
  }
  return bar;
}

function applyDiagnosisCard(root: HTMLElement) {
  root.querySelectorAll('h1, h2, h3, h4').forEach((heading) => {
    if (!/วินิจฉัย/.test(heading.textContent || '')) return;

    let node = heading.nextElementSibling;
    let list: Element | null = null;
    let lastSibling: Element | null = null;
    while (node && !/^H[1-4]$/.test(node.tagName)) {
      if (!list && /^(UL|OL)$/.test(node.tagName)) list = node;
      // Already ran once against this heading (StrictMode's double effect
      // invoke in dev re-runs this on the same, already-mutated DOM) —
      // the card this same call would produce is already sitting right
      // here as a later sibling, so stop before appending a second copy.
      if (node.classList.contains('ai-diagnosis-card')) return;
      lastSibling = node;
      node = node.nextElementSibling;
    }
    if (!list || !lastSibling) return;

    const rows: HTMLElement[] = [];
    list.querySelectorAll(':scope > li').forEach((li) => {
      const strong = li.querySelector('strong');
      const badge = li.querySelector('.ai-prob-badge');
      const level = badge && PROB_LEVELS.find((l) => badge.classList.contains(`ai-prob-${l}`));
      if (!strong || !badge || !level) return;

      const row = document.createElement('div');
      row.className = 'ai-diagnosis-card-row';

      const dot = document.createElement('span');
      dot.className = `ai-prob-dot ai-prob-dot-${level}`;
      row.appendChild(dot);

      const name = document.createElement('span');
      name.className = 'ai-diagnosis-card-name';
      name.textContent = strong.textContent || '';
      row.appendChild(name);

      const word = badge.getAttribute('data-prob-level') || '';
      const count = PROBABILITY_DOTS[word];
      if (count !== undefined) row.appendChild(buildDotBar(level, count));

      row.appendChild(badge.cloneNode(true));
      rows.push(row);
    });
    if (!rows.length) return;

    const card = document.createElement('div');
    card.className = 'ai-diagnosis-card';
    const title = document.createElement('div');
    title.className = 'ai-diagnosis-card-title';
    title.textContent = 'สรุปการวินิจฉัย';
    card.appendChild(title);
    rows.forEach((row) => card.appendChild(row));

    // Static color-key row (สูงมาก/สูง/ปานกลาง/ต่ำ/ต่ำมาก) — labels only, not
    // clickable filters, just explains what each dot/badge color means.
    const legend = document.createElement('div');
    legend.className = 'ai-diagnosis-legend';
    const legendLabel = document.createElement('span');
    legendLabel.className = 'ai-diagnosis-legend-label';
    legendLabel.textContent = 'โอกาสเป็น:';
    legend.appendChild(legendLabel);
    (['สูงมาก', 'สูง', 'ปานกลาง', 'ต่ำ', 'ต่ำมาก'] as const).forEach((word) => {
      const pillLevel = PROBABILITY_CLASS[word].split(' ')[1].replace('ai-prob-', '') as (typeof PROB_LEVELS)[number];
      const pill = document.createElement('span');
      pill.className = `ai-prob-badge ai-prob-${pillLevel}`;
      pill.textContent = word;
      legend.appendChild(pill);
    });
    card.appendChild(legend);

    lastSibling!.after(card);
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
    applyExpertBlocks(el);
    applySectionSublabelIndent(el);
    splitDrugChoiceListItems(el);
    applyDrugCategoryLabels(el);
    applyNoteHighlights(el);
    applyDiagnosisCard(el);
    applySymptomHighlights(el);
  }, [content]);

  return (
    <div ref={ref} className={className} dangerouslySetInnerHTML={{ __html: renderMd(content) }} />
  );
}
