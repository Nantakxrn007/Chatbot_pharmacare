function escapeHtml(text: string): string {
  const d = document.createElement('div');
  d.textContent = text || '';
  return d.innerHTML;
}

/** Lightweight markdown renderer matching the original testcase.html regex-based one. */
export function renderTestCaseMarkdown(text: string): string {
  if (!text) return '';
  let html = escapeHtml(text);

  html = html.replace(/^## 🔍(.+)$/gm, '<h2 class="flex items-center gap-2 text-base font-bold text-slate-800 mt-5 mb-2 pb-1 border-b border-slate-200"><span class="text-xl">🔍</span>$1</h2>');
  html = html.replace(/^## 💊(.+)$/gm, '<h2 class="flex items-center gap-2 text-base font-bold text-emerald-700 mt-5 mb-2 pb-1 border-b border-emerald-100"><span class="text-xl">💊</span>$1</h2>');
  html = html.replace(/^## ⚠️(.+)$/gm, '<h2 class="flex items-center gap-2 text-base font-bold text-amber-700 mt-5 mb-2 pb-1 border-b border-amber-100"><span class="text-xl">⚠️</span>$1</h2>');
  html = html.replace(/^## 📚(.+)$/gm, '<h2 class="flex items-center gap-2 text-sm font-semibold text-slate-500 mt-5 mb-2"><span>📚</span>$1</h2>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-base font-bold text-slate-800 mt-4 mb-2 pb-1 border-b border-slate-200">$1</h2>');
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-sm font-semibold text-slate-700 mt-3 mb-1.5">$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold text-slate-900 mt-4 mb-2">$1</h1>');

  html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-slate-900">$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em class="italic text-slate-700">$1</em>');

  html = html.replace(/`([^`]+)`/g, '<code class="bg-slate-100 text-emerald-700 px-1.5 py-0.5 rounded text-xs font-mono">$1</code>');

  html = html.replace(/^&gt; (.+)$/gm, '<blockquote class="border-l-3 border-emerald-400 pl-3 my-2 text-slate-600 italic text-sm">$1</blockquote>');

  html = html.replace(/^---$/gm, '<hr class="my-4 border-slate-200">');

  html = html.replace(/(^- .+$\n?)+/gm, (match) => {
    const items = match
      .trim()
      .split('\n')
      .map((l) => `<li class="flex gap-2 mb-1"><span class="text-emerald-500 mt-0.5 flex-shrink-0">•</span><span>${l.replace(/^- /, '')}</span></li>`)
      .join('');
    return `<ul class="my-2 space-y-0.5">${items}</ul>`;
  });

  html = html.replace(/(^\d+\. .+$\n?)+/gm, (match) => {
    let idx = 1;
    const items = match
      .trim()
      .split('\n')
      .map((l) => `<li class="flex gap-2 mb-1"><span class="text-emerald-600 font-semibold flex-shrink-0 w-4">${idx++}.</span><span>${l.replace(/^\d+\. /, '')}</span></li>`)
      .join('');
    return `<ol class="my-2 space-y-0.5">${items}</ol>`;
  });

  html = html.replace(/\n\n+/g, '</p><p class="mb-2 leading-relaxed">');
  html = html.replace(/\n/g, '<br>');
  html = `<p class="mb-2 leading-relaxed">${html}</p>`;
  html = html.replace(/<p[^>]*>\s*<\/p>/g, '');

  return html;
}
