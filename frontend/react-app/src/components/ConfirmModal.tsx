interface Props {
  open: boolean;
  title: string;
  description?: string;
  cancelLabel?: string;
  confirmLabel?: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export default function ConfirmModal({
  open,
  title,
  description,
  cancelLabel = 'ยกเลิก',
  confirmLabel = 'ยืนยัน',
  danger = false,
  onClose,
  onConfirm,
}: Props) {
  return (
    <div className={`modal-overlay${open ? ' show' : ''}`}>
      <div className="modal modal-center">
        {danger && (
          <div className="modal-icon-warn">
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
            </svg>
          </div>
        )}
        <h3>{title}</h3>
        {description && <p>{description}</p>}
        <div className="modal-actions">
          <button className="modal-btn modal-btn-cancel" onClick={onClose}>{cancelLabel}</button>
          <button className={`modal-btn ${danger ? 'modal-btn-danger' : 'modal-btn-ok'}`} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
