import { useEffect, useRef, useState } from 'react';

interface Props {
  open: boolean;
  title: string;
  initialValue?: string;
  placeholder?: string;
  cancelLabel?: string;
  confirmLabel?: string;
  multiline?: boolean;
  onClose: () => void;
  onConfirm: (value: string) => void;
}

export default function TextPromptModal({
  open,
  title,
  initialValue = '',
  placeholder,
  cancelLabel = 'ยกเลิก',
  confirmLabel = 'บันทึก',
  multiline = false,
  onClose,
  onConfirm,
}: Props) {
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) {
      setValue(initialValue);
      setTimeout(() => (multiline ? textareaRef.current : inputRef.current)?.focus(), 100);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const confirm = () => {
    const trimmed = value.trim();
    if (!trimmed) {
      (multiline ? textareaRef.current : inputRef.current)?.focus();
      return;
    }
    onConfirm(trimmed);
  };

  return (
    <div className={`modal-overlay${open ? ' show' : ''}`}>
      <div className="modal">
        <h3>{title}</h3>
        {multiline ? (
          <textarea
            ref={textareaRef}
            className="modal-textarea"
            placeholder={placeholder}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        ) : (
          <input
            ref={inputRef}
            type="text"
            placeholder={placeholder}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') confirm();
            }}
          />
        )}
        <div className="modal-actions">
          <button className="modal-btn modal-btn-cancel" onClick={onClose}>{cancelLabel}</button>
          <button className="modal-btn modal-btn-ok" onClick={confirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
