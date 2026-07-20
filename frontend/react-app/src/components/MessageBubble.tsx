import type { Message, Source } from '../types';
import MarkdownMessage from './MarkdownMessage';

function formatTime(ts?: string): string {
  const d = ts ? new Date(ts) : new Date();
  return d.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
}

interface SourcesRowProps {
  sources?: Source[];
  onOpenSource: (source: string, page: string, type: string, heading: string) => void;
}

export function SourcesRow({ sources, onOpenSource }: SourcesRowProps) {
  if (!sources?.length) return null;
  return (
    <div className="sources-row">
      <span className="sources-label">📎 อ้างอิง:</span>
      {sources.map((s, i) => (
        <span
          key={i}
          className="source-tag"
          onClick={() => onOpenSource(s.source, s.page ? String(s.page) : '', s.type || '', s.heading || '')}
        >
          {s.type === 'external' ? '🌐' : '📄'} {s.source}
          {s.page ? ` p.${s.page}` : ''}
          {s.heading ? ` — ${s.heading.substring(0, 25)}` : ''}
        </span>
      ))}
    </div>
  );
}

interface Props {
  message: Message;
  onOpenSource: (source: string, page: string, type: string, heading: string) => void;
  onEdit?: () => void;
  onRegenerate?: () => void;
  onCopy?: (text: string) => void;
  userInitial?: string;
}

export default function MessageBubble({ message, onOpenSource, onEdit, onRegenerate, onCopy, userInitial }: Props) {
  const t = formatTime(message.timestamp);

  if (message.role === 'user') {
    return (
      <div className="msg-row user msg-enter">
        <div className="msg-user-col">
          <div className="msg-bubble-user">{message.content}</div>
          <div className="msg-time">{t}</div>
          <div className="msg-user-actions">
            <button className="msg-action-btn" onClick={onEdit}>
              <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Z" />
              </svg>
              แก้ไข
            </button>
          </div>
        </div>
        <div className="msg-user-avatar">{(userInitial || 'A').charAt(0).toUpperCase()}</div>
      </div>
    );
  }

  if (message.role === 'system') {
    return (
      <div className="msg-row assistant msg-enter">
        <div className="msg-bubble-ai">
          <div className="ai-avatar">
            <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2Z" />
            </svg>
          </div>
          <div className="ai-content">
            <MarkdownMessage
              content={message.content}
              onOpenSource={onOpenSource}
              className="ai-text"
            />
            <div className="msg-time">{t}</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="msg-row assistant msg-enter">
      <div className="msg-bubble-ai">
        <div className="ai-avatar">
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="m8.5 8.5 7 7" />
          </svg>
        </div>
        <div className="ai-content">
          <MarkdownMessage content={message.content} onOpenSource={onOpenSource} className="ai-text" />
          <SourcesRow sources={message.sources} onOpenSource={onOpenSource} />
          <div className="ai-actions">
            <button
              className="msg-action-btn"
              onClick={(e) => onCopy?.((e.currentTarget.closest('.ai-content') as HTMLElement)?.querySelector('.ai-text')?.textContent || '')}
            >
              <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.666 3.888A2.25 2.25 0 0 0 13.5 2.25h-3a2.25 2.25 0 0 0-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 0 1-.75.75H9a.75.75 0 0 1-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 0 1-2.25 2.25H6.75A2.25 2.25 0 0 1 4.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 0 1 1.927-.184" />
              </svg>
              คัดลอก
            </button>
            <button className="msg-action-btn" onClick={onRegenerate}>
              <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
              </svg>
              สร้างใหม่
            </button>
          </div>
          <div className="msg-time">{t}</div>
        </div>
      </div>
    </div>
  );
}
