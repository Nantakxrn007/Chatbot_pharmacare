import { useEffect, useRef, useState } from 'react';
import { checkPatientName } from '../lib/api';
import type { ChatModelOption } from '../types';

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: (name: string, modelId: string) => void;
  models: ChatModelOption[];
  defaultModelId: string;
}

export default function NewChatModal({ open, onClose, onConfirm, models, defaultModelId }: Props) {
  const [name, setName] = useState('');
  const [modelId, setModelId] = useState(defaultModelId);
  const [warn, setWarn] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (open) {
      setName('');
      setWarn(false);
      setModelId(localStorage.getItem('last_model_id') || defaultModelId);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open, defaultModelId]);

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
    localStorage.setItem('last_model_id', modelId);
    onConfirm(trimmed, modelId);
  };

  const parseLabel = (label: string) => {
    const m = label.match(/^(.*?)\s*\((.*)\)$/);
    return m ? { name: m[1], tag: m[2] } : { name: label, tag: '' };
  };

  return (
    <div
      className={`nc-overlay${open ? ' show' : ''}`}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="nc-card" role="dialog" aria-modal="true">
        <div className="nc-title">แชทใหม่</div>
        <div className="nc-sub">ตั้งชื่อผู้ป่วยและเลือกโมเดลที่จะใช้ตอบ</div>

        <input
          ref={inputRef}
          className="nc-input"
          type="text"
          placeholder="ชื่อผู้ป่วย เช่น สมชาย, น้องมิว"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') confirm();
            if (e.key === 'Escape') onClose();
          }}
        />
        <div className={`nc-warn${warn ? ' show' : ''}`}>
          ⚠️ ชื่อนี้มีอยู่แล้วในระบบ — กรุณาตั้งชื่อใหม่ เช่น เพิ่มนามสกุลหรือหมายเลข (สมชาย 02)
        </div>

        {models.length > 1 && (
          <div className="nc-models">
            {models.map((m) => {
              const { name: modelName, tag } = parseLabel(m.label);
              const selected = modelId === m.id;
              return (
                <button
                  key={m.id}
                  type="button"
                  className={`nc-model${selected ? ' selected' : ''}`}
                  onClick={() => setModelId(m.id)}
                >
                  <span className="nc-check">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M5 13l4 4L19 7" />
                    </svg>
                  </span>
                  <span className="nc-model-name">{modelName}</span>
                  {tag && <span className="nc-model-tag">{tag}</span>}
                </button>
              );
            })}
          </div>
        )}

        <div className="nc-actions">
          <button type="button" className="nc-btn nc-btn-cancel" onClick={onClose}>ยกเลิก</button>
          <button type="button" className="nc-btn nc-btn-ok" onClick={confirm}>เริ่มแชท</button>
        </div>
      </div>
    </div>
  );
}
