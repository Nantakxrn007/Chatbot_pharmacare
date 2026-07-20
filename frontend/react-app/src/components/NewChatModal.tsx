import { useEffect, useRef, useState } from 'react';
import { checkPatientName } from '../lib/api';

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: (name: string) => void;
}

export default function NewChatModal({ open, onClose, onConfirm }: Props) {
  const [name, setName] = useState('');
  const [warn, setWarn] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (open) {
      setName('');
      setWarn(false);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  useEffect(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    setWarn(false);
    if (name.trim().length === 0) return;
    timeoutRef.current = setTimeout(async () => {
      try {
        const exists = await checkPatientName(name.trim());
        setWarn(exists);
      } catch {
        // ignore, matches original silent-fail behavior
      }
    }, 400);
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, [name]);

  const confirm = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      inputRef.current?.focus();
      return;
    }
    try {
      const exists = await checkPatientName(trimmed);
      if (exists) {
        setWarn(true);
        return;
      }
    } catch {
      // ignore, matches original silent-fail behavior
    }
    onConfirm(trimmed);
  };

  return (
    <div className={`modal-overlay${open ? ' show' : ''}`}>
      <div className="modal">
        <h3>ตั้งชื่อแชทใหม่</h3>
        <input
          ref={inputRef}
          type="text"
          placeholder="ชื่อผู้ป่วย เช่น สมชาย, น้องมิว"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') confirm();
          }}
        />
        <div className={`modal-warn${warn ? ' show' : ''}`}>
          ⚠️ ชื่อนี้มีอยู่แล้วในระบบ — กรุณาตั้งชื่อใหม่ เช่น เพิ่มนามสกุลหรือหมายเลข (สมชาย 02)
        </div>
        <div className="modal-actions">
          <button className="modal-btn modal-btn-cancel" onClick={onClose}>ยกเลิก</button>
          <button className="modal-btn modal-btn-ok" onClick={confirm}>บันทึก</button>
        </div>
      </div>
    </div>
  );
}
