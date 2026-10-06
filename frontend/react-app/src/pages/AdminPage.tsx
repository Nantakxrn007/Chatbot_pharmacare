import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import BrandLink from '../components/BrandLink';
import { adminRequest, fetchAdminChats, fetchAdminOverview, fetchAuditEvents, fetchMe } from '../lib/api';
import type { AdminChat, AdminOverview, AuditEvent } from '../types';
import '../styles/admin.css';

type Tab = 'overview' | 'users' | 'log';

const ACTION_TH: Record<string, string> = {
  login: 'เข้าสู่ระบบ',
  rate_limited: 'ถูกจำกัดความถี่',
  password_change: 'เปลี่ยนรหัสผ่านตัวเอง',
  'admin.user_create': 'เพิ่มผู้ใช้',
  'admin.password_reset': 'รีเซ็ตรหัสผ่านผู้ใช้',
  'admin.role_change': 'เปลี่ยนบทบาท',
  'admin.user_disable': 'ปิดบัญชี',
  'admin.user_enable': 'เปิดบัญชี',
  'admin.unlock': 'ปลดล็อกการเข้าสู่ระบบ',
  'POST chat': 'ถามแชท',
  'GET patients': 'ดูข้อมูลผู้ป่วย',
  'POST patients': 'สร้างสรุปผู้ป่วย',
  'GET sessions': 'ดูแชท',
  'POST sessions': 'สร้างแชทใหม่',
  'DELETE sessions': 'ลบแชท',
  'PATCH sessions': 'เปลี่ยนชื่อแชท',
  'GET admin': 'เปิดหน้า Admin',
  'POST admin': 'ใช้งานหน้า Admin',
  'GET tokens': 'ดูการใช้ Token',
  'POST testcases': 'รันชุดทดสอบ',
};

const STATUS_TH: Record<string, string> = {
  ok: 'สำเร็จ',
  failed: 'ล้มเหลว',
  locked: 'ถูกล็อก',
  blocked: 'ถูกบล็อก',
  denied: 'ไม่มีสิทธิ์',
  error: 'ผิดพลาด',
};

function fmtTime(iso?: string | null): string {
  if (!iso) return 'ยังไม่มี';
  return new Date(iso).toLocaleString('th-TH', { dateStyle: 'short', timeStyle: 'medium' });
}

function statusClass(s: string): string {
  if (s === 'ok') return 'ad-pill ad-pill-ok';
  if (['failed', 'locked', 'blocked', 'denied'].includes(s)) return 'ad-pill ad-pill-bad';
  return 'ad-pill ad-pill-warn';
}

function Icon({ d, size = 20 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

const ICONS = {
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  login: 'M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4M10 17l5-5-5-5M15 12H3',
  warn: 'M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  clock: 'M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 118 0v4',
  activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
  check: 'M5 13l4 4L19 7',
  x: 'M6 6l12 12M18 6L6 18',
};

type Tone = 'good' | 'warn' | 'bad' | 'neutral';

function StatCard({ icon, tone, value, title, hint }: { icon: keyof typeof ICONS; tone: Tone; value: ReactNode; title: string; hint: string }) {
  return (
    <div className={`ad-stat ad-tone-${tone}`}>
      <div className="ad-stat-icon"><Icon d={ICONS[icon]} /></div>
      <div className="ad-stat-body">
        <div className="ad-stat-title">{title}</div>
        <div className="ad-stat-value">{value}</div>
        <div className="ad-stat-hint">{hint}</div>
      </div>
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="ad-modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="ad-modal">
        <div className="ad-modal-title">{title}</div>
        {children}
      </div>
    </div>
  );
}

export default function AdminPage() {
  const navigate = useNavigate();
  const [allowed, setAllowed] = useState(false);
  const [me, setMe] = useState('');
  const [tab, setTab] = useState<Tab>('overview');
  const [hours, setHours] = useState(24);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [chats, setChats] = useState<AdminChat[]>([]);
  const [fUser, setFUser] = useState('');
  const [fAction, setFAction] = useState('');
  const [fStatus, setFStatus] = useState('');
  const [limit, setLimit] = useState('100');
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const [modal, setModal] = useState<null | { kind: 'add' } | { kind: 'reset'; user: string }>(null);
  const [fNew, setFNew] = useState({ username: '', display_name: '', password: '', role: 'user' });
  const [fPass, setFPass] = useState('');
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      sessionStorage.setItem('after_login', '/admin');
      navigate('/login', { replace: true });
      return;
    }
    fetchMe()
      .then((m) => {
        if (m.role !== 'admin') {
          alert('หน้านี้สำหรับผู้ดูแลระบบ (admin) เท่านั้น. กรุณาล็อกอินด้วยบัญชี admin');
          navigate('/', { replace: true });
        } else {
          setMe(m.username);
          setAllowed(true);
        }
      })
      .catch(() => {
        sessionStorage.setItem('after_login', '/admin');
        navigate('/login', { replace: true });
      });
  }, [navigate]);

  const load = useCallback(async () => {
    try {
      const [ov, ev, ch] = await Promise.all([
        fetchAdminOverview(hours),
        fetchAuditEvents({ limit, username: fUser.trim(), action: fAction.trim(), status: fStatus }),
        fetchAdminChats(),
      ]);
      setOverview(ov);
      setEvents(ev);
      setChats(ch);
      setUpdatedAt(new Date());
      setError('');
    } catch (e) {
      const code = e instanceof Error ? e.message : '';
      if (code === '401') navigate('/login', { replace: true });
      else if (code === '403') navigate('/', { replace: true });
      else if (code === '429') setError('เรียกถี่เกินไป กรุณารอสักครู่');
      else setError('โหลดข้อมูลไม่สำเร็จ');
    }
  }, [hours, limit, fUser, fAction, fStatus, navigate]);

  useEffect(() => {
    if (!allowed) return;
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [allowed, load]);

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 3000);
  };

  const run = async (fn: () => Promise<void>, okMsg: string, onOk?: () => void) => {
    setBusy(true);
    setFormError('');
    try {
      await fn();
      notify(okMsg);
      onOk?.();
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'เกิดข้อผิดพลาด';
      if (modal) setFormError(msg);
      else notify(msg);
    } finally {
      setBusy(false);
    }
  };

  const exportCsv = () => {
    const esc = (v: string | number) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const rows = [['id', 'time', 'user', 'ip', 'action', 'resource', 'status', 'detail'].join(',')].concat(
      events.map((e) => [e.id, e.ts, e.username, e.ip, e.action, e.resource, e.status, e.detail].map(esc).join(',')),
    );
    const blob = new Blob(['\ufeff' + rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!allowed) return null;

  const a = overview?.audit;
  const d = overview?.data;
  const cfg = overview?.config;
  const activeUsers = overview?.users.filter((u) => !u.disabled).length ?? 0;
  const disabledUsers = overview?.users.filter((u) => u.disabled).length ?? 0;
  const encryptedAll = !!d && d.messages_plaintext === 0 && d.sessions_plaintext === 0;

  const checks: { ok: boolean; text: string; fix?: string }[] = overview
    ? [
        { ok: encryptedAll, text: 'ข้อมูลผู้ป่วยและแชทถูกเข้ารหัสแล้ว', fix: `ยังมีข้อมูลไม่เข้ารหัส ${d ? d.messages_plaintext + d.sessions_plaintext : 0} รายการ. รีสตาร์ทเซิร์ฟเวอร์เพื่อเข้ารหัส` },
        { ok: overview.chain.ok, text: 'บันทึกการใช้งานไม่ถูกแก้ไข', fix: `พบการแก้ไข log ที่รายการ #${overview.chain.first_bad_id}` },
        { ok: !cfg?.jwt_secret_default, text: 'ตั้งรหัสลับสำหรับ token แล้ว', fix: 'ยังใช้ค่าเริ่มต้น. ตั้ง JWT_SECRET ใน .env' },
        { ok: !!cfg?.cookie_secure, text: 'ส่งข้อมูลผ่าน HTTPS', fix: 'ยังไม่เปิด HTTPS. ใช้ได้เฉพาะ localhost (ตอน deploy ต้องเปิด)' },
        { ok: overview.locked.length === 0, text: 'ไม่มีบัญชีที่ถูกล็อก', fix: `ตอนนี้มี ${overview.locked.length} รายการถูกล็อก. ปลดล็อกได้ที่แท็บผู้ใช้งาน` },
      ]
    : [];

  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'ภาพรวม' },
    { id: 'users', label: 'ผู้ใช้งาน' },
    { id: 'log', label: 'ประวัติการใช้งาน' },
  ];

  return (
    <div className="admin-page">
      <header className="ad-header">
        <div className="ad-header-inner">
          <BrandLink light />
          <div className="ad-brand">
            <div className="ad-brand-icon"><Icon d={ICONS.shield} size={22} /></div>
            <div>
              <div className="ad-title">ศูนย์ควบคุมระบบ</div>
              <div className="ad-sub">ผู้ดูแล: {me}</div>
            </div>
          </div>
          <div className="ad-spacer" />
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={1}>ข้อมูล 1 ชั่วโมงล่าสุด</option>
            <option value={24}>ข้อมูล 24 ชั่วโมงล่าสุด</option>
            <option value={168}>ข้อมูล 7 วันล่าสุด</option>
            <option value={720}>ข้อมูล 30 วันล่าสุด</option>
          </select>
          <button className="ad-btn ad-btn-ghost" onClick={load}>รีเฟรช</button>
        </div>
        <div className="ad-tabs">
          {tabs.map((t) => (
            <button key={t.id} className={`ad-tab${tab === t.id ? ' active' : ''}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
          <span className="ad-updated">{updatedAt ? `อัปเดตล่าสุด ${updatedAt.toLocaleTimeString('th-TH')}` : ''}</span>
        </div>
      </header>

      <main className="ad-container">
        {error && <div className="ad-banner ad-banner-bad">{error}</div>}

        {tab === 'overview' && (
          <>
            <div className="ad-stats">
              <StatCard icon="login" tone="good" value={a?.logins_ok ?? '...'} title="เข้าสู่ระบบสำเร็จ" hint="จำนวนครั้งที่มีคนล็อกอินได้" />
              <StatCard icon="warn" tone={!a || a.logins_failed === 0 ? 'good' : a.logins_failed >= 10 ? 'bad' : 'warn'} value={a?.logins_failed ?? '...'} title="ใส่รหัสผ่านผิด" hint="ถ้าเยอะผิดปกติ อาจมีคนพยายามเดารหัส" />
              <StatCard icon="clock" tone={!a || a.rate_limited === 0 ? 'good' : 'warn'} value={a?.rate_limited ?? '...'} title="ถูกหยุดเพราะกดถี่เกินไป" hint="ระบบกันคนส่งคำขอรัวเกินกำหนด" />
              <StatCard icon="lock" tone={!a || a.denied === 0 ? 'good' : 'warn'} value={a?.denied ?? '...'} title="พยายามเข้าโดยไม่มีสิทธิ์" hint="ไม่ได้ล็อกอิน หรือไม่ใช่ admin" />
              <StatCard icon="activity" tone="neutral" value={a?.events ?? '...'} title="การใช้งานทั้งหมด" hint="ทุกการกระทำที่ระบบบันทึกไว้" />
              <StatCard
                icon={overview?.chain.ok === false ? 'x' : 'check'}
                tone={overview ? (overview.chain.ok ? 'good' : 'bad') : 'neutral'}
                value={overview ? (overview.chain.ok ? 'ปลอดภัย' : 'ตรวจพบการแก้ไข') : '...'}
                title="ประวัติการใช้งานน่าเชื่อถือไหม"
                hint={overview?.chain.ok === false ? 'มีคนแก้หรือลบบันทึก ควรตรวจสอบทันที' : 'ไม่มีใครแก้หรือลบบันทึกย้อนหลัง'}
              />
            </div>

            <div className="ad-grid-2">
              <section className="ad-card">
                <h3>เช็กลิสต์ความปลอดภัย</h3>
                <ul className="ad-checks">
                  {checks.map((c) => (
                    <li key={c.text} className={c.ok ? 'ok' : 'bad'}>
                      <span className="ad-check-dot"><Icon d={c.ok ? ICONS.check : ICONS.x} size={14} /></span>
                      <div>
                        <div>{c.text}</div>
                        {!c.ok && <div className="ad-check-fix">{c.fix}</div>}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>

              <section className="ad-card">
                <h3>ภาพรวมระบบ</h3>
                <div className="ad-kv"><span>ผู้ใช้ที่ใช้งานได้</span><b>{activeUsers} คน</b></div>
                <div className="ad-kv"><span>บัญชีที่ถูกปิด</span><b>{disabledUsers} คน</b></div>
                <div className="ad-kv"><span>แชททั้งหมด</span><b>{d?.sessions_total ?? '...'}</b></div>
                <div className="ad-kv"><span>ข้อความทั้งหมด</span><b>{d?.messages_total ?? '...'}</b></div>
                <div className="ad-kv"><span>ข้อความที่เข้ารหัสแล้ว</span><b>{d ? `${d.messages_encrypted} / ${d.messages_total}` : '...'}</b></div>
                {d && Object.entries(d.sessions_by_model).map(([id, n]) => (
                  <div className="ad-kv" key={id}><span>แชทที่ใช้ Gemini {id}</span><b>{n}</b></div>
                ))}
                <div className="ad-kv"><span>จำกัดการใช้งานทั่วไป</span><b>{cfg?.api_rate_limit} ครั้ง/นาที</b></div>
                <div className="ad-kv"><span>จำกัดการถามแชท</span><b>{cfg?.chat_rate_limit} ครั้ง/นาที</b></div>
              </section>
            </div>

            <div className="ad-grid-2">
              <section className="ad-card">
                <h3>IP ที่ใส่รหัสผิดบ่อย</h3>
                {a?.top_failed_ips.length ? (
                  a.top_failed_ips.map((r) => <div className="ad-kv" key={r.ip}><span>{r.ip || '...'}</span><b>{r.n} ครั้ง</b></div>)
                ) : <div className="ad-empty">ไม่มี</div>}
              </section>
              <section className="ad-card">
                <h3>ใครใช้งานมากที่สุด</h3>
                {a?.by_user.length ? (
                  a.by_user.slice(0, 6).map((r) => <div className="ad-kv" key={r.username}><span>{r.username}</span><b>{r.n} ครั้ง</b></div>)
                ) : <div className="ad-empty">ยังไม่มีข้อมูล</div>}
              </section>
            </div>
          </>
        )}

        {tab === 'users' && (
          <>
            <section className="ad-card">
              <div className="ad-card-head">
                <h3>ผู้ใช้งานทั้งหมด</h3>
                <button className="ad-btn ad-btn-primary" onClick={() => { setFNew({ username: '', display_name: '', password: '', role: 'user' }); setFormError(''); setModal({ kind: 'add' }); }}>
                  + เพิ่มผู้ใช้
                </button>
              </div>
              <div className="ad-table-wrap">
                <table>
                  <thead><tr><th>ผู้ใช้</th><th>บทบาท</th><th>สถานะ</th><th>แชท</th><th>ข้อความ</th><th>ใช้ล่าสุด</th><th></th></tr></thead>
                  <tbody>
                    {overview?.users.map((u) => {
                      const self = u.username === me;
                      return (
                        <tr key={u.username} className={u.disabled ? 'ad-row-off' : ''}>
                          <td>
                            <Link to={`/profile/${u.username}`} className="ad-user-link"><b>{u.display_name}</b></Link>
                            <div className="ad-mute">@{u.username}{u.department ? ` · ${u.department}` : ''}</div>
                          </td>
                          <td>{u.role === 'admin' ? 'ผู้ดูแลระบบ' : 'เภสัชกร'}</td>
                          <td><span className={`ad-pill ${u.disabled ? 'ad-pill-bad' : 'ad-pill-ok'}`}>{u.disabled ? 'ปิดอยู่' : 'ใช้งานได้'}</span></td>
                          <td>{u.sessions ?? 0}</td>
                          <td>{u.messages ?? 0}</td>
                          <td>{fmtTime(u.last_activity)}</td>
                          <td className="ad-actions">
                            <button className="ad-btn ad-btn-sm" onClick={() => { setFPass(''); setFormError(''); setModal({ kind: 'reset', user: u.username }); }}>รีเซ็ตรหัสผ่าน</button>
                            {!self && (
                              <>
                                <button
                                  className="ad-btn ad-btn-sm"
                                  disabled={busy}
                                  onClick={() => run(() => adminRequest('POST', `/api/admin/users/${u.username}/role`, { role: u.role === 'admin' ? 'user' : 'admin' }), 'เปลี่ยนบทบาทแล้ว')}
                                >
                                  {u.role === 'admin' ? 'ลดเป็นเภสัชกร' : 'ตั้งเป็น admin'}
                                </button>
                                <button
                                  className={`ad-btn ad-btn-sm ${u.disabled ? '' : 'ad-btn-danger'}`}
                                  disabled={busy}
                                  onClick={() => {
                                    if (!u.disabled && !confirm(`ปิดบัญชี ${u.username}? ผู้ใช้นี้จะเข้าสู่ระบบไม่ได้ทันที`)) return;
                                    run(() => adminRequest('POST', `/api/admin/users/${u.username}/disabled`, { disabled: !u.disabled }), u.disabled ? 'เปิดบัญชีแล้ว' : 'ปิดบัญชีแล้ว');
                                  }}
                                >
                                  {u.disabled ? 'เปิดบัญชี' : 'ปิดบัญชี'}
                                </button>
                              </>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="ad-card">
              <div className="ad-card-head">
                <h3>แชทล่าสุดของทุกคน</h3>
                <span className="ad-mute">อัปเดตเองทุก 30 วินาที</span>
              </div>
              <div className="ad-table-wrap ad-table-tall">
                <table>
                  <thead><tr><th>ผู้ป่วย</th><th>เจ้าของแชท</th><th>แผนก</th><th>โมเดล</th><th>ข้อความ</th><th>อัปเดตล่าสุด</th></tr></thead>
                  <tbody>
                    {chats.map((c) => {
                      const owner = overview?.users.find((u) => u.username === c.username);
                      return (
                        <tr key={c.id}>
                          <td>{c.patient_name || 'ไม่มีชื่อ'}</td>
                          <td><Link to={`/profile/${c.username}`} className="ad-user-link">{owner?.display_name || c.username}</Link></td>
                          <td>{owner?.department || 'ยังไม่ระบุ'}</td>
                          <td>Gemini {c.model_id}</td>
                          <td>{c.message_count}</td>
                          <td>{fmtTime(c.updated_at)}</td>
                        </tr>
                      );
                    })}
                    {!chats.length && <tr><td colSpan={6} className="ad-empty">ยังไม่มีแชท</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="ad-card">
              <div className="ad-card-head">
                <h3>การเข้าสู่ระบบที่ถูกล็อก</h3>
                {overview && overview.locked.length > 1 && (
                  <button className="ad-btn ad-btn-sm" disabled={busy} onClick={() => run(() => adminRequest('POST', '/api/admin/unlock', {}), 'ปลดล็อกทั้งหมดแล้ว')}>ปลดล็อกทั้งหมด</button>
                )}
              </div>
              {overview?.locked.length ? (
                overview.locked.map((l) => (
                  <div className="ad-kv" key={l.id}>
                    <span>{l.key === 'ip' ? 'ทั้ง IP' : 'ผู้ใช้ + IP'}: {l.target} <span className="ad-mute">(อีก {l.retry_after} วินาที)</span></span>
                    <button className="ad-btn ad-btn-sm" disabled={busy} onClick={() => run(() => adminRequest('POST', '/api/admin/unlock', { id: l.id }), 'ปลดล็อกแล้ว')}>ปลดล็อก</button>
                  </div>
                ))
              ) : <div className="ad-empty">ตอนนี้ไม่มีใครถูกล็อก</div>}
            </section>
          </>
        )}

        {tab === 'log' && (
          <section className="ad-card">
            <div className="ad-card-head">
              <h3>ประวัติการใช้งาน</h3>
              <button className="ad-btn ad-btn-sm" onClick={exportCsv} disabled={!events.length}>ดาวน์โหลด CSV</button>
            </div>
            <div className="ad-filters">
              <input placeholder="ค้นหาตามผู้ใช้" value={fUser} onChange={(e) => setFUser(e.target.value)} />
              <input placeholder="ค้นหาตามการกระทำ (เช่น login)" value={fAction} onChange={(e) => setFAction(e.target.value)} />
              <select value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
                <option value="">ทุกสถานะ</option>
                {Object.entries(STATUS_TH).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <select value={limit} onChange={(e) => setLimit(e.target.value)}>
                <option value="50">แสดง 50 รายการ</option>
                <option value="100">แสดง 100 รายการ</option>
                <option value="300">แสดง 300 รายการ</option>
                <option value="1000">แสดง 1000 รายการ</option>
              </select>
            </div>
            <div className="ad-table-wrap ad-table-tall">
              <table>
                <thead><tr><th>เวลา</th><th>ผู้ใช้</th><th>การกระทำ</th><th>ผลลัพธ์</th><th>IP</th><th>รายละเอียด</th></tr></thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.id}>
                      <td>{fmtTime(e.ts)}</td>
                      <td>{e.username || 'ไม่ระบุ'}</td>
                      <td title={`${e.action} ${e.resource}`}>{ACTION_TH[e.action] || e.action}</td>
                      <td><span className={statusClass(e.status)}>{STATUS_TH[e.status] || e.status}</span></td>
                      <td>{e.ip}</td>
                      <td className="ad-mute">{e.detail}</td>
                    </tr>
                  ))}
                  {!events.length && <tr><td colSpan={6} className="ad-empty">ไม่พบรายการ</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        )}

      </main>

      {modal?.kind === 'add' && (
        <Modal title="เพิ่มผู้ใช้ใหม่" onClose={() => setModal(null)}>
          <label className="ad-field">ชื่อผู้ใช้ (ภาษาอังกฤษ/ตัวเลข)<input value={fNew.username} onChange={(e) => setFNew({ ...fNew, username: e.target.value })} /></label>
          <label className="ad-field">ชื่อที่แสดง<input value={fNew.display_name} onChange={(e) => setFNew({ ...fNew, display_name: e.target.value })} /></label>
          <label className="ad-field">รหัสผ่านเริ่มต้น (อย่างน้อย 8 ตัว)<input type="password" value={fNew.password} onChange={(e) => setFNew({ ...fNew, password: e.target.value })} /></label>
          <label className="ad-field">บทบาท
            <select value={fNew.role} onChange={(e) => setFNew({ ...fNew, role: e.target.value })}>
              <option value="user">เภสัชกร</option>
              <option value="admin">ผู้ดูแลระบบ</option>
            </select>
          </label>
          {formError && <div className="ad-form-error">{formError}</div>}
          <div className="ad-modal-actions">
            <button className="ad-btn ad-btn-ghost" onClick={() => setModal(null)}>ยกเลิก</button>
            <button className="ad-btn ad-btn-primary" disabled={busy} onClick={() => run(() => adminRequest('POST', '/api/admin/users', fNew), 'เพิ่มผู้ใช้แล้ว', () => setModal(null))}>เพิ่มผู้ใช้</button>
          </div>
        </Modal>
      )}

      {modal?.kind === 'reset' && (
        <Modal title={`รีเซ็ตรหัสผ่าน ${modal.user}`} onClose={() => setModal(null)}>
          <label className="ad-field">รหัสผ่านใหม่ (อย่างน้อย 8 ตัว)<input type="password" value={fPass} onChange={(e) => setFPass(e.target.value)} /></label>
          {formError && <div className="ad-form-error">{formError}</div>}
          <div className="ad-modal-actions">
            <button className="ad-btn ad-btn-ghost" onClick={() => setModal(null)}>ยกเลิก</button>
            <button className="ad-btn ad-btn-primary" disabled={busy} onClick={() => run(() => adminRequest('POST', `/api/admin/users/${modal.user}/password`, { password: fPass }), 'รีเซ็ตรหัสผ่านแล้ว', () => setModal(null))}>บันทึก</button>
          </div>
        </Modal>
      )}

      <div className={`ad-toast${toast ? ' show' : ''}`}>{toast}</div>
    </div>
  );
}
