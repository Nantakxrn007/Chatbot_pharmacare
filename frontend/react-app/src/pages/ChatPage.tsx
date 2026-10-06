import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import Swal from 'sweetalert2';
import Sidebar from '../components/Sidebar';
import NewChatModal from '../components/NewChatModal';
import ConfirmModal from '../components/ConfirmModal';
import TextPromptModal from '../components/TextPromptModal';
import PdfPanel, { type PdfTarget } from '../components/PdfPanel';
import ToolsPanel from '../components/ToolsPanel';
import { TokenSummaryModal, GlobalTokenModal } from '../components/TokenModals';
import MessageBubble from '../components/MessageBubble';
import { consumeChatStream } from '../hooks/useChatStream';
import {
  clearAuth,
  createSession,
  listChatModels,
  deleteSession,
  fetchMe,
  getSession,
  listSessions,
  logout as apiLogout,
  renameSession,
  searchSessions,
  streamChat,
} from '../lib/api';
import type { ChatModelOption, Message, Session, StreamEvent } from '../types';

// Streamed answers are revealed a whole line at a time (each new line fades
// in — see MarkdownMessage fadeInNew). Re-rendering per character re-ran all
// the markdown decorations dozens of times a second and looked jittery.
// true = keep the typing dots up and show the whole answer once it's complete
// (no line-by-line reveal at all).
const REVEAL_ALL_AT_ONCE = true;
const REVEAL_TICK_MS = 40;
const LINE_DELAY_MAX_MS = 420; // calm pace when little is waiting
const LINE_DELAY_MIN_MS = 140; // catch-up pace when a burst is queued
// A line still being written shows as-is once it has kept the reader waiting this long.
const PARTIAL_LINE_WAIT_MS = 1200;
const THAI_MARK = /[ัิ-ฺ็-๎]/;

/**
 * Move a reveal cut point so the partial text renders cleanly: never strand a
 * Thai vowel/tone mark or half an emoji, and show a bold span, a [Ref ...]
 * citation or a table row in one go (once its closer has arrived) instead of
 * flashing raw markdown that then snaps into formatting.
 */
function safeRevealCut(text: string, cut: number): number {
  while (cut < text.length && (THAI_MARK.test(text[cut]) || (text.charCodeAt(cut) & 0xfc00) === 0xdc00)) cut++;
  const lineStart = text.lastIndexOf('\n', cut - 1) + 1;
  let lineEnd = text.indexOf('\n', cut);
  if (lineEnd === -1) lineEnd = text.length;
  const line = text.slice(lineStart, cut);
  let end = -1;
  if (line.trimStart().startsWith('|')) {
    end = lineEnd;
  } else if ((line.split('**').length - 1) % 2 === 1 || line.endsWith('*')) {
    // walk to the "**" that leaves every bold span on this line closed
    let pos = text[cut - 1] === '*' ? cut - 1 : cut;
    while (true) {
      const close = text.indexOf('**', pos);
      if (close === -1 || close >= lineEnd) break;
      pos = close + 2;
      if ((text.slice(lineStart, pos).split('**').length - 1) % 2 === 0) {
        end = pos;
        break;
      }
    }
  } else if (line.lastIndexOf('[') > line.lastIndexOf(']')) {
    const close = text.indexOf(']', cut);
    if (close !== -1 && close < lineEnd) end = close + 1;
  }
  return end === -1 ? cut : end;
}

const QUICK_ACTIONS = [
  {
    label: 'เด็กเป็นหวัด',
    desc: 'เด็ก 3 ขวบ น้ำมูกใส ไอ ไข้ 37.8',
    q: 'เด็ก 3 ขวบ เป็นหวัด น้ำมูกใส ไอเล็กน้อย ไข้ 37.8 ควรให้ยาอะไร?',
    icon: 'sick',
    iconColor: '#10b981',
    iconBg: 'rgba(16, 185, 129, 0.12)',
  },
  {
    label: 'เจ็บคอ Centor 4',
    desc: 'ต่อมทอนซิลบวมมีหนอง ต้อง ATB?',
    q: 'ผู้ใหญ่เจ็บคอมาก มีไข้สูง ต่อมทอนซิลบวมมีหนอง Modified Centor = 4 คะแนน ควรให้ยาอะไร?',
    icon: 'record_voice_over',
    iconColor: '#0891b2',
    iconBg: 'rgba(8, 145, 178, 0.12)',
  },
  {
    label: 'หูอักเสบ AOM',
    desc: 'เด็ก 2 ขวบ ปวดหู ไข้ 38.5',
    q: 'เด็ก 2 ขวบ ปวดหูข้างขวา ไข้ 38.5 สงสัย AOM ควรรักษาอย่างไร?',
    icon: 'hearing',
    iconColor: '#7c3aed',
    iconBg: 'rgba(124, 58, 237, 0.12)',
  },
  {
    label: 'ไซนัสอักเสบ',
    desc: 'น้ำมูกข้นเหลืองเขียว 12 วัน',
    q: 'ผู้ใหญ่ น้ำมูกข้นเหลืองเขียว ปวดหน้าผาก 12 วัน สงสัยไซนัสอักเสบ',
    icon: 'medical_services',
    iconColor: '#d97706',
    iconBg: 'rgba(217, 119, 6, 0.12)',
  },
];

export default function ChatPage() {
  const navigate = useNavigate();
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [displayName, setDisplayName] = useState('');
  const [department, setDepartment] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [currentPatientName, setCurrentPatientName] = useState<string | null>(null);
  const [updatedAtLabel, setUpdatedAtLabel] = useState('');

  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [isTyping, setIsTyping] = useState(false);
  const [typingLabel, setTypingLabel] = useState('กำลังค้นหาและวิเคราะห์...');
  const [isLoading, setIsLoading] = useState(false);
  const [pendingMessage, setPendingMessage] = useState<string | null>(null);
  const [chatModels, setChatModels] = useState<ChatModelOption[]>([]);
  const [defaultModelId, setDefaultModelId] = useState('3.1');
  const [showSuggested, setShowSuggested] = useState(false);

  const [input, setInput] = useState('');
  const [promptTokens, setPromptTokens] = useState(0);
  const [completionTokens, setCompletionTokens] = useState(0);

  const [newChatOpen, setNewChatOpen] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<{ id: string; currentName: string } | null>(null);
  const [editTarget, setEditTarget] = useState<string | null>(null);
  const [tokenSummaryOpen, setTokenSummaryOpen] = useState(false);
  const [globalTokenOpen, setGlobalTokenOpen] = useState(false);
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null);

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false);
  const [toolsPanelCollapsed, setToolsPanelCollapsed] = useState(false);
  const [toolsPanelMobileOpen, setToolsPanelMobileOpen] = useState(false);
  const [toast, setToast] = useState('');

  const showWelcome = messages.length === 0 && streamingText === null;

  const refreshSessions = useCallback(async () => {
    setSessions(await listSessions());
  }, []);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login', { replace: true });
      return;
    }
    fetchMe()
      .then((me) => {
        setDisplayName(me.display_name || me.username);
        setDepartment(me.department || '');
        setIsAdmin(me.role === 'admin');
      })
      .catch(() => {
        clearAuth();
        navigate('/login', { replace: true });
      });
    listChatModels()
      .then((r) => {
        setChatModels(r.models);
        setDefaultModelId(r.default);
      })
      .catch(() => {});
    refreshSessions();
    // currentSessionId only lives in this component's state, so leaving "/"
    // (e.g. to view a patient's history) and coming back remounts ChatPage
    // with nothing loaded — restore whatever chat was open last instead of
    // dropping back to the blank welcome screen.
    const lastSessionId = localStorage.getItem('lastSessionId');
    if (lastSessionId) {
      switchSession(lastSessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate, refreshSessions]);

  useEffect(() => {
    if (currentSessionId) {
      localStorage.setItem('lastSessionId', currentSessionId);
    } else {
      localStorage.removeItem('lastSessionId');
    }
  }, [currentSessionId]);

  // Follow new text only while the reader is at the bottom — scrolling up to
  // re-read part of a long answer shouldn't get yanked back down every tick.
  const stickToBottomRef = useRef(true);
  // our own smooth scroll's intermediate positions aren't the reader's choice
  const ignoreScrollUntilRef = useRef(0);
  const handleChatScroll = () => {
    if (performance.now() < ignoreScrollUntilRef.current) return;
    const c = chatMessagesRef.current;
    if (c) stickToBottomRef.current = c.scrollHeight - c.scrollTop - c.clientHeight < 160;
  };

  useEffect(() => {
    const c = chatMessagesRef.current;
    if (!c || !stickToBottomRef.current) return;
    if (REVEAL_ALL_AT_ONCE && streamingText !== null) {
      // The whole answer just appeared: bring its start into view rather than
      // its end, and stop following so the save right after doesn't jump down.
      stickToBottomRef.current = false;
      ignoreScrollUntilRef.current = performance.now() + 1000;
      const rows = c.querySelectorAll<HTMLElement>('.msg-row');
      const row = rows[rows.length - 1];
      if (!row) return;
      const top = row.getBoundingClientRect().top - c.getBoundingClientRect().top + c.scrollTop - 16;
      requestAnimationFrame(() => c.scrollTo({ top, behavior: 'smooth' }));
      return;
    }
    // glide while an answer streams in; jump when switching chats/sending
    const behavior = streamingText !== null ? 'smooth' : 'auto';
    requestAnimationFrame(() => c.scrollTo({ top: c.scrollHeight, behavior }));
  }, [messages, streamingText, isTyping]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.max(22, Math.min(ta.scrollHeight, 160)) + 'px';
    }
  }, [input]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2000);
  };

  const updateDashboardHref = currentPatientName && currentPatientName !== 'แชทใหม่'
    ? `/patient/${encodeURIComponent(currentPatientName)}`
    : '';

  const clearChatState = () => {
    setMessages([]);
    setStreamingText(null);
    stickToBottomRef.current = true;
    setPromptTokens(0);
    setCompletionTokens(0);
  };

  const handleNewChatConfirm = async (name: string, modelId: string) => {
    setNewChatOpen(false);
    try {
      const s = await createSession(name, modelId);
      setCurrentSessionId(s.id);
      setCurrentPatientName(s.patient_name || name);
      setUpdatedAtLabel('เพิ่งสร้าง');
      clearChatState();
      await refreshSessions();

      if (pendingMessage) {
        const msg = pendingMessage;
        setPendingMessage(null);
        // Don't call sendMessage() here — its closure still sees the old
        // (null) currentSessionId since this state update hasn't re-rendered
        // yet, so it would think there's no session and pop the name modal
        // again. Send directly with the session id we just got back instead.
        setMessages((prev) => [...prev, { role: 'user', content: msg, timestamp: new Date().toISOString() }]);
        await runStream('/api/chat/stream', { session_id: s.id, message: msg });
      }
    } catch (e) {
      console.error(e);
    }
  };

  const switchSession = async (id: string) => {
    setCurrentSessionId(id);
    try {
      const s = await getSession(id);
      setCurrentPatientName(s.patient_name || s.title || null);
      let dateStr = '';
      if (s.updated_at) {
        try {
          dateStr = new Date(s.updated_at).toLocaleString('th-TH', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch {
          // ignore
        }
      }
      setUpdatedAtLabel(dateStr);
      clearChatState();
      let pTok = 0;
      let cTok = 0;
      if (s.messages?.length) {
        setMessages(s.messages);
        pTok = s.messages.reduce((sum, m) => sum + (m.prompt_tokens || 0), 0);
        cTok = s.messages.reduce((sum, m) => sum + (m.completion_tokens || 0), 0);
        setShowSuggested(true);
      } else {
        setShowSuggested(false);
      }
      setPromptTokens(pTok);
      setCompletionTokens(cTok);
      await refreshSessions();
      if (window.innerWidth < 768) setSidebarMobileOpen(false);
    } catch (e) {
      console.error(e);
    }
  };

  const handleDelete = (id: string) => {
    setDeleteTargetId(id);
  };

  const confirmDelete = async () => {
    const id = deleteTargetId;
    setDeleteTargetId(null);
    if (!id) return;
    await deleteSession(id);
    if (currentSessionId === id) {
      setCurrentSessionId(null);
      setCurrentPatientName(null);
      clearChatState();
    }
    await refreshSessions();
  };

  const handleRename = (id: string) => {
    const session = sessions.find((s) => s.id === id);
    setRenameTarget({ id, currentName: session?.patient_name || session?.title || '' });
  };

  const confirmRename = async (newName: string) => {
    const target = renameTarget;
    setRenameTarget(null);
    if (!target) return;
    await renameSession(target.id, newName);
    if (currentSessionId === target.id) {
      setCurrentPatientName(newName);
    }
    await refreshSessions();
  };

  const handleSearch = async (q: string) => {
    const result = await searchSessions(q);
    if (result) setSessions(result);
  };

  const runStream = async (
    path: '/api/chat/stream' | '/api/chat/edit' | '/api/chat/regenerate',
    body: object
  ) => {
    setIsLoading(true);
    setIsTyping(true);
    setTypingLabel('กำลังค้นหาและวิเคราะห์...');
    stickToBottomRef.current = true;
    // The backend opens the stream right away but sends nothing while it
    // retrieves, then releases text in bursts (it holds lines back until
    // citations/blocks are complete). Keep the typing dots up until there is
    // text, and reveal what has arrived at a steady pace instead of in jumps.
    let full = '';
    let shown = 0;
    let streamEnded = false;
    let nextAt = 0;
    let lastRevealAt = performance.now();
    let onDrained: (() => void) | null = null;
    const ticker = window.setInterval(() => {
      if (shown < full.length) {
        if (REVEAL_ALL_AT_ONCE) {
          setTypingLabel('กำลังเรียบเรียงคำตอบ...');
          if (!streamEnded) return;
          shown = full.length;
          setIsTyping(false);
          setStreamingText(full);
          return;
        }
        const now = performance.now();
        if (now < nextAt) return;
        // next complete line, skipping over blank lines so they don't cost a beat
        let end = shown;
        do {
          const nl = full.indexOf('\n', end);
          end = nl === -1 ? -1 : nl + 1;
        } while (end !== -1 && end < full.length && full.slice(shown, end).trim() === '');
        if (end === -1) {
          if (streamEnded) end = full.length;
          else if (now - lastRevealAt > PARTIAL_LINE_WAIT_MS) end = safeRevealCut(full, full.length);
          else return;
        }
        if (end <= shown) return;
        shown = end;
        lastRevealAt = now;
        const queuedLines = full.slice(shown).split('\n').length - 1;
        nextAt = now + Math.max(LINE_DELAY_MIN_MS, LINE_DELAY_MAX_MS - queuedLines * 25);
        setIsTyping(false);
        setStreamingText(full.slice(0, shown));
      } else if (onDrained) {
        onDrained();
        onDrained = null;
      }
    }, REVEAL_TICK_MS);
    const drained = () =>
      new Promise<void>((resolve) => {
        if (shown >= full.length) resolve();
        else onDrained = resolve;
      });
    try {
      const r = await streamChat(path, body);
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(data.detail || 'API Error');
      }
      const result: { done?: Extract<StreamEvent, { type: 'done' }> } = {};
      await consumeChatStream(r, (event) => {
        if (event.type === 'session') {
          setCurrentSessionId(event.session_id);
        } else if (event.type === 'chunk') {
          full += event.content;
        } else if (event.type === 'done') {
          result.done = event;
        } else if (event.type === 'error') {
          full += `\n\n❌ ${event.content}`;
        }
      });
      streamEnded = true;
      await drained();
      setIsTyping(false);
      const done = result.done;
      if (done) {
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: full, sources: done.sources, timestamp: new Date().toISOString() },
        ]);
        setStreamingText(null);
        if (done.usage) {
          setPromptTokens((p) => p + (done.usage?.prompt_tokens || 0));
          setCompletionTokens((c) => c + (done.usage?.completion_tokens || 0));
        }
        refreshSessions();
        setShowSuggested(true);
      }
    } catch (e) {
      setIsTyping(false);
      setStreamingText(null);
      const msg = e instanceof Error ? e.message : String(e);
      setMessages((prev) => [...prev, { role: 'assistant', content: `❌ เกิดข้อผิดพลาด: ${msg}` }]);
    } finally {
      window.clearInterval(ticker);
      setIsLoading(false);
    }
  };

  async function sendMessage(overrideMsg?: string) {
    const msg = (overrideMsg ?? input).trim();
    if (!msg || isLoading) return;

    setInput('');

    if (!currentSessionId) {
      setPendingMessage(msg);
      setNewChatOpen(true);
      return;
    }

    setMessages((prev) => [...prev, { role: 'user', content: msg, timestamp: new Date().toISOString() }]);
    await runStream('/api/chat/stream', { session_id: currentSessionId, message: msg });
  }

  const quickAsk = (q: string) => {
    if (!currentSessionId) {
      setPendingMessage(q);
      setNewChatOpen(true);
      return;
    }
    sendMessage(q);
  };

  const editLastMessage = () => {
    if (!currentSessionId || isLoading) return;
    const lastUserIdx = [...messages].reverse().findIndex((m) => m.role === 'user');
    if (lastUserIdx === -1) return;
    const idx = messages.length - 1 - lastUserIdx;
    setEditTarget(messages[idx].content);
  };

  const confirmEditMessage = async (newText: string) => {
    const originalText = editTarget;
    setEditTarget(null);
    if (originalText === null || newText === originalText || !currentSessionId) return;

    setMessages((prev) => {
      const next = [...prev];
      // drop the last assistant message (if any) after this user message, then the user message itself
      if (next[next.length - 1]?.role === 'assistant') next.pop();
      next.pop();
      return [...next, { role: 'user', content: newText, timestamp: new Date().toISOString() }];
    });

    await runStream('/api/chat/edit', { session_id: currentSessionId, message: newText });
  };

  const regenerate = async () => {
    if (!currentSessionId || isLoading) return;
    setMessages((prev) => {
      const next = [...prev];
      if (next[next.length - 1]?.role === 'assistant') next.pop();
      return next;
    });
    await runStream('/api/chat/regenerate', { session_id: currentSessionId });
  };

  const goToPatientSummary = () => {
    if (currentPatientName && currentPatientName !== 'แชทใหม่') {
      navigate(`/patient/${encodeURIComponent(currentPatientName)}`);
    } else {
      showToast('กรุณาเลือกแชทผู้ป่วยที่มีชื่อก่อนครับ');
    }
  };

  const openSource = (source: string, page: string, type: string, heading: string) => {
    if (type === 'external') {
      Swal.fire({
        icon: 'info',
        title: 'ความรู้นอกเอกสารอ้างอิง',
        html: `ข้อมูลนี้เป็นความรู้ทางการแพทย์ทั่วไปที่ AI นำมาใช้ประกอบคำตอบ<br><br><b style="color:#10b981">แหล่งที่มาอ้างอิง:</b> ${source}`,
        confirmButtonText: 'รับทราบ',
        confirmButtonColor: '#10b981',
        background: '#f8fafc',
        customClass: { popup: 'rounded-xl', title: 'text-xl font-bold text-slate-800' },
      });
      return;
    }
    setPdfTarget({ source, page, type, heading });
  };

  const handleLogout = () => {
    apiLogout();
    clearAuth();
    navigate('/login', { replace: true });
  };

  const toggleSidebar = () => {
    if (window.innerWidth <= 768) {
      setSidebarMobileOpen((v) => !v);
    } else {
      setSidebarCollapsed((v) => !v);
    }
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 'n') {
        e.preventDefault();
        setNewChatOpen(true);
      }
      if (e.ctrlKey && e.key === 'k') {
        e.preventDefault();
        document.getElementById('searchInput')?.focus();
      }
      if (e.key === 'Escape') {
        setNewChatOpen(false);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  const copyToClipboard = (text: string) => {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(() => showToast('คัดลอกแล้ว ✓'));
      return;
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.top = '-9999px';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    try {
      document.execCommand('copy');
      showToast('คัดลอกแล้ว ✓');
    } catch {
      showToast('คัดลอกไม่สำเร็จ');
    }
    document.body.removeChild(textarea);
  };

  const chatTitle = currentPatientName || (currentSessionId ? 'แชท' : 'หน้าหลัก');
  const chatSubtitle = currentPatientName
    ? (updatedAtLabel ? `อัปเดตล่าสุด: ${updatedAtLabel}` : 'ผู้ป่วย')
    : 'ระบบผู้ช่วยเภสัชกร PharmaCare AI';

  return (
    <div className="app-layout">
      <Sidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        displayName={displayName || 'A'}
        department={department}
        isAdmin={isAdmin}
        models={chatModels}
        collapsed={sidebarCollapsed}
        mobileOpen={sidebarMobileOpen}
        onNewChat={() => setNewChatOpen(true)}
        onSearch={handleSearch}
        onSwitch={switchSession}
        onRename={handleRename}
        onDelete={handleDelete}
        onLogout={handleLogout}
        onOpenGlobalTokens={() => setGlobalTokenOpen(true)}
      />

      <main className="main-area">
        <header className="top-bar">
          <div className="top-bar-left">
            <button className="menu-btn" onClick={toggleSidebar} title={sidebarCollapsed ? 'เปิดแถบด้านข้าง' : 'ปิดแถบด้านข้าง'}>
              <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <div className="flex flex-col">
              <h2 className="chat-title" style={{ margin: 0 }}>{chatTitle}</h2>
              <span className="chat-subtitle">{chatSubtitle}</span>
            </div>
          </div>

          <div className="top-bar-right">
            {currentSessionId && (
              <button className="top-bar-action top-bar-action-token" onClick={() => setTokenSummaryOpen(true)} title="ค่าใช้จ่ายแชทนี้">
                <span>🪙</span>
                <span className="top-bar-action-label">สรุป Token</span>
              </button>
            )}
            {updateDashboardHref && (
              <Link className="top-bar-action top-bar-action-patient" to={updateDashboardHref}>
                <span>📄</span>
                <span className="top-bar-action-label">ประวัติการรักษา</span>
              </Link>
            )}
            <button className="tools-mobile-btn" onClick={() => setToolsPanelMobileOpen(true)} title="เปิดแผงเครื่องมือ">
              <span className="material-symbols-rounded" style={{ fontSize: 18 }}>tune</span>
            </button>
            <div className="top-bar-user">
              <div className="top-bar-user-avatar">{(displayName || 'A').charAt(0).toUpperCase()}</div>
              <span className="top-bar-user-name">{displayName || 'Pharmacist'}</span>
            </div>
          </div>
        </header>

        <div className="chat-messages" ref={chatMessagesRef} onScroll={handleChatScroll}>
          {showWelcome ? (
            <div className="welcome">
              <div className="welcome-icon">🏥</div>
              <h2>ยินดีต้อนรับสู่ PharmaCare AI</h2>
              <p style={{ marginBottom: 2 }}>ผู้ช่วยสนับสนุนเภสัชกรในการซักประวัติ และคัดกรองโรคติดเชื้อทางเดินหายใจส่วนบนเบื้องต้น</p>
              <div className="welcome-note">กดปุ่ม <strong>"แชทใหม่"</strong> เพื่อเริ่มการใช้งาน</div>
              <div className="quick-actions">
                {QUICK_ACTIONS.map((qa) => (
                  <button className="quick-btn" key={qa.label} onClick={() => quickAsk(qa.q)}>
                    <div className="quick-btn-icon" style={{ background: qa.iconBg }}>
                      <span className="material-symbols-rounded" style={{ color: qa.iconColor }}>{qa.icon}</span>
                    </div>
                    <div className="quick-btn-text">
                      <div className="label" style={{ color: qa.iconColor }}>{qa.label}</div>
                      <div className="desc">{qa.desc}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map((m, i) => (
                <MessageBubble
                  key={i}
                  message={m}
                  onOpenSource={openSource}
                  onEdit={editLastMessage}
                  onRegenerate={regenerate}
                  onCopy={copyToClipboard}
                  onQuickAsk={quickAsk}
                  userInitial={displayName}
                />
              )).concat(
                // Same list + key the finished answer will get, so React keeps
                // this bubble's DOM when it becomes a saved message instead of
                // remounting it (which replayed the fade-in over the whole answer).
                streamingText !== null
                  ? [<MessageBubble key={messages.length} message={{ role: 'assistant', content: streamingText }} onOpenSource={openSource} streaming />]
                  : []
              )}
              {isTyping && (
                <div className="msg-row assistant msg-enter">
                  <div className="msg-bubble-ai">
                    <div className="ai-avatar">
                      <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="m8.5 8.5 7 7" />
                      </svg>
                    </div>
                    <div className="ai-content" style={{ padding: '0.7rem 1rem' }}>
                      <div className="typing-dots" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <div className="typing-dot" />
                        <div className="typing-dot" />
                        <div className="typing-dot" />
                        <span style={{ fontSize: '0.75rem', color: '#94a3b8', marginLeft: 6 }}>{typingLabel}</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div
          className="input-area flex flex-col gap-1.5 p-3 bg-white border-t border-gray-200"
          style={{ marginLeft: -32, paddingLeft: 32 + 12, position: 'relative', zIndex: 1 }}
        >
          <div className="suggested-questions flex flex-wrap gap-1.5" style={{ display: showSuggested ? 'flex' : 'none', position: 'relative' }}>
            <div className="relative group" style={{ zIndex: 50 }}>
              <button className="text-xs px-2.5 py-1 rounded-full border border-gray-200 bg-gray-50 text-gray-700 hover:bg-emerald-50 hover:text-emerald-700 hover:border-emerald-200 transition-colors shadow-sm flex items-center gap-1">
                <span>✨</span>
                สรุปเคสนี้
                <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <div className="absolute bottom-full left-0 pb-2 hidden group-hover:block w-56">
                <div className="bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden">
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      goToPatientSummary();
                    }}
                    className="flex items-center gap-1.5 px-4 py-2 text-xs text-gray-700 hover:bg-emerald-50 hover:text-emerald-700 border-b border-gray-100"
                  >
                    <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
                    </svg>
                    ไปหน้าประวัติ (เพื่ออัปเดตสรุป)
                  </a>
                  <button
                    onClick={() => quickAsk('ช่วยสรุปเคสคนไข้รายนี้ให้หน่อย โดยจัดทำเป็นตารางสรุป อาการหลัก, ยาที่ได้รับ, และข้อควรระวัง')}
                    className="w-full flex items-center gap-1.5 text-left px-4 py-2 text-xs text-gray-700 hover:bg-emerald-50 hover:text-emerald-700"
                  >
                    <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6" />
                    </svg>
                    สรุปเป็นตารางในแชทนี้
                  </button>
                </div>
              </div>
            </div>

            <button
              className="text-xs px-2.5 py-1 rounded-full border border-gray-200 bg-gray-50 text-gray-700 hover:bg-emerald-50 hover:text-emerald-700 hover:border-emerald-200 transition-colors shadow-sm flex items-center gap-1"
              onClick={() => quickAsk('คนไข้รายนี้มีประวัติการแพ้ยาหรือโรคประจำตัวอะไรที่ต้องระวังไหม?')}
            >
              <span>⚠️</span>
              โรคประจำตัว / แพ้ยา
            </button>
            <button
              className="text-xs px-2.5 py-1 rounded-full border border-gray-200 bg-gray-50 text-gray-700 hover:bg-emerald-50 hover:text-emerald-700 hover:border-emerald-200 transition-colors shadow-sm flex items-center gap-1"
              onClick={() => quickAsk('ขนาดยาที่ต้องใช้สำหรับคนไข้รายนี้ ควรเป็นเท่าไหร่?')}
            >
              <span>💊</span>
              ขนาดยาที่แนะนำ
            </button>
          </div>

          <div className="input-wrapper">
            <textarea
              ref={textareaRef}
              rows={1}
              placeholder="กรอกอาการผู้ป่วย หรือถามคำถามด้านยาได้เลย"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
            />
            <button className="send-btn" disabled={!input.trim() || isLoading} onClick={() => sendMessage()}>
              <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M22 2L11 13" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M22 2l-7 20-4-9-9-4z" />
              </svg>
            </button>
          </div>
          <div className="input-footer">
            PharmaCare AI อ้างอิงจาก AAFP 2022 & URI Guidelines 2562 — ควรปรึกษาเภสัชกรจริงเสมอ
          </div>
        </div>
      </main>

      <ToolsPanel
        onOpenReference={openSource}
        onSendMessage={quickAsk}
        collapsed={toolsPanelCollapsed}
        onToggleCollapsed={() => setToolsPanelCollapsed((v) => !v)}
        mobileOpen={toolsPanelMobileOpen}
        onCloseMobile={() => setToolsPanelMobileOpen(false)}
      />

      <PdfPanel target={pdfTarget} onClose={() => setPdfTarget(null)} />

      {sidebarMobileOpen && <div className="sidebar-overlay show" onClick={() => setSidebarMobileOpen(false)} />}
      {toolsPanelMobileOpen && <div className="tools-overlay show" onClick={() => setToolsPanelMobileOpen(false)} />}
      <div className={`toast${toast ? ' show' : ''}`}>{toast}</div>

      <NewChatModal
        open={newChatOpen}
        onClose={() => setNewChatOpen(false)}
        onConfirm={handleNewChatConfirm}
        models={chatModels}
        defaultModelId={defaultModelId}
      />
      <ConfirmModal
        open={deleteTargetId !== null}
        title="ลบแชทนี้?"
        description="ประวัติการสนทนาทั้งหมดในแชทนี้จะถูกลบถาวร ไม่สามารถกู้คืนได้"
        confirmLabel="ลบแชท"
        danger
        onClose={() => setDeleteTargetId(null)}
        onConfirm={confirmDelete}
      />
      <TextPromptModal
        open={renameTarget !== null}
        title="ตั้งชื่อแชทใหม่"
        initialValue={renameTarget?.currentName || ''}
        placeholder="เช่น สมชาย, น้องมิว"
        onClose={() => setRenameTarget(null)}
        onConfirm={confirmRename}
      />
      <TextPromptModal
        open={editTarget !== null}
        title="แก้ไขข้อความ"
        initialValue={editTarget || ''}
        multiline
        onClose={() => setEditTarget(null)}
        onConfirm={confirmEditMessage}
      />
      <TokenSummaryModal
        open={tokenSummaryOpen}
        onClose={() => setTokenSummaryOpen(false)}
        promptTokens={promptTokens}
        completionTokens={completionTokens}
      />
      <GlobalTokenModal open={globalTokenOpen} onClose={() => setGlobalTokenOpen(false)} />
    </div>
  );
}
