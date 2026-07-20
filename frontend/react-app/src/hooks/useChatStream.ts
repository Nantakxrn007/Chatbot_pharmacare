import type { StreamEvent } from '../types';

/**
 * Reads a fetch() Response body as newline-delimited `data: {...}` SSE-style
 * chunks and invokes onEvent for each parsed JSON payload. Mirrors the
 * buffering logic from the original vanilla app.js (partial lines carried
 * over across reads).
 */
export async function consumeChatStream(response: Response, onEvent: (event: StreamEvent) => void) {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data: ')) continue;
      try {
        const parsed = JSON.parse(trimmed.slice(6)) as StreamEvent;
        onEvent(parsed);
      } catch {
        // ignore malformed line, matches original behavior
      }
    }
  }
}
