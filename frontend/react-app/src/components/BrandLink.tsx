import { Link } from 'react-router-dom';
import '../styles/profile.css';

/** โลโก้มุมซ้ายบน กดแล้วกลับหน้าหลัก */
export default function BrandLink({ light = false }: { light?: boolean }) {
  return (
    <Link to="/" className={`brand-link${light ? ' light' : ''}`}>
      <span className="brand-mark">💊</span>
      <span className="brand-name">PharmaCare AI</span>
    </Link>
  );
}
