/* API 访问层：统一鉴权、错误处理、SSE 流式解析。 */

const TOKEN_KEY = 'kb_token';

export const store = {
  get token() { return localStorage.getItem(TOKEN_KEY) || ''; },
  set token(v) { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); },
  user: null,
};

let onUnauthorized = () => {};

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(method, path, body, options = {}) {
  const headers = {};
  if (store.token) headers.Authorization = `Bearer ${store.token}`;
  let payload;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const resp = await fetch(path, { method, headers, body: payload, ...options });
  if (resp.status === 401) {
    onUnauthorized();
    throw new ApiError('登录已过期，请重新登录', 401, 'unauthorized');
  }
  const text = await resp.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { message: text }; }
  if (!resp.ok || data.code) {
    throw new ApiError(data.message || `请求失败（${resp.status}）`, resp.status, data.error);
  }
  return data.data;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body ?? {}),
  put: (path, body) => request('PUT', path, body ?? {}),
  del: (path) => request('DELETE', path),
  upload: (path, formData) => request('POST', path, formData),
};

/**
 * 流式问答：解析 SSE 帧并逐事件回调。
 * onEvent({type, ...payload})
 */
export async function streamChat(payload, onEvent) {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${store.token}` },
    body: JSON.stringify(payload),
  });
  if (resp.status === 401) { onUnauthorized(); throw new ApiError('登录已过期', 401); }
  if (!resp.ok || !resp.body) throw new ApiError('问答服务不可用', resp.status);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLines = frame.split('\n').filter((l) => l.startsWith('data:'));
      if (!dataLines.length) continue;
      const raw = dataLines.map((l) => l.slice(5).trim()).join('');
      try {
        const ev = JSON.parse(raw);
        onEvent(ev);
      } catch (e) { /* 忽略非 JSON 帧 */ }
    }
  }
}

export { ApiError };
