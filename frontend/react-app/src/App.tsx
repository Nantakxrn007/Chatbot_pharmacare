import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

// แยกแต่ละหน้าเป็น chunk ของตัวเอง — ไลบรารีหนัก (pdfjs, xlsx) จะโหลดเมื่อเปิดหน้านั้นเท่านั้น
const LoginPage = lazy(() => import('./pages/LoginPage'));
const ChatPage = lazy(() => import('./pages/ChatPage'));
const PatientsPage = lazy(() => import('./pages/PatientsPage'));
const PatientDetailPage = lazy(() => import('./pages/PatientDetailPage'));
const TestCasePage = lazy(() => import('./pages/TestCasePage'));
const AdminPage = lazy(() => import('./pages/AdminPage'));
const ProfilePage = lazy(() => import('./pages/ProfilePage'));

export default function App() {
  return (
    <Suspense fallback={null}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<ChatPage />} />
        <Route path="/patients" element={<PatientsPage />} />
        <Route path="/patient/:name" element={<PatientDetailPage />} />
        <Route path="/testcase" element={<TestCasePage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/profile/:username" element={<ProfilePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
