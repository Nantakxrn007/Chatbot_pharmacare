import { Link } from 'react-router-dom';
import type { Session } from '../types';

function formatDate(ts?: string): string {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleString('th-TH', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

// Groups sessions by day — "วันนี้" / "เมื่อวาน" for the two most recent days,
// the actual date for anything older, so the sidebar reads like a timeline
// instead of one long flat list.
function groupSessionsByDay(sessions: Session[]): { label: string; items: Session[] }[] {
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);

  const groups: { label: string; items: Session[] }[] = [];
  for (const s of sessions) {
    const ts = s.updated_at ? new Date(s.updated_at) : null;
    let label = 'ไม่ทราบวันที่';
    if (ts && !isNaN(ts.getTime())) {
      if (isSameDay(ts, now)) {
        label = 'วันนี้';
      } else if (isSameDay(ts, yesterday)) {
        label = 'เมื่อวาน';
      } else {
        label = ts.toLocaleDateString('th-TH', { day: 'numeric', month: 'long', year: 'numeric' });
      }
    }

    const lastGroup = groups[groups.length - 1];
    if (lastGroup && lastGroup.label === label) {
      lastGroup.items.push(s);
    } else {
      groups.push({ label, items: [s] });
    }
  }
  return groups;
}

interface Props {
  sessions: Session[];
  currentSessionId: string | null;
  displayName: string;
  collapsed: boolean;
  mobileOpen: boolean;
  onNewChat: () => void;
  onSearch: (q: string) => void;
  onSwitch: (id: string) => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
  onOpenGlobalTokens: () => void;
}

export default function Sidebar({
  sessions,
  currentSessionId,
  displayName,
  collapsed,
  mobileOpen,
  onNewChat,
  onSearch,
  onSwitch,
  onRename,
  onDelete,
  onLogout,
  onOpenGlobalTokens,
}: Props) {
  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}${mobileOpen ? ' open' : ''}`} id="sidebar">
      <div className="sidebar-header">
        <Link to="/" className="sidebar-logo">
          <div className="sidebar-logo-icon">
            <span className="material-symbols-rounded" style={{ fontSize: 24, color: '#1fae86', fontVariationSettings: "'FILL' 1" }}>
              local_pharmacy
            </span>
          </div>
          <div className="logo-text">
            <h1>PharmaCare AI</h1>
            <p>ผู้ช่วยเภสัชกรอัจฉริยะ</p>
          </div>
        </Link>
      </div>

      <button className="new-chat-btn" onClick={onNewChat} title="Ctrl+N">
        <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 5v14M5 12h14" />
        </svg>
        เคสใหม่
      </button>

      <div className="search-box">
        <svg className="search-icon" width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="7" />
          <path strokeLinecap="round" strokeWidth="2" d="m21 21-4.3-4.3" />
        </svg>
        <input type="text" id="searchInput" placeholder="ค้นหาแชท... (Ctrl+K)" onChange={(e) => onSearch(e.target.value)} />
      </div>

      <div className="session-list-label">ประวัติ</div>
      <div className="session-list">
        {sessions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '2rem 0', color: '#8aaba5', fontSize: '0.75rem' }}>
            <p>ยังไม่มีแชท</p>
            <p style={{ marginTop: '0.3rem' }}>กดปุ่ม "เคสใหม่" เพื่อเริ่มต้น</p>
          </div>
        ) : (
          groupSessionsByDay(sessions).map((group, groupIndex) => (
            <div key={`${group.label}-${groupIndex}`}>
              <div className="session-group-label">{group.label}</div>
              {group.items.map((s) => {
                const active = s.id === currentSessionId;
                const label = s.patient_name || s.title || '?';
                return (
                  <div
                    key={s.id}
                    className={`session-item${active ? ' active' : ''}`}
                    onClick={() => onSwitch(s.id)}
                  >
                    <div className="avatar">{label.charAt(0).toUpperCase()}</div>
                    <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                      <span className="title">{label}</span>
                      <span className="meta">{formatDate(s.updated_at)}</span>
                    </div>
                    <div className="actions">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onRename(s.id);
                        }}
                        title="เปลี่ยนชื่อ"
                      >
                        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                        </svg>
                      </button>
                      <button
                        className="delete-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(s.id);
                        }}
                        title="ลบ"
                      >
                        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      <div className="sidebar-footer">
        <a href="#" className="sidebar-nav-link" onClick={(e) => { e.preventDefault(); onOpenGlobalTokens(); }}>
          <svg width="16" height="16" fill="none" stroke="#4a6660" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 7v5l3 3" />
          </svg>
          ภาพรวม Token ทั้งระบบ
        </a>
        <Link to="/patients" className="sidebar-nav-link">
          <svg width="16" height="16" fill="none" stroke="#4a6660" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
            <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 00-3-3.87" />
            <path d="M16 3.13a4 4 0 010 7.75" />
          </svg>
          รายชื่อผู้ป่วยทั้งหมด
        </Link>
        <Link to="/testcase" className="sidebar-nav-link">
          <svg width="16" height="16" fill="none" stroke="#4a6660" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
            <rect x="8" y="2" width="8" height="4" rx="1" />
            <path d="M9 4H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-3" />
          </svg>
          Test Cases
        </Link>
        <div className="user-section">
          <div className="user-avatar">{displayName.charAt(0).toUpperCase()}</div>
          <div className="user-info">
            <div className="name">{displayName}</div>
            <div className="role">เภสัชกร</div>
          </div>
          <button className="logout-btn" onClick={onLogout} title="ออกจากระบบ">
            <svg width="16" height="16" fill="none" stroke="#8aaba5" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" />
              <path d="M16 17l5-5-5-5" />
              <path d="M21 12H9" />
            </svg>
          </button>
        </div>
      </div>
    </aside>
  );
}
