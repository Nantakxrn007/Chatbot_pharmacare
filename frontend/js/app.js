// ═══ Auth ═══════════════════════════════════════════════════════════════════
const token = localStorage.getItem('token');
if (!token) { window.location.href = '/login'; }
const AUTH = { headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' } };

// Check token validity
fetch('/api/me', { headers: AUTH.headers })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(data => {
        document.getElementById('userDisplayName').textContent = data.display_name || data.username;
        document.getElementById('userAvatar').textContent = (data.display_name || data.username).charAt(0).toUpperCase();
    })
    .catch(() => { localStorage.clear(); window.location.href = '/login'; });

function handleLogout() {
    fetch('/api/logout', { method: 'POST' });
    localStorage.clear();
    window.location.href = '/login';
}

// ═══ State ══════════════════════════════════════════════════════════════════
let currentSessionId = null;
let currentPatientName = null;
let isLoading = false;
let pendingMessage = null; // Bugfix: Store question when creating a new chat
let currentPromptTokens = 0;
let currentCompletionTokens = 0;

// ═══ New Chat Modal ════════════════════════════════════════════════════════
let nameCheckTimeout = null;

function openNewChatModal() {
    document.getElementById('newChatModal').classList.add('show');
    const inp = document.getElementById('patientNameInput');
    inp.value = '';
    document.getElementById('nameWarning').classList.remove('show');
    setTimeout(() => inp.focus(), 100);
    // Add live name check
    inp.oninput = () => {
        clearTimeout(nameCheckTimeout);
        document.getElementById('nameWarning').classList.remove('show');
        if (inp.value.trim().length > 0) {
            nameCheckTimeout = setTimeout(() => checkPatientName(inp.value.trim()), 400);
        }
    };
    inp.onkeydown = (e) => { if (e.key === 'Enter') confirmCreateSession(); };
}

function closeNewChatModal() {
    document.getElementById('newChatModal').classList.remove('show');
}

async function checkPatientName(name) {
    try {
        const r = await fetch(`/api/patients/check-name?name=${encodeURIComponent(name)}`, { headers: AUTH.headers });
        const data = await r.json();
        if (data.exists) {
            document.getElementById('nameWarning').classList.add('show');
        } else {
            document.getElementById('nameWarning').classList.remove('show');
        }
    } catch (e) { }
}

async function confirmCreateSession() {
    const name = document.getElementById('patientNameInput').value.trim();
    if (!name) { document.getElementById('patientNameInput').focus(); return; }

    // Check duplicate name
    try {
        const r = await fetch(`/api/patients/check-name?name=${encodeURIComponent(name)}`, { headers: AUTH.headers });
        const data = await r.json();
        if (data.exists) {
            document.getElementById('nameWarning').classList.add('show');
            return;
        }
    } catch (e) { }

    closeNewChatModal();
    try {
        const r = await fetch('/api/sessions', { method: 'POST', ...AUTH, body: JSON.stringify({ patient_name: name, title: name }) });
        const s = await r.json();
        currentSessionId = s.id;
        currentPatientName = s.patient_name || name;
        document.getElementById('chatTitle').textContent = currentPatientName;
        document.getElementById('chatSubtitle').textContent = `ผู้ป่วย: ${currentPatientName} | อัปเดตล่าสุด: เพิ่งสร้าง`;
        updateDashboardLink();
        clearChat(); showWelcome(true); loadSessions();

        // 🚨 Bugfix: Auto-send the pending message if exists
        if (pendingMessage) {
            const msg = pendingMessage;
            pendingMessage = null; // Clear it
            document.getElementById('chatInput').value = msg;
            sendMessage();
        }
    } catch (e) { console.error(e); }
}

function updateDashboardLink() {
    const link = document.getElementById('dashboardLink');
    if (currentPatientName && currentPatientName !== 'แชทใหม่') {
        link.href = `/patient/${encodeURIComponent(currentPatientName)}`;
        link.style.display = 'flex';
    } else {
        link.style.display = 'none';
    }
}

// ═══ Sessions ══════════════════════════════════════════════════════════════
async function loadSessions() {
    try {
        const r = await fetch('/api/sessions', { headers: AUTH.headers });
        if (!r.ok) return;
        renderSessionList(await r.json());
    } catch (e) { console.error(e); }
}

function renderSessionList(list) {
    const el = document.getElementById('sessionList');
    if (!list.length) {
        el.innerHTML = '<div style="text-align:center;padding:2rem 0;color:#94a3b8;font-size:0.75rem"><p>ยังไม่มีแชท</p><p style="margin-top:0.3rem">กดปุ่ม "แชทใหม่" เพื่อเริ่มต้น</p></div>';
        return;
    }
    el.innerHTML = list.map(s => {
        let dateStr = '';
        if (s.updated_at) {
            try { dateStr = new Date(s.updated_at).toLocaleString('th-TH', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch(e){}
        }
        return `
<div class="session-item flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-all duration-200 border border-transparent ${s.id === currentSessionId ? 'bg-emerald-50 border-emerald-200 shadow-sm' : 'hover:bg-gray-50 hover:border-gray-200'}" onclick="switchSession('${s.id}')">
    <div class="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
    </div>
    <div style="flex:1;overflow:hidden;display:flex;flex-direction:column;">
        <span class="title text-sm font-semibold ${s.id === currentSessionId ? 'text-emerald-800' : 'text-gray-700'}">${esc(s.patient_name || s.title)}</span>
        <span style="font-size:0.65rem;color:#94a3b8;margin-top:2px;">${dateStr}</span>
    </div>
    <div class="actions flex gap-1 opacity-0 transition-opacity">
        <button class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded" onclick="event.stopPropagation();renameChat('${s.id}')" title="เปลี่ยนชื่อ">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
        </button>
        <button class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded" onclick="event.stopPropagation();deleteChat('${s.id}')" title="ลบ">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
        </button>
    </div>
</div>
`}).join('');
}

async function switchSession(id) {
    currentSessionId = id;
    try {
        const r = await fetch(`/api/sessions/${id}`, { headers: AUTH.headers });
        const s = await r.json();
        currentPatientName = s.patient_name || s.title;
        let dateStr = '';
        if (s.updated_at) {
            try { dateStr = new Date(s.updated_at).toLocaleString('th-TH', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch(e){}
        }
        document.getElementById('chatTitle').textContent = currentPatientName;
        document.getElementById('chatSubtitle').textContent = `ผู้ป่วย: ${currentPatientName}` + (dateStr ? ` | อัปเดตล่าสุด: ${dateStr}` : '');
        document.getElementById('patientActions').style.display = 'flex'; // Show context buttons
        
        updateDashboardLink();
        clearChat();
        currentPromptTokens = 0;
        currentCompletionTokens = 0;
        if (s.messages?.length) {
            showWelcome(false);
            s.messages.forEach(m => {
                appendMessage(m.role, m.content, m.sources, false, m.timestamp);
                currentPromptTokens += (m.prompt_tokens || 0);
                currentCompletionTokens += (m.completion_tokens || 0);
            });
            scrollToBottom();
            document.getElementById('suggestedQuestions').style.display = 'flex';
        } else { 
            showWelcome(true); 
            document.getElementById('suggestedQuestions').style.display = 'none';
        }
        loadSessions();
        if (window.innerWidth < 768) toggleSidebar();
    } catch (e) { console.error(e); }
}

async function deleteChat(id) {
    if (!confirm('ลบแชทนี้?')) return;
    await fetch(`/api/sessions/${id}`, { method: 'DELETE', headers: AUTH.headers });
    if (currentSessionId === id) { currentSessionId = null; currentPatientName = null; clearChat(); showWelcome(true); document.getElementById('chatTitle').textContent = 'เลือกหรือสร้างแชทใหม่'; document.getElementById('chatSubtitle').textContent = 'RAG-powered Pharmacy Assistant'; updateDashboardLink(); }
    loadSessions();
}

async function renameChat(id) {
    const t = prompt('ตั้งชื่อแชทใหม่:');
    if (!t?.trim()) return;
    await fetch(`/api/sessions/${id}`, { method: 'PATCH', ...AUTH, body: JSON.stringify({ title: t.trim() }) });
    if (currentSessionId === id) { document.getElementById('chatTitle').textContent = t.trim(); currentPatientName = t.trim(); updateDashboardLink(); }
    loadSessions();
}

async function searchSessions(q) {
    try {
        const r = await fetch(`/api/sessions/search?q=${encodeURIComponent(q)}`, { headers: AUTH.headers });
        if (r.ok) renderSessionList(await r.json());
    } catch (e) { }
}

// ═══ Chat ═══════════════════════════════════════════════════════════════════
async function sendMessage() {
    const inp = document.getElementById('chatInput');
    const msg = inp.value.trim();
    if (!msg || isLoading) return;

    isLoading = true; updateSendBtn(); inp.value = ''; autoResize(inp);

    if (!currentSessionId) {
        // Auto-create session with prompt to name patient
        pendingMessage = msg; // Store message for later
        openNewChatModal();
        isLoading = false; updateSendBtn();
        return;
    }
    showWelcome(false);

    appendMessage('user', msg);
    scrollToBottom();
    showTyping(true);

    try {
        const r = await fetch('/api/chat/stream', { method: 'POST', ...AUTH, body: JSON.stringify({ session_id: currentSessionId, message: msg }) });
        if (!r.ok) throw new Error((await r.json()).detail || 'API Error');

        showTyping(false);
        const msgId = 'msg-' + Date.now();
        appendPlaceholder(msgId);
        const contentDiv = document.getElementById(msgId + '-content');
        let full = '', buffer = '';
        const reader = r.body.getReader(), decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
                if (!line.trim().startsWith('data: ')) continue;
                try {
                    const d = JSON.parse(line.trim().slice(6));
                    if (d.type === 'session') currentSessionId = d.session_id;
                    else if (d.type === 'chunk') { full += d.content; contentDiv.innerHTML = renderMd(full); scrollToBottom(); }
                    else if (d.type === 'done') { 
                        updateSources(msgId, d.sources); 
                        if (d.usage) {
                            currentPromptTokens += (d.usage.prompt_tokens || 0);
                            currentCompletionTokens += (d.usage.completion_tokens || 0);
                        }
                        loadSessions(); 
                        document.getElementById('suggestedQuestions').style.display = 'flex';
                    }
                    else if (d.type === 'error') { contentDiv.innerHTML += `<p style="color:#ef4444;margin-top:0.5rem">${esc(d.content)}</p>`; }
                } catch (e) { }
            }
        }
    } catch (e) {
        showTyping(false);
        appendMessage('assistant', `❌ เกิดข้อผิดพลาด: ${e.message}`);
    } finally {
        isLoading = false; updateSendBtn(); scrollToBottom();
    }
}

function quickAsk(q) {
    if (!currentSessionId) {
        pendingMessage = q; // Store message for later
        openNewChatModal();
        return;
    }
    document.getElementById('chatInput').value = q; sendMessage();
}

// ═══ Edit & Regenerate ═════════════════════════════════════════════════════
async function editLastMessage() {
    if (!currentSessionId || isLoading) return;
    const rows = document.querySelectorAll('.msg-row.user');
    if (!rows.length) return;
    const lastRow = rows[rows.length - 1];
    const originalText = lastRow.querySelector('.msg-bubble-user')?.textContent?.trim() || '';

    const newText = prompt('แก้ไขข้อความ:', originalText);
    if (newText === null || newText.trim() === '' || newText.trim() === originalText) return;

    const allRows = [...document.querySelectorAll('.msg-row')];
    for (let i = allRows.length - 1; i >= 0; i--) {
        if (allRows[i].classList.contains('assistant')) { allRows[i].remove(); break; }
    }
    for (let i = allRows.length - 1; i >= 0; i--) {
        if (allRows[i].classList.contains('user')) { allRows[i].remove(); break; }
    }

    isLoading = true; updateSendBtn();
    appendMessage('user', newText.trim());
    showTyping(true); scrollToBottom();

    try {
        const r = await fetch('/api/chat/edit', { method: 'POST', ...AUTH, body: JSON.stringify({ session_id: currentSessionId, message: newText.trim() }) });
        showTyping(false);
        const msgId = 'msg-' + Date.now();
        appendPlaceholder(msgId);
        const contentDiv = document.getElementById(msgId + '-content');
        let full = '', buffer = '';
        const reader = r.body.getReader(), decoder = new TextDecoder();
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n'); buffer = lines.pop() || '';
            for (const line of lines) {
                if (!line.trim().startsWith('data: ')) continue;
                try {
                    const d = JSON.parse(line.trim().slice(6));
                    if (d.type === 'chunk') { full += d.content; contentDiv.innerHTML = renderMd(full); scrollToBottom(); }
                    else if (d.type === 'done') { 
                        updateSources(msgId, d.sources); 
                        if (d.usage) {
                            currentPromptTokens += (d.usage.prompt_tokens || 0);
                            currentCompletionTokens += (d.usage.completion_tokens || 0);
                        }
                        loadSessions(); 
                    }
                } catch (e) { }
            }
        }
    } catch (e) { showTyping(false); appendMessage('assistant', `❌ ${e.message}`); }
    finally { isLoading = false; updateSendBtn(); }
}

async function regenerate() {
    if (!currentSessionId || isLoading) return;
    const rows = [...document.querySelectorAll('.msg-row.assistant')];
    if (rows.length) rows[rows.length - 1].remove();

    isLoading = true; updateSendBtn(); showTyping(true); scrollToBottom();

    try {
        const r = await fetch('/api/chat/regenerate', { method: 'POST', ...AUTH, body: JSON.stringify({ session_id: currentSessionId }) });
        showTyping(false);
        const msgId = 'msg-' + Date.now();
        appendPlaceholder(msgId);
        const contentDiv = document.getElementById(msgId + '-content');
        let full = '', buffer = '';
        const reader = r.body.getReader(), decoder = new TextDecoder();
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n'); buffer = lines.pop() || '';
            for (const line of lines) {
                if (!line.trim().startsWith('data: ')) continue;
                try {
                    const d = JSON.parse(line.trim().slice(6));
                    if (d.type === 'chunk') { full += d.content; contentDiv.innerHTML = renderMd(full); scrollToBottom(); }
                    else if (d.type === 'done') { 
                        updateSources(msgId, d.sources); 
                        if (d.usage) {
                            currentPromptTokens += (d.usage.prompt_tokens || 0);
                            currentCompletionTokens += (d.usage.completion_tokens || 0);
                        }
                        loadSessions(); 
                    }
                } catch (e) { }
            }
        }
    } catch (e) { showTyping(false); appendMessage('assistant', `❌ ${e.message}`); }
    finally { isLoading = false; updateSendBtn(); }
}

// ═══ UI Rendering ══════════════════════════════════════════════════════════
function appendMessage(role, content, sources, animate = true, timestamp) {
    const c = document.getElementById('chatMessages');
    const d = document.createElement('div');
    const t = timestamp ? new Date(timestamp).toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' }) : new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
    d.className = `msg-row ${role} ${animate ? 'msg-enter' : ''}`;

    if (role === 'user') {
        d.innerHTML = `<div style="max-width:75%"><div class="msg-bubble-user">${esc(content)}</div><div class="msg-time">${t}</div><div class="msg-user-actions"><button class="msg-action-btn" onclick="editLastMessage()">✏️ แก้ไข</button></div></div>`;
    } else if (role === 'system') {
        d.className = `msg-row assistant ${animate ? 'msg-enter' : ''}`;
        d.innerHTML = `<div class="msg-bubble-ai"><div class="ai-avatar">📋</div><div class="ai-content"><div class="ai-text" style="color:#64748b;font-style:italic;font-size:0.82rem">${renderMd(content)}</div><div class="msg-time">${t}</div></div></div>`;
    } else {
        const srcHtml = sources?.length ? `<div class="sources-row"><span class="sources-label">📎 อ้างอิง:</span>${sources.map(s => `<span class="source-tag" onclick="openPDF('${escAttr(s.source)}', '${escAttr(s.page || '')}', '${escAttr(s.type || '')}', '${escAttr(s.heading || '')}')">${s.type === 'external' ? '🌐' : '📄'} ${esc(s.source)}${s.page ? ' p.' + s.page : ''}${s.heading ? ' — ' + esc(s.heading).substring(0, 25) : ''}</span>`).join('')}</div>` : '';
        d.innerHTML = `<div class="msg-bubble-ai"><div class="ai-avatar">💊</div><div class="ai-content"><div class="ai-text">${renderMd(content)}</div>${srcHtml}<div class="ai-actions"><button class="msg-action-btn" onclick="copyMsg(this)">📋 คัดลอก</button><button class="msg-action-btn" onclick="regenerate()">🔄 สร้างใหม่</button></div><div class="msg-time">${t}</div></div></div>`;
    }
    c.appendChild(d);
}

function appendPlaceholder(id) {
    const c = document.getElementById('chatMessages');
    const d = document.createElement('div');
    d.className = 'msg-row assistant msg-enter'; d.id = id;
    d.innerHTML = `<div class="msg-bubble-ai"><div class="ai-avatar">💊</div><div class="ai-content"><div class="ai-text" id="${id}-content"><div class="typing-dots"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div><div id="${id}-sources"></div><div id="${id}-actions" class="ai-actions" style="display:none"><button class="msg-action-btn" onclick="copyMsg(this)">📋 คัดลอก</button><button class="msg-action-btn" onclick="regenerate()">🔄 สร้างใหม่</button></div><div class="msg-time">${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}</div></div></div>`;
    c.appendChild(d);
}

function updateSources(id, sources) {
    const el = document.getElementById(id + '-sources');
    const actEl = document.getElementById(id + '-actions');
    if (actEl) actEl.style.display = 'flex';
    if (!el || !sources?.length) return;
    el.innerHTML = `<div class="sources-row"><span class="sources-label">📎 อ้างอิง:</span>${sources.map(s => `<span class="source-tag" onclick="openPDF('${escAttr(s.source)}', '${escAttr(s.page || '')}', '${escAttr(s.type || '')}', '${escAttr(s.heading || '')}')">${s.type === 'external' ? '🌐' : '📄'} ${esc(s.source)}${s.page ? ' p.' + s.page : ''}${s.heading ? ' — ' + esc(s.heading).substring(0, 25) : ''}</span>`).join('')}</div>`;
}

function showTyping(show) {
    let el = document.getElementById('typingIndicator');
    if (show) {
        if (el) return;
        const c = document.getElementById('chatMessages');
        const d = document.createElement('div');
        d.id = 'typingIndicator'; d.className = 'msg-row assistant msg-enter';
        d.innerHTML = `<div class="msg-bubble-ai"><div class="ai-avatar">💊</div><div class="ai-content" style="padding:0.7rem 1rem"><div class="typing-dots" style="display:flex;align-items:center;gap:6px"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div><span style="font-size:0.75rem;color:#94a3b8;margin-left:6px">กำลังค้นหาและวิเคราะห์...</span></div></div></div>`;
        c.appendChild(d); scrollToBottom();
    } else { if (el) el.remove(); }
}

function showWelcome(s) { const w = document.getElementById('welcomeScreen'); if (w) w.style.display = s ? 'flex' : 'none'; }
function clearChat() { const c = document.getElementById('chatMessages'), w = document.getElementById('welcomeScreen'); c.innerHTML = ''; if (w) c.appendChild(w); }

// ═══ Copy ═══════════════════════════════════════════════════════════════════
function copyMsg(btn) {
    const text = btn.closest('.ai-content').querySelector('.ai-text')?.innerText || '';
    navigator.clipboard.writeText(text).then(() => showToast('คัดลอกแล้ว ✓'));
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2000);
}

// ═══ Utilities ═════════════════════════════════════════════════════════════
function esc(t) { const d = document.createElement('div'); d.textContent = t || ''; return d.innerHTML; }
function escAttr(t) {
    // สำหรับใส่ใน onclick='...' — กัน ', ", \, newline พัง JS
    return String(t || '')
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"')
        .replace(/\r?\n/g, ' ');
}

function goToPatientSummary() {
    if (currentPatientName && currentPatientName !== 'แชทใหม่') {
        window.location.href = `/patient/${encodeURIComponent(currentPatientName)}`;
    } else {
        showToast('กรุณาเลือกแชทผู้ป่วยที่มีชื่อก่อนครับ');
    }
}

function openPDF(source, page, type, heading) {
    if (type === 'external') {
        Swal.fire({
            icon: 'info',
            title: 'ความรู้นอกเอกสารอ้างอิง',
            html: `ข้อมูลนี้เป็นความรู้ทางการแพทย์ทั่วไปที่ AI นำมาใช้ประกอบคำตอบ<br><br><b style="color:#10b981">แหล่งที่มาอ้างอิง:</b> ${source}`,
            confirmButtonText: 'รับทราบ',
            confirmButtonColor: '#10b981',
            background: '#f8fafc',
            customClass: {
                popup: 'rounded-xl',
                title: 'text-xl font-bold text-slate-800'
            }
        });
        return;
    }

    let filename = "";
    const srcUpper = (source || "").toUpperCase();
    if (srcUpper.includes("AAFP")) {
        filename = "AAFP_2022_Original.pdf";
    } else if (srcUpper.includes("URI")) {
        filename = "P2_URI.pdf";
    } else if (srcUpper.includes("DOSE")) {
        // page ใน chunk = หน้า Dose supportive.pdf
        filename = "Dose supportive.pdf";
    } else {
        filename = source + ".pdf";
    }

    // Chrome PDF viewer อ่านได้ชัวร์แค่ #page=N
    // ห้ามต่อ &search=... หลัง hash — จะทำให้กระโดดไปหน้า 1
    const pageNum = String(page || "").replace(/\D/g, "");
    const hash = pageNum ? `#page=${pageNum}` : "";
    const pdfUrl = `/data/${encodeURI(filename)}?t=${Date.now()}${hash}`;

    const panel = document.getElementById('pdfPanel');
    const iframe = document.getElementById('pdfIframe');
    const title = document.getElementById('pdfTitle');

    const label = pageNum ? `หน้า ${pageNum}` : "PDF";
    title.textContent = `📄 ${source} (${label})`;
    if (heading) {
        title.title = heading; // tooltip เท่านั้น ไม่ใส่ใน URL
    }

    // Force reload เพื่อให้ hash #page ทำงานทุกครั้ง
    iframe.src = "about:blank";
    setTimeout(() => {
        iframe.src = pdfUrl;
    }, 50);

    panel.classList.add("open");
}

function closePDF() {
    const panel = document.getElementById('pdfPanel');
    panel.classList.remove('open');
    panel.style.width = ''; // Reset custom width from resizer
    setTimeout(() => {
        document.getElementById('pdfIframe').src = "";
    }, 300);
}

// ═══ Token Summary ═══
function openTokenSummaryModal() {
    const totalTokens = currentPromptTokens + currentCompletionTokens;
    // Rate: 1M prompt = $0.25, 1M completion = $1.50. 1 USD = 35 THB
    const promptCostUsd = (currentPromptTokens / 1000000) * 0.25;
    const completionCostUsd = (currentCompletionTokens / 1000000) * 1.50;
    const totalCostThb = (promptCostUsd + completionCostUsd) * 35;

    document.getElementById('tsPromptTokens').textContent = currentPromptTokens.toLocaleString() + ' Tokens';
    document.getElementById('tsCompletionTokens').textContent = currentCompletionTokens.toLocaleString() + ' Tokens';
    document.getElementById('tsTotalTokens').textContent = totalTokens.toLocaleString() + ' Tokens';
    document.getElementById('tsTotalCost').textContent = '฿' + totalCostThb.toFixed(4);

    document.getElementById('tokenSummaryModal').classList.add('show');
}

function closeTokenSummaryModal() {
    document.getElementById('tokenSummaryModal').classList.remove('show');
}

// ═══ Global Token Modal ═══
async function openGlobalTokenModal() {
    document.getElementById('globalTokenModal').classList.add('show');
    
    // Set default month to current month if not set
    const monthInput = document.getElementById('gtMonthFilter');
    if (!monthInput.value) {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        monthInput.value = `${year}-${month}`;
    }
    
    await loadGlobalTokens(monthInput.value);
}

async function loadGlobalTokens(month) {
    document.getElementById('gtTotalTokens').textContent = 'กำลังโหลด...';
    document.getElementById('gtTotalCost').textContent = '฿0.0000';
    document.getElementById('gtTableBody').innerHTML = '<tr><td colspan="3" class="px-4 py-6 text-center text-gray-400">กำลังโหลดข้อมูล...</td></tr>';
    
    try {
        const url = month ? `/api/tokens/summary?month=${encodeURIComponent(month)}` : '/api/tokens/summary';
        const r = await fetch(url, { headers: AUTH.headers });
        const data = await r.json();
        
        const totalTokens = data.total_prompt + data.total_completion;
        const totalCostThb = ((data.total_prompt / 1000000 * 0.25) + (data.total_completion / 1000000 * 1.50)) * 35;
        
        document.getElementById('gtTotalTokens').textContent = totalTokens.toLocaleString();
        document.getElementById('gtTotalCost').textContent = '฿' + totalCostThb.toFixed(4);
        
        const tbody = document.getElementById('gtTableBody');
        if (!data.sessions || data.sessions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" class="px-4 py-6 text-center text-gray-400">ยังไม่มีข้อมูลการใช้งานในเดือนนี้</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.sessions.map(s => {
            const rowTokens = (s.total_prompt || 0) + (s.total_completion || 0);
            const rowCost = (((s.total_prompt || 0) / 1000000 * 0.25) + ((s.total_completion || 0) / 1000000 * 1.50)) * 35;
            return `
            <tr class="hover:bg-gray-50 transition-colors">
                <td class="px-4 py-3 font-medium text-gray-800">${esc(s.patient_name || s.title || 'แชทใหม่')}</td>
                <td class="px-4 py-3 text-right text-emerald-600 font-semibold">${rowTokens.toLocaleString()}</td>
                <td class="px-4 py-3 text-right text-gray-600">฿${rowCost.toFixed(4)}</td>
            </tr>`;
        }).join('');
        
    } catch (e) {
        document.getElementById('gtTableBody').innerHTML = `<tr><td colspan="3" class="px-4 py-6 text-center text-red-500">เกิดข้อผิดพลาดในการโหลดข้อมูล</td></tr>`;
    }
}

function closeGlobalTokenModal() {
    document.getElementById('globalTokenModal').classList.remove('show');
}

// ═══ PDF Resizer Logic ═══
const resizer = document.getElementById('pdfResizer');
const pdfPanel = document.getElementById('pdfPanel');
let isResizing = false;

if (resizer && pdfPanel) {
    resizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        resizer.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.getElementById('pdfIframe').style.pointerEvents = 'none'; // Prevent iframe capturing mouse
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        // panel is on the right, so width = window.innerWidth - e.clientX
        const newWidth = window.innerWidth - e.clientX;
        // constraints
        if (newWidth > 300 && newWidth < window.innerWidth - 300) {
            pdfPanel.style.width = newWidth + 'px';
        }
    });

    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            resizer.classList.remove('dragging');
            document.body.style.cursor = '';
            document.getElementById('pdfIframe').style.pointerEvents = 'auto';
        }
    });
}

// ═══════════════════════════════════════════════════════════════════════════
//  renderMd — Markdown renderer สำหรับ PharmaCare AI Chat
// ═══════════════════════════════════════════════════════════════════════════

function renderMd(text) {
    if (!text) return '';
    try {
        // Parse inline references: [Ref: AAFP, Page: 4] or [Ref: ความรู้ทั่วไปทางการแพทย์ - อ้างอิงจาก UpToDate]
        let processed = text.replace(/\[Ref:\s*(.*?)\]/gi, (match, content) => {
            let source = content;
            let page = '';
            let type = 'internal';
            
            if (content.includes('ความรู้ทั่วไป') || content.includes('อ้างอิงจาก')) {
                type = 'external';
                let extMatch = content.match(/อ้างอิงจาก\s*(.*)/);
                if (extMatch) source = extMatch[1].trim();
                return `<span class="inline-source-tag" onclick="openPDF('${esc(source)}', '', 'external')">🌐 ${esc(source)}</span>`;
            }
            
            // Extract page if present
            let pageMatch = content.match(/(?:,\s*(?:Page|หน้า)?\s*:?\s*|\s*p\.?\s*)(\d+)/i);
            if (pageMatch) {
                page = pageMatch[1];
                source = content.replace(pageMatch[0], '').trim();
                // remove trailing comma if left behind
                source = source.replace(/,$/, '').trim();
            }
            
            return `<span class="inline-source-tag" onclick="openPDF('${esc(source)}', '${page}', 'internal')">📄 ${esc(source)}${page ? ' p.'+page : ''}</span>`;
        });

        // Configure marked to use breaks for newlines
        if (window.marked) {
            marked.setOptions({ breaks: true, gfm: true });
            return marked.parse(processed);
        }
        return processed;
    } catch (e) {
        return text;
    }
}

function scrollToBottom() { const c = document.getElementById('chatMessages'); if(c) requestAnimationFrame(() => c.scrollTop = c.scrollHeight); }
function handleKeyDown(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }
function autoResize(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px'; updateSendBtn(); }
function updateSendBtn() { const b = document.getElementById('sendBtn'), i = document.getElementById('chatInput'); if(b && i) b.disabled = !i.value.trim() || isLoading; }
function toggleSidebar() {
    if (window.innerWidth <= 768) {
        document.getElementById('sidebar').classList.toggle('open');
        document.getElementById('sidebarOverlay').classList.toggle('show');
    } else {
        document.getElementById('sidebar').classList.toggle('collapsed');
    }
}

// ═══ Keyboard Shortcuts ═══════════════════════════════════════════════════
document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.key === 'n') { e.preventDefault(); openNewChatModal(); }
    if (e.ctrlKey && e.key === 'k') { e.preventDefault(); document.getElementById('searchInput')?.focus(); }
    if (e.key === 'Escape') { closeNewChatModal(); }
});

// ═══ Init ══════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    const chatInput = document.getElementById('chatInput');
    if (chatInput) chatInput.addEventListener('input', updateSendBtn);
    
    if (document.getElementById('sessionList')) {
        loadSessions().then(() => {
            const urlParams = new URLSearchParams(window.location.search);
            const sessionToOpen = urlParams.get('session');
            if (sessionToOpen) {
                switchSession(sessionToOpen);
                window.history.replaceState({}, document.title, window.location.pathname);
            }
        });
    }
});

