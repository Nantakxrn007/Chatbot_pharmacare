import { Navigate, Route, Routes } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import ChatPage from './pages/ChatPage';
import PatientsPage from './pages/PatientsPage';
import PatientDetailPage from './pages/PatientDetailPage';
import TestCasePage from './pages/TestCasePage';

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<ChatPage />} />
      <Route path="/patients" element={<PatientsPage />} />
      <Route path="/patient/:name" element={<PatientDetailPage />} />
      <Route path="/testcase" element={<TestCasePage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
