/* AI 鉴权问答工作台 */
import { api, streamChat } from '../api.js';
import { esc, icon, timeAgo, openDrawer, toast, renderMarkdown, fmtMs } from '../ui.js';

const state = {
  sessions: [],
  sessionId: null,
  messages: [],
  streaming: false,
  trace: null,
  suggestions: [],
};

export async function renderChat(el, ctx) {
  const initialSession = ctx.params?.get('session');
  state.sessionId = initialSession ? Number(initialSession) : null;
  state.messages = [];
  state.trace = null;

  el.innerHTML = `
    <div class="chat-wrap">
      <aside class="card chat-side">
        <div class="side-head"><b>历史会话</b>
          <button class="btn sm ghost" id="new-chat" style="margin-left:auto">${icon('plus', 13)}新对话</button>
        </div>
        <div class="session-list" id="session-list"></div>
      </aside>

      <section class="chat-main">
        <div class="chat-head">
          <div class="avatar sm" style="background:linear-gradient(135deg,#7a5af8,#2f5cff)">AI</div>
          <div>
            <div class="title">知识库智能问答</div>
            <div class="meta" id="chat-meta">已登录 · 鉴权检索已开启</div>
          </div>
          <div style="margin-left:auto" class="row gap8">
            <span class="tag blue">${icon('shield', 12)} 服务端鉴权</span>
            <button class="btn sm ghost" id="clear-session" title="清空当前会话">${icon('trash', 13)}</button>
          </div>
        </div>
        <div class="chat-body" id="chat-body"></div>
        <div class="chat-input-zone">
          <div class="suggest-row" id="suggest-row"></div>
          <div class="chat-input-box">
            <textarea id="chat-input" rows="1" placeholder="输入你的问题，例如：差旅住宿标准是多少？"></textarea>
            <button class="btn primary" id="send-btn" style="height:36px">${icon('arrow', 15)}发送</button>
          </div>
          <div class="chat-input-hint">
            <span>回答仅基于你可访问的知识单元生成，未授权内容会被服务端拦截</span>
            <span id="engine-hint">—</span>
          </div>
        </div>
      </section>

      <aside class="card chat-trace">
        <div class="side-head"><b>检索与鉴权轨迹</b>
          <span class="tag gray" style="margin-left:auto" id="trace-tag">待检索</span>
        </div>
        <div class="trace-body" id="trace-body"></div>
      </aside>
    </div>`;

  const body = el.querySelector('#chat-body');
  const input = el.querySelector('#chat-input');

  const autoGrow = () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(110, input.scrollHeight)}px`;
  };
  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  el.querySelector('#send-btn').onclick = send;
  el.querySelector('#new-chat').onclick = () => {
    state.sessionId = null;
    state.messages = [];
    state.trace = null;
    renderMessages();
    renderTrace(null);
    renderSessions();
  };
  el.querySelector('#clear-session').onclick = async () => {
    if (!state.sessionId) return;
    await api.del(`/api/chat/sessions/${state.sessionId}`);
    state.sessionId = null;
    state.messages = [];
    renderMessages();
    renderTrace(null);
    await loadSessions();
    toast('会话已清空');
  };

  async function loadSessions() {
    const data = await api.get('/api/chat/sessions');
    state.sessions = data.items;
    renderSessions();
  }

  function renderSessions() {
    const list = el.querySelector('#session-list');
    if (!state.sessions.length) {
      list.innerHTML = `<div class="empty" style="padding:26px 10px"><p>暂无历史会话</p></div>`;
      return;
    }
    list.innerHTML = state.sessions.map((s) => `
      <div class="session-item ${s.id === state.sessionId ? 'active' : ''}" data-id="${s.id}">
        <div class="s-title">${esc(s.title || '新会话')}</div>
        <div class="s-meta"><span>${timeAgo(s.updated_at)}</span><span>${s.msg_count} 条</span></div>
      </div>`).join('');
    list.querySelectorAll('.session-item').forEach((node) => {
      node.onclick = () => openSession(Number(node.dataset.id));
    });
  }

  async function openSession(id) {
    const data = await api.get(`/api/chat/sessions/${id}/messages`);
    state.sessionId = id;
    state.messages = data.items;
    renderSessions();
    renderMessages();
    const last = [...state.messages].reverse().find((m) => m.trace && Object.keys(m.trace).length);
    renderTrace(last ? last.trace : null);
  }

  function messageHtml(m) {
    if (m.role === 'user') {
      return `<div class="msg user">
        <div class="avatar sm">${esc((ctx.user.name || 'U').slice(0, 1))}</div>
        <div class="bubble-wrap">
          <div class="who">${esc(ctx.user.name)} · ${timeAgo(m.created_at)}</div>
          <div class="bubble">${renderMarkdown(m.content)}</div>
        </div></div>`;
    }
    const citations = m.citations || [];
    const notes = [];
    if (m.denied) notes.push(`<div class="msg-note red">${icon('lock', 14)}
      <span>本次检索有 ${m.denied} 份文档因权限受限被拦截，未进入回答上下文。</span></div>`);
    if (m.trace && m.trace.cache_hit) notes.push(`<div class="msg-note blue">${icon('spark', 14)}
      <span>命中已发布 FAQ 缓存（相似度 ${(m.trace.faq?.similarity ?? 1).toFixed ? m.trace.faq.similarity.toFixed(3) : m.trace.faq?.similarity}），毫秒级直出。</span></div>`);
    const citeHtml = citations.length ? `<div class="cite-list">${citations.map((c) => `
        <div class="cite-card" data-cite='${esc(JSON.stringify(c))}'>
          <span class="c-idx">[${c.index}]</span>
          <div class="c-title">${esc(c.title)}</div>
          <div class="c-meta">${esc(c.code || '')} · P${c.page_no || 1} 第${c.para_index || 1}段${c.heading_path ? ` · ${esc(String(c.heading_path).split(' > ').pop())}` : ''}</div>
          <div class="c-snip">${esc(c.snippet || '')}</div>
        </div>`).join('')}</div>` : '';
    const tools = m.latency_ms ? `<div class="msg-tools">
        <span>${icon('clock', 11)} ${fmtMs(m.latency_ms)}</span>
        <span>${icon('layers', 11)} ${(m.token_in || 0) + (m.token_out || 0)} tokens</span>
        <span>${citations.length} 个引用</span></div>` : '';
    return `<div class="msg">
      <div class="avatar sm" style="background:linear-gradient(135deg,#7a5af8,#2f5cff)">AI</div>
      <div class="bubble-wrap">
        <div class="who">知识库助手 · ${timeAgo(m.created_at)}</div>
        <div class="bubble">${renderMarkdown(m.content)}</div>
        ${notes.join('')}${citeHtml}${tools}
      </div></div>`;
  }

  function renderMessages() {
    if (!state.messages.length) {
      body.innerHTML = `<div class="chat-empty">
        <img class="logo-mark" src="/static/logo.svg" alt="" />
        <h3>向知识库提问</h3>
        <p>系统会先检索、再按你的部门 / 角色 / 个人权限裁剪，最后才拼装 Prompt 生成回答</p>
        <div class="suggest-row" id="empty-suggest" style="justify-content:center"></div>
      </div>`;
      renderSuggestions(el.querySelector('#empty-suggest'));
      return;
    }
    body.innerHTML = state.messages.map(messageHtml).join('');
    bindCitations();
    // 滚动到最后一次提问处，保证用户能看到问题与回答开头
    const userMsgs = body.querySelectorAll('.msg.user');
    if (userMsgs.length) {
      const last = userMsgs[userMsgs.length - 1];
      const rect = last.getBoundingClientRect();
      const bodyRect = body.getBoundingClientRect();
      body.scrollTop += (rect.top - bodyRect.top) - 14;
    }
  }

  function bindCitations() {
    body.querySelectorAll('.cite-card').forEach((node) => {
      node.onclick = () => {
        let c = {};
        try { c = JSON.parse(node.dataset.cite); } catch (e) { c = {}; }
        openDrawer({
          title: `引用溯源 · [${c.index}] ${c.title}`,
          body: `<div class="kv-list">
              <div class="k">知识单元</div><div>${esc(c.code || '-')} · ${esc(c.title || '')}</div>
              <div class="k">定位</div><div>第 ${c.page_no || 1} 页 · 第 ${c.para_index || 1} 段 · 字符 ${c.char_start}-${c.char_end}</div>
              <div class="k">标题路径</div><div>${esc(c.heading_path || '—')}</div>
              <div class="k">融合得分</div><div>${c.score ?? '-'}（向量 ${c.vector_score ?? '-'}）</div>
            </div>
            <h4 style="margin:18px 0 8px;font-size:13px">命中切片</h4>
            <div class="chunk-card"><div class="ch-text">${esc(c.snippet || '')}</div></div>
            <div class="small muted mt8">提示：鉴权发生在“召回之后、拼 Prompt 之前”，未授权切片不会出现在这里。</div>`,
        });
      };
    });
  }

  function renderSuggestions(node) {
    if (!node) return;
    const list = state.suggestions.length ? state.suggestions : ['差旅住宿标准是多少？'];
    node.innerHTML = list.map((s) => `<span class="suggest" data-q="${esc(s)}">${esc(s)}</span>`).join('');
    node.querySelectorAll('.suggest').forEach((n) => {
      n.onclick = () => {
        input.value = n.dataset.q;
        autoGrow();
        input.focus();
      };
    });
  }

  function renderTrace(trace) {
    const rail = el.querySelector('#trace-body');
    const tag = el.querySelector('#trace-tag');
    if (!trace) {
      tag.className = 'tag gray';
      tag.textContent = '待检索';
      rail.innerHTML = `<div class="empty" style="padding:30px 10px">
        ${icon('shield', 28)}<p>每次提问都会展示：<br/>召回数量 · 鉴权放行 / 拦截 · 缓存命中</p></div>`;
      return;
    }
    if (trace.cache_hit) {
      tag.className = 'tag green';
      tag.textContent = '缓存命中';
    } else if (trace.denied_count) {
      tag.className = 'tag orange';
      tag.textContent = '含权限拦截';
    } else {
      tag.className = 'tag blue';
      tag.textContent = '鉴权通过';
    }
    const cands = (trace.candidates || []).map((c) => `
      <div class="trace-cand ${c.allowed ? '' : 'denied'}">
        <div class="tc-top"><span class="tc-title">${esc(c.title)}</span>
          <span class="tag ${c.allowed ? 'green' : 'red'}">${c.allowed ? '放行' : '拦截'}</span></div>
        <div class="tc-meta"><span>P${c.page_no || 1}</span>
          <span>RRF ${c.score}</span><span>${(c.channels || []).join(' + ')}</span></div>
      </div>`).join('');
    rail.innerHTML = `
      <div class="trace-stats">
        <div class="trace-stat"><span>召回切片</span><b>${trace.retrieved ?? 0}</b></div>
        <div class="trace-stat"><span>放行单元</span><b>${trace.allowed_count ?? 0}</b></div>
        <div class="trace-stat"><span>拦截单元</span><b style="color:${trace.denied_count ? 'var(--danger)' : 'inherit'}">${trace.denied_count ?? 0}</b></div>
        <div class="trace-stat"><span>最高置信度</span><b>${(trace.top1_sim ?? 0).toFixed ? trace.top1_sim.toFixed(2) : trace.top1_sim}</b></div>
      </div>
      ${trace.faq ? `<div class="msg-note blue" style="margin:0 0 12px">${icon('spark', 13)}
        <span>FAQ 缓存命中：${esc(trace.faq.question)}<br/>相似度 ${trace.faq.similarity}</span></div>` : ''}
      <div class="small muted" style="margin:10px 0 8px">候选切片</div>
      ${cands || '<div class="small muted">无候选（未命中知识库）</div>'}
      <div class="small muted" style="margin-top:14px;line-height:1.8">
        鉴权顺序：召回 → 四维权限过滤 → Prompt 编排。<br/>
        拦截记录会写入审计日志，不返回标题等敏感信息。
      </div>`;
  }

  function setStreaming(on) {
    state.streaming = on;
    const btn = el.querySelector('#send-btn');
    btn.disabled = on;
    btn.innerHTML = on ? '生成中…' : `${icon('arrow', 15)}发送`;
  }

  async function send() {
    const question = input.value.trim();
    if (!question || state.streaming) return;
    input.value = '';
    autoGrow();
    setStreaming(true);

    const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
    state.messages.push({ role: 'user', content: question, created_at: now });
    const assistant = { role: 'assistant', content: '', citations: [], created_at: now, latency_ms: 0, denied: 0, trace: null };
    state.messages.push(assistant);
    renderMessages();
    renderTrace(null);

    const bubbles = body.querySelectorAll('.msg .bubble');
    const target = bubbles[bubbles.length - 1];
    const wrap = target.closest('.bubble-wrap');
    target.innerHTML = '<span class="muted">正在检索知识库并执行权限校验…</span>';

    try {
      await streamChat({ session_id: state.sessionId, question }, (ev) => {
        if (ev.type === 'ack') {
          if (!state.sessionId) state.sessionId = ev.session_id;
        } else if (ev.type === 'trace') {
          state.trace = ev.trace;
          renderTrace(ev.trace);
          assistant.trace = ev.trace;
        } else if (ev.type === 'delta') {
          if (!assistant.content) target.innerHTML = '';
          assistant.content += ev.text;
          target.innerHTML = renderMarkdown(assistant.content);
          body.scrollTop = body.scrollHeight;
        } else if (ev.type === 'denied') {
          assistant.denied = ev.count;
          if (!wrap.querySelector('.msg-note.red')) {
            const note = document.createElement('div');
            note.className = 'msg-note red';
            note.innerHTML = `${icon('lock', 14)}<span>本次检索有 ${ev.count} 份文档因权限受限被拦截，未进入回答上下文。</span>`;
            wrap.appendChild(note);
          }
        } else if (ev.type === 'citations') {
          assistant.citations = ev.items || [];
        } else if (ev.type === 'notice') {
          toast(ev.message, 'info', 4000);
        } else if (ev.type === 'error') {
          toast(ev.message || '生成失败', 'err');
        } else if (ev.type === 'done') {
          assistant.latency_ms = ev.latency_ms || 0;
          assistant.token_in = 0;
          assistant.token_out = ev.usage?.completion_tokens || 0;
          if (!assistant.content) assistant.content = '（无内容）';
          state.messages[state.messages.length - 1] = assistant;
          renderMessages();
          loadSessions();
          setStreaming(false);
        }
      });
    } catch (err) {
      toast(err.message || '问答失败', 'err');
      setStreaming(false);
    } finally {
      if (state.streaming) setStreaming(false);
    }
  }

  // 初始化
  await loadSessions();
  try {
    const sug = await api.get('/api/chat/suggestions');
    state.suggestions = sug.items || [];
  } catch (e) { state.suggestions = []; }
  try {
    const health = await api.get('/api/system/health');
    el.querySelector('#engine-hint').textContent = health.engine === 'remote' ? '大模型：远程 API' : '大模型：本地检索增强引擎';
  } catch (e) { /* ignore */ }

  if (state.sessionId) {
    await openSession(state.sessionId);
  } else {
    renderMessages();
    renderTrace(null);
  }

  // 自动聚焦
  setTimeout(() => input.focus(), 50);
}
