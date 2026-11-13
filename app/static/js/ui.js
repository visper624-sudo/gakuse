/* UI 基础组件：图标、提示、抽屉/弹窗、图表、格式化。 */

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.2 9.2 0 0 1-3.9-.8L3 21l1.9-4.6A8.3 8.3 0 0 1 4 11.5a8.4 8.4 0 0 1 8.5-8.4 8.4 8.4 0 0 1 8.5 8.4z"/>',
  kb: '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H10l2 2.5h6.5A1.5 1.5 0 0 1 20 8v10.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z"/>',
  ops: '<path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="4"/>',
  org: '<rect x="9" y="3" width="6" height="5" rx="1.2"/><rect x="2.5" y="16" width="6" height="5" rx="1.2"/><rect x="15.5" y="16" width="6" height="5" rx="1.2"/><path d="M12 8v4M5.5 16v-2.5h13V16"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/>',
  upload: '<path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"/><path d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="M4 20h4l10-10-4-4L4 16v4z"/><path d="m13.5 5.5 4 4"/>',
  trash: '<path d="M4 7h16M9 7V4.5h6V7m-8 0 1 13h8l1-13"/>',
  shield: '<path d="M12 3l7 3v5.5c0 4.5-3 7.8-7 9.5-4-1.7-7-5-7-9.5V6z"/><path d="m9 12 2 2 4-4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7"/>',
  download: '<path d="M12 4v11m0 0 4-4m-4 4-4-4"/><path d="M4 18.5h16"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  book: '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H19v15H5.5A1.5 1.5 0 0 0 4 20.5z"/><path d="M4 5.5v15"/>',
  layers: '<path d="m12 3 8.5 4.5L12 12 3.5 7.5z"/><path d="m3.5 12 8.5 4.5 8.5-4.5M3.5 16.5 12 21l8.5-4.5"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><path d="M16 5.2a3.2 3.2 0 0 1 0 5.6M17.5 14.8c2.2.7 3.5 2.4 3.5 4.7"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.2M12 18.8V21M4.2 7.5l1.9 1.1M17.9 15.4l1.9 1.1M4.2 16.5l1.9-1.1M17.9 8.6l1.9-1.1"/>',
  lock: '<rect x="4.5" y="10" width="15" height="10" rx="2"/><path d="M8 10V7.5a4 4 0 0 1 8 0V10"/>',
  alert: '<path d="M12 4 2.8 20h18.4z"/><path d="M12 9.5v4M12 16.5v.5"/>',
  doc: '<path d="M6 3.5h7l5 5V21H6z"/><path d="M13 3.5V8h4.5"/>',
  spark: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><path d="m6.2 6.2 2 2M15.8 15.8l2 2M6.2 17.8l2-2M15.8 8.2l2-2"/>',
  arrow: '<path d="M5 12h14m0 0-5-5m5 5-5 5"/>',
  logout: '<path d="M15 4h3.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H15"/><path d="M10 8 6 12l4 4M6 12h9"/>',
  filter: '<path d="M4 6h16l-6 7v5l-4 2v-7z"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"/>',
};

export function icon(name, size = 16) {
  const body = ICONS[name] || ICONS.doc;
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
}

/* ---------------- 格式化 ---------------- */
export const fmtNum = (n) => {
  if (n === null || n === undefined) return '-';
  if (n >= 100000) return `${(n / 10000).toFixed(1)}万`;
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
};
export const fmtPct = (n) => `${(Number(n) || 0).toFixed(1)}%`;
export const fmtMs = (n) => (n >= 1000 ? `${(n / 1000).toFixed(2)}s` : `${Math.round(n)}ms`);
export function timeAgo(s) {
  if (!s) return '-';
  const t = new Date(s.replace(' ', 'T')).getTime();
  if (Number.isNaN(t)) return s;
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return s.slice(0, 10);
}

/* ---------------- Toast ---------------- */
export function toast(message, type = 'info', timeout = 2600) {
  const root = document.getElementById('toast-root');
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.innerHTML = `${icon(type === 'err' ? 'alert' : 'check', 14)}<span>${esc(message)}</span>`;
  root.appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .2s, transform .2s';
    node.style.opacity = '0';
    node.style.transform = 'translateY(-6px)';
    setTimeout(() => node.remove(), 220);
  }, timeout);
}

/* ---------------- 抽屉 / 弹窗 ---------------- */
export function openDrawer({ title, body, footer = '', wide = false, onMount }) {
  const root = document.getElementById('overlay-root');
  const mask = document.createElement('div');
  mask.className = 'mask';
  mask.innerHTML = `<div class="drawer ${wide ? 'wide' : ''}">
      <div class="drawer-head"><h3>${esc(title)}</h3>
        <button class="close" aria-label="关闭">${icon('close', 18)}</button></div>
      <div class="drawer-body">${body}</div>
      ${footer ? `<div class="drawer-foot">${footer}</div>` : ''}
    </div>`;
  root.appendChild(mask);
  const close = () => mask.remove();
  mask.addEventListener('click', (e) => { if (e.target === mask) close(); });
  mask.querySelector('.close').onclick = close;
  if (onMount) onMount(mask, close);
  return { el: mask, close };
}

export function openModal({ title, subtitle = '', body, footer = '', onMount }) {
  const root = document.getElementById('overlay-root');
  const mask = document.createElement('div');
  mask.className = 'mask center';
  mask.innerHTML = `<div class="modal">
      <div class="modal-head"><h3>${esc(title)}</h3>${subtitle ? `<p>${subtitle}</p>` : ''}</div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ''}
    </div>`;
  root.appendChild(mask);
  const close = () => mask.remove();
  mask.addEventListener('click', (e) => { if (e.target === mask) close(); });
  if (onMount) onMount(mask, close);
  return { el: mask, close };
}

export function confirmDialog({ title = '确认操作', message, okText = '确认', danger = false }) {
  return new Promise((resolve) => {
    const { close } = openModal({
      title,
      body: `<p style="margin:0;font-size:13px;line-height:1.8;color:var(--ink-2)">${message}</p>`,
      footer: `<button class="btn" data-act="cancel">取消</button>
               <button class="btn ${danger ? 'danger' : 'primary'}" data-act="ok">${esc(okText)}</button>`,
      onMount(root, closeFn) {
        root.querySelector('[data-act="cancel"]').onclick = () => { closeFn(); resolve(false); };
        root.querySelector('[data-act="ok"]').onclick = () => { closeFn(); resolve(true); };
      },
    });
    void close;
  });
}

/* ---------------- 图表（纯 SVG，无外部依赖） ---------------- */
const PALETTE = ['#2f5cff', '#7a5af8', '#06aed4', '#12b76a', '#f79009', '#f04438'];

export function lineChart(labels, series, { height = 200, yTicks = 4, showX = true } = {}) {
  const W = 720;
  const H = height;
  const padL = 44, padR = 16, padT = 14, padB = 26;
  const iw = W - padL - padR;
  const ih = H - padT - padB;
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const step = iw / Math.max(1, labels.length - 1);
  const y = (v) => padT + ih - (v / max) * ih;
  const x = (i) => padL + i * step;

  let grid = '';
  for (let i = 0; i <= yTicks; i += 1) {
    const gy = padT + (ih / yTicks) * i;
    const val = Math.round(max - (max / yTicks) * i);
    grid += `<line x1="${padL}" y1="${gy}" x2="${W - padR}" y2="${gy}" stroke="#eef1f8" stroke-width="1"/>`;
    grid += `<text x="${padL - 8}" y="${gy + 3.5}" text-anchor="end" font-size="9.5" fill="#98a2b3">${val}</text>`;
  }
  const xLabels = showX ? labels.map((l, i) => `<text x="${x(i)}" y="${H - 7}" text-anchor="middle" font-size="9.5" fill="#98a2b3">${esc(l)}</text>`).join('') : '';

  const paths = series.map((s, si) => {
    const color = s.color || PALETTE[si % PALETTE.length];
    const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
    const area = s.area === false ? '' :
      `<polygon points="${padL},${padT + ih} ${pts} ${W - padR},${padT + ih}" fill="url(#grad${si})" opacity=".55"/>`;
    const dots = s.values.map((v, i) =>
      `<circle cx="${x(i)}" cy="${y(v)}" r="2.6" fill="#fff" stroke="${color}" stroke-width="1.6"><title>${esc(labels[i])}：${v}</title></circle>`).join('');
    return `${area}<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
  }).join('');

  const defs = series.map((s, si) => {
    const color = s.color || PALETTE[si % PALETTE.length];
    return `<linearGradient id="grad${si}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity=".28"/>
      <stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient>`;
  }).join('');

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <defs>${defs}</defs>${grid}${xLabels}${paths}</svg>`;
}

export function donut(data, { size = 168, thickness = 18 } = {}) {
  const total = data.reduce((a, b) => a + Number(b.value || 0), 0) || 1;
  const r = (size - thickness) / 2;
  const c = size / 2;
  let acc = 0;
  const segs = data.map((d, i) => {
    const frac = Number(d.value || 0) / total;
    const dash = frac * 2 * Math.PI * r;
    const gap = 2 * Math.PI * r - dash;
    const el = `<circle cx="${c}" cy="${c}" r="${r}" fill="none"
      stroke="${d.color || PALETTE[i % PALETTE.length]}" stroke-width="${thickness}"
      stroke-dasharray="${dash} ${gap}" stroke-dashoffset="${-acc}" transform="rotate(-90 ${c} ${c})">
      <title>${esc(d.name)}：${d.value}</title></circle>`;
    acc += dash;
    return el;
  }).join('');
  const legend = data.map((d, i) => `<div class="row gap8" style="font-size:11.5px;margin-top:6px">
      <i style="width:8px;height:8px;border-radius:2px;background:${d.color || PALETTE[i % PALETTE.length]}"></i>
      <span class="muted grow">${esc(d.name)}</span><b>${fmtNum(d.value)}</b></div>`).join('');
  return `<div class="row gap16" style="align-items:center">
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${segs}
        <text x="${c}" y="${c - 2}" text-anchor="middle" font-size="20" font-weight="600" fill="#101828">${fmtNum(total)}</text>
        <text x="${c}" y="${c + 15}" text-anchor="middle" font-size="10" fill="#98a2b3">总量</text>
      </svg>
      <div style="flex:1;min-width:110px">${legend}</div>
    </div>`;
}

export function barList(items, { color = 'linear-gradient(90deg,#2f5cff,#7a5af8)' } = {}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return `<div class="bar-list">${items.map((i) => `
    <div class="bar-item">
      <div class="bar-top"><span class="ellipsis" style="max-width:76%">${esc(i.label)}</span>
        <b>${fmtNum(i.value)}</b></div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, (i.value / max) * 100)}%;background:${color}"></div></div>
    </div>`).join('')}</div>`;
}

/* ---------------- 轻量 Markdown ---------------- */
export function renderMarkdown(text) {
  let s = esc(text || '');
  s = s.replace(/^&gt;\s?(.*)$/gm, '<span style="display:block;padding:8px 12px;background:#f5f7ff;border-left:3px solid #b7c6ff;border-radius:0 8px 8px 0;color:#475467;font-size:12px;margin-top:8px">$1</span>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/`([^`]+)`/g, '<code style="background:#f2f4fa;padding:1px 5px;border-radius:4px;font-size:11.5px">$1</code>');
  s = s.replace(/（\[(\d+)\]）/g, '<sup class="cite-ref">[$1]</sup>');
  s = s.replace(/\n/g, '<br/>');
  return s;
}

export const emptyState = (text, iconName = 'doc') =>
  `<div class="empty">${icon(iconName, 34)}<p>${esc(text)}</p></div>`;

export const tag = (text, cls = '') => `<span class="tag ${cls}">${esc(text)}</span>`;
