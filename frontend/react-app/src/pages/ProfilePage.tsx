import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import BrandLink from '../components/BrandLink';
import { fetchProfile, saveProfile } from '../lib/api';
import type { UserProfile } from '../types';
import '../styles/profile.css';

export default function ProfilePage() {
  const { username } = useParams();
  const navigate = useNavigate();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [name, setName] = useState('');
  const [department, setDepartment] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      sessionStorage.setItem('after_login', username ? `/profile/${username}` : '/profile');
      navigate('/login', { replace: true });
      return;
    }
    fetchProfile(username)
      .then((p) => {
        setProfile(p);
        setName(p.display_name || '');
        setDepartment(p.department || '');
      })
      .catch((e) => {
        const code = e instanceof Error ? e.message : '';
        if (code === '401') navigate('/login', { replace: true });
        else if (code === '403') navigate('/', { replace: true });
        else setError('โหลดโปรไฟล์ไม่สำเร็จ');
      });
  }, [username, navigate]);

  const save = async () => {
    if (!profile) return;
    setBusy(true);
    setError('');
    setSaved('');
    try {
      const next = await saveProfile(
        { display_name: name.trim(), department },
        profile.is_self ? undefined : profile.username,
      );
      setProfile(next);
      setSaved('บันทึกแล้ว');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'บันทึกไม่สำเร็จ');
    } finally {
      setBusy(false);
    }
  };

  if (!profile && !error) return null;

  const initial = (profile?.display_name || profile?.username || '?')[0].toUpperCase();

  return (
    <div className="profile-page">
      <header className="pf-header">
        <BrandLink />
      </header>
      <main className="pf-wrap">
        {error && !profile && <div className="pf-error">{error}</div>}
        {profile && (
          <section className="pf-card">
            <div className="pf-avatar">{initial}</div>
            <div className="pf-username">@{profile.username}</div>
            <div className="pf-role">{profile.role === 'admin' ? 'ผู้ดูแลระบบ' : 'เภสัชกร'}</div>
            {profile.disabled && <div className="pf-off">บัญชีนี้ถูกปิดอยู่</div>}

            <label className="pf-field">
              ชื่อที่แสดง
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="pf-field">
              แผนก
              <div className="pf-depts">
                {profile.departments.map((d) => (
                  <button
                    key={d}
                    type="button"
                    className={`pf-dept${department === d ? ' selected' : ''}`}
                    onClick={() => setDepartment(d)}
                  >
                    {d}
                  </button>
                ))}
              </div>
            </label>
            {error && <div className="pf-error">{error}</div>}
            {saved && <div className="pf-saved">{saved}</div>}
            <button className="pf-save" disabled={busy || !name.trim() || !department} onClick={save}>
              บันทึกโปรไฟล์
            </button>
          </section>
        )}
      </main>
    </div>
  );
}
