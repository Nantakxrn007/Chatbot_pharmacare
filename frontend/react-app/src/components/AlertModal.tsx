interface Props {
  open: boolean;
  title?: string;
  message: string;
  closeLabel?: string;
  onClose: () => void;
}

export default function AlertModal({ open, title = 'แจ้งเตือน', message, closeLabel = 'ตกลง', onClose }: Props) {
  return (
    <div className={`modal-overlay${open ? ' show' : ''}`}>
      <div className="modal modal-center">
        <h3>{title}</h3>
        <p>{message}</p>
        <div className="modal-actions">
          <button className="modal-btn modal-btn-ok" onClick={onClose}>{closeLabel}</button>
        </div>
      </div>
    </div>
  );
}
