import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { fetchAdminOverview, fetchAuditEvents, fetchMe } from '../lib/api';
import type { AdminOverview, AuditEvent } from '../types';
import '../styles/admin.css';

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('th-TH', { dateStyle: 'short', timeStyle: 'medium' });
}

function statusClass(s: string): string {
  if (s === 'ok') return 'ad-badge ad-ok';
  if (s === 'failed' || s === 'locked' || s === 'blocked' || s === 'denied') return 'ad-badge ad-bad';
  return 'ad-badge ad-warn';
}

export default function AdminPage() {
  const navigate = useNavigate();
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [hours, setHours] = useState(24);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [fUser, setFUser] = useState('');
  const [fAction, setFAction] = useState('');
  const [fStatus, setFStatus] = useState('');
  const [limit, setLimit] = useState('100');
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      sessionStorage.setItem('after_login', '/admin');
      navigate('/login', { replace: true });
      return;
    }
    fetchMe()
      .then((me) => {
        if (me.role !== 'admin') {
          alert('หน้านี้สำหรับผู้ดูแลระบบ (admin) เท่านั้น — กรุณาล็อกอินด้วยบัญชี admin');
          navigate('/', { replace: true });
        }
        else setAllowed(true);
      })
      .catch(() => {
        sessionStorage.setItem('after_login', '/admin');
        navigate('/login', { replace: true });
      });
  }, [navigate]);

  const load = useCallback(async () => {
    try {
      const [ov, ev] = await Promise.all([
        fetchAdminOverview(hours),
        fetchAuditEvents({ limit, username: fUser.trim(), action: fAction.trim(), status: fStatus }),
      ]);
      setOverview(ov);
      setEvents(ev);
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

  if (!allowed) return null;
  const a = overview?.audit;
  const d = overview?.data;
  const cfg = overview?.config;
  const warnings: string[] = [];
  if (overview) {
    if (!overview.chain.ok) warnings.push(`Audit log ถูกแก้ไขหรือเสียหาย (เริ่มผิดที่แถว #${overview.chain.first_bad_id})`);
    if (cfg?.jwt_secret_default) warnings.push('JWT_SECRET ยังเป็นค่าเริ่มต้น — ใครก็ปลอม token ได้');
    if (d && (d.messages_plaintext > 0 || d.sessions_plaintext > 0))
      warnings.push(`ยังมีข้อมูลไม่เข้ารหัส: ข้อความ ${d.messages_plaintext}, เซสชัน ${d.sessions_plaintext}`);
    if (!cfg?.cookie_secure) warnings.push('COOKIE_SECURE=false — เหมาะกับ localhost เท่านั้น ตอน deploy ต้องเปิด HTTPS แล้วตั้งเป็น true');
    if (overview.locked.length) warnings.push(`มี ${overview.locked.length} รายการถูกล็อกจากการ login ผิดอยู่ตอนนี้`);
  }

  return (
    <div className="admin-page">
      <nav className="ad-nav">
        <Link to="/" className="ad-back">← กลับแชท</Link>
        <div>
          <div className="ad-title">Admin · Security Dashboard</div>
          <div className="ad-sub">ตัวเลขและบันทึกการใช้งานเท่านั้น — ไม่แสดงชื่อผู้ป่วยหรือเนื้อหาแชท</div>
        </div>
        <div className="ad-spacer" />
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={1}>1 ชม.</option>
          <option value={24}>24 ชม.</option>
          <option value={168}>7 วัน</option>
          <option value={720}>30 วัน</option>
        </select>
        <button onClick={load}>รีเฟรช</button>
        <span className="ad-updated">{updatedAt ? `อัปเดต ${updatedAt.toLocaleTimeString('th-TH')}` : ''}</span>
      </nav>

      <div className="ad-container">
        {error && <div className="ad-alert">{error}</div>}
        {warnings.map((w) => (
          <div className="ad-alert" key={w}>⚠ {w}</div>
        ))}

        <div className="ad-grid">
          <div className="ad-card"><div className="ad-num">{a?.events ?? '—'}</div><div>กิจกรรมทั้งหมด</div></div>
          <div className="ad-card"><div className="ad-num">{a?.logins_ok ?? '—'}</div><div>Login สำเร็จ</div></div>
          <div className={`ad-card ${a && a.logins_failed ? 'ad-card-bad' : ''}`}><div className="ad-num">{a?.logins_failed ?? '—'}</div><div>Login ผิด/ถูกล็อก</div></div>
          <div className={`ad-card ${a && a.rate_limited ? 'ad-card-warn' : ''}`}><div className="ad-num">{a?.rate_limited ?? '—'}</div><div>ถูกจำกัดอัตรา (429)</div></div>
          <div className={`ad-card ${a && a.denied ? 'ad-card-warn' : ''}`}><div className="ad-num">{a?.denied ?? '—'}</div><div>ถูกปฏิเสธ (401/403)</div></div>
          <div className={`ad-card ${overview && !overview.chain.ok ? 'ad-card-bad' : ''}`}>
            <div className="ad-num">{overview ? (overview.chain.ok ? 'ปกติ' : 'ผิดปกติ') : '—'}</div>
            <div>ความสมบูรณ์ Audit log ({overview?.chain.rows ?? 0} แถว)</div>
          </div>
        </div>

        <div className="ad-two">
          <section className="ad-panel">
            <h3>ผู้ใช้งาน</h3>
            <table>
              <thead><tr><th>ผู้ใช้</th><th>บทบาท</th><th>เซสชัน</th><th>ข้อความ</th><th>ใช้ล่าสุด</th></tr></thead>
              <tbody>
                {overview?.users.map((u) => (
                  <tr key={u.username}>
                    <td>{u.display_name} <span className="ad-mute">({u.username})</span></td>
                    <td>{u.role}</td>
                    <td>{u.sessions ?? 0}</td>
                    <td>{u.messages ?? 0}</td>
                    <td>{fmtTime(u.last_activity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="ad-panel">
            <h3>การเข้ารหัสข้อมูล</h3>
            <table>
              <tbody>
                <tr><td>ข้อความทั้งหมด</td><td>{d?.messages_total ?? '—'}</td></tr>
                <tr><td>เข้ารหัสแล้ว</td><td>{d?.messages_encrypted ?? '—'}</td></tr>
                <tr><td>ยังไม่เข้ารหัส</td><td className={d && d.messages_plaintext ? 'ad-bad-text' : ''}>{d?.messages_plaintext ?? '—'}</td></tr>
                <tr><td>เซสชันทั้งหมด</td><td>{d?.sessions_total ?? '—'}</td></tr>
              </tbody>
            </table>
            <h3>IP ที่ login ผิดบ่อย</h3>
            {a?.top_failed_ips.length ? (
              <table><tbody>{a.top_failed_ips.map((r) => <tr key={r.ip}><td>{r.ip || '—'}</td><td>{r.n} ครั้ง</td></tr>)}</tbody></table>
            ) : <div className="ad-mute">ไม่มี</div>}
            <h3>ถูกล็อกอยู่ตอนนี้</h3>
            {overview?.locked.length ? (
              <table><tbody>{overview.locked.map((l) => <tr key={l.key + l.target}><td>{l.key}: {l.target}</td><td>อีก {l.retry_after} วินาที</td></tr>)}</tbody></table>
            ) : <div className="ad-mute">ไม่มี</div>}
          </section>
        </div>

        <section className="ad-panel">
          <h3>Audit log</h3>
          <div className="ad-filters">
            <input placeholder="ผู้ใช้" value={fUser} onChange={(e) => setFUser(e.target.value)} />
            <input placeholder="action (เช่น login, GET patients)" value={fAction} onChange={(e) => setFAction(e.target.value)} />
            <select value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
              <option value="">ทุกสถานะ</option>
              <option value="ok">ok</option>
              <option value="failed">failed</option>
              <option value="locked">locked</option>
              <option value="blocked">blocked</option>
              <option value="denied">denied</option>
              <option value="error">error</option>
            </select>
            <select value={limit} onChange={(e) => setLimit(e.target.value)}>
              <option value="50">50</option>
              <option value="100">100</option>
              <option value="300">300</option>
              <option value="1000">1000</option>
            </select>
          </div>
          <div className="ad-table-wrap">
            <table>
              <thead><tr><th>#</th><th>เวลา</th><th>ผู้ใช้</th><th>IP</th><th>Action</th><th>Resource</th><th>สถานะ</th><th>รายละเอียด</th></tr></thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td>{e.id}</td>
                    <td>{fmtTime(e.ts)}</td>
                    <td>{e.username || '—'}</td>
                    <td>{e.ip}</td>
                    <td>{e.action}</td>
                    <td className="ad-res">{e.resource}</td>
                    <td><span className={statusClass(e.status)}>{e.status}</span></td>
                    <td>{e.detail}</td>
                  </tr>
                ))}
                {!events.length && <tr><td colSpan={8} className="ad-mute">ไม่มีรายการ</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
