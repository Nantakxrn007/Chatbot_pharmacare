import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe, login } from '../lib/api';

export default function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [shake, setShake] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem('token')) return;
    fetchMe()
      .then(() => navigate('/', { replace: true }))
      .catch(() => {});
  }, [navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const data = await login(username.trim(), password);
      localStorage.setItem('token', data.token);
      localStorage.setItem('username', data.username);
      localStorage.setItem('display_name', data.display_name);
      navigate('/', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'เข้าสู่ระบบไม่สำเร็จ');
      setShake(true);
      setTimeout(() => setShake(false), 400);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-container">
        <div className={`login-card${shake ? ' shake' : ''}`}>
          <div className="logo-area">
            <span className="material-symbols-rounded logo-icon">local_pharmacy</span>
            <div className="logo-title">PharmaCare AI</div>
            <div className="logo-subtitle">ระบบสนับสนุนการตัดสินใจทางคลินิกสำหรับเภสัชกร</div>
          </div>

          <div className={`error-msg${error ? ' show' : ''}`}>
            <span className="material-symbols-rounded">warning</span>
            {error}
          </div>

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label" htmlFor="username">ชื่อผู้ใช้</label>
              <div className="form-input-wrap">
                <span className="material-symbols-rounded">person</span>
                <input
                  className="form-input"
                  type="text"
                  id="username"
                  placeholder="ชื่อผู้ใช้"
                  autoComplete="username"
                  required
                  autoFocus
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="password">รหัสผ่าน</label>
              <div className="form-input-wrap">
                <span className="material-symbols-rounded">lock</span>
                <input
                  className="form-input"
                  type="password"
                  id="password"
                  placeholder="รหัสผ่าน"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </div>

            <button type="submit" className={`login-btn${loading ? ' loading' : ''}`} disabled={loading}>
              <span className="btn-text">เข้าสู่ระบบ</span>
              <span className="spinner" />
            </button>
          </form>

          <p className="footer-text">
            PharmaCare AI — โรคติดเชื้อทางเดินหายใจส่วนบน (URI)
            <br />
            <span className="footer-ref">
              <span className="material-symbols-rounded">description</span>
              อ้างอิง AAFP 2022 และแนวทาง URI เด็ก (ไทย)
            </span>
          </p>
        </div>
      </div>
    </div>
  );
}
