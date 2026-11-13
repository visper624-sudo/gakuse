/* 应用入口：登录、路由、导航与全局初始化 */
import { api, store, setUnauthorizedHandler } from './api.js';
import { esc, icon, toast } from './ui.js';
import { renderDashboard } from './views/dashboard.js';
import { renderChat } from './views/chat.js';
import { renderKB } from './views/kb.js';
import { renderOps } from './views/ops.js';
import { renderOrg } from './views/org.js';

const ROUTES = [
  { key: 'dashboard', label: '运营看板', icon: 'dashboard', perm: 'menu:dashboard', render: renderDashboard, desc: '平台运营数据' },
  { key: 'chat', label: 'AI 问答工作台', icon: 'chat', perm: 'menu:chat', render: renderChat, desc: '鉴权检索问答' },
  { key: 'kb', label: '知识维护与导入', icon: 'kb', perm: 'menu:kb', render: renderKB, desc: '文档解析与权限' },
  { key: 'ops', label: '知识沉淀与运营', icon: 'ops', perm: 'menu:faq', render: renderOps, desc: 'FAQ 与知识缺口' },
  { key: 'org', label: '组织与系统配置', icon: 'org', perm: 'menu:org', render: renderOrg, desc: '账号角色与模型' },
];

const el = (id) => document.getElementById(id);

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '') || 'dashboard';
  const [path, queryString] = raw.split('?');
  const params = new URLSearchParams(queryString || '');
  return { key: path.split('/')[0] || 'dashboard', params };
}

function allowedRoutes(user) {
  const perms = new Set(user?.perms || []);
  return ROUTES.filter((r) => perms.has(r.perm));
}

/* ---------------- 登录 ---------------- */
function showLogin(message = '') {
  el('app-root').classList.add('hidden');
  el('login-root').classList.remove('hidden');
  el('login-root').innerHTML = `
    <div class="login-page">
      <div class="login-hero">
        <div class="grid-bg"></div>
        <div class="login-brand">
          <img class="logo-mark" src="/static/logo.svg" alt="" />
          <div><h1>知识库管理平台</h1><p>KNOWLEDGE OS · 2.9 项目实战</p></div>
        </div>
        <div class="login-copy">
          <h2>让制度文档<em>可被信任地问答</em><br/>让敏感知识<em>只对正确的人可见</em></h2>
          <p>多格式解析切片 · 混合检索 · 四维数据权限 · 越权拦截 · FAQ 聚类沉淀 · 运营看板</p>
        </div>
        <div class="login-features">
          <div class="login-feature"><b>细粒度数据权限</b><span>全局 / 部门 / 角色 / 个人 四维 OR 鉴权，默认无权限</span></div>
          <div class="login-feature"><b>鉴权发生在 Prompt 之前</b><span>召回后裁剪上下文，未授权内容绝不进入大模型</span></div>
          <div class="login-feature"><b>引用可溯源</b><span>回答附带页码、段落与字符区间，一键定位原文</span></div>
          <div class="login-feature"><b>知识自进化</b><span>高频提问聚类成 FAQ，未命中问题沉淀为缺口任务</span></div>
        </div>
      </div>
      <div class="login-panel">
        <div class="login-card">
          <h2>登录控制台</h2>
          <p>使用组织账号登录，系统将按你的部门与角色返回可见知识</p>
          <form id="login-form" autocomplete="off">
            <div class="field"><label>账号</label>
              <input class="input" id="login-username" placeholder="用户名" value="admin" /></div>
            <div class="field"><label>密码</label>
              <input class="input" id="login-password" type="password" placeholder="密码" value="123456" /></div>
            <div class="login-error" id="login-error">${esc(message)}</div>
            <button class="btn primary" type="submit" style="width:100%;height:40px">登录</button>
          </form>
          <div class="demo-accounts">
            <span>演示账号（点击填入，密码均为 123456）</span>
            <div class="demo-chips">
              <div class="demo-chip" data-u="admin"><b>admin</b> 系统管理员</div>
              <div class="demo-chip" data-u="kadmin"><b>kadmin</b> 知识管理员</div>
              <div class="demo-chip" data-u="zhangsan"><b>zhangsan</b> 普通用户</div>
              <div class="demo-chip" data-u="chenjing"><b>chenjing</b> 管理层</div>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  el('login-root').querySelectorAll('.demo-chip').forEach((chip) => {
    chip.onclick = () => {
      el('login-username').value = chip.dataset.u;
      el('login-password').value = '123456';
    };
  });
  el('login-form').onsubmit = async (e) => {
    e.preventDefault();
    const err = el('login-error');
    err.textContent = '';
    try {
      const data = await api.post('/api/auth/login', {
        username: el('login-username').value.trim(),
        password: el('login-password').value,
      });
      store.token = data.token;
      store.user = data.user;
      await boot();
    } catch (e2) {
      err.textContent = e2.message || '登录失败';
    }
  };
}

/* ---------------- 主界面 ---------------- */
async function showApp() {
  el('login-root').classList.add('hidden');
  el('app-root').classList.remove('hidden');
  const user = store.user;
  const routes = allowedRoutes(user);
  const route = parseHash();
  const current = routes.find((r) => r.key === route.key) || routes[0];

  el('nav').innerHTML = `
    <div class="nav-group">工作台</div>
    ${routes.map((r) => `<div class="nav-item ${r.key === current.key ? 'active' : ''}" data-key="${r.key}">
        ${icon(r.icon, 16)}<span>${esc(r.label)}</span>
      </div>`).join('')}
    <div class="nav-group">说明</div>
    <div class="nav-item" data-key="help">${icon('book', 16)}<span>验收演示路径</span></div>`;
  el('nav').querySelectorAll('.nav-item').forEach((item) => {
    item.onclick = () => {
      if (item.dataset.key === 'help') return showHelp();
      location.hash = `#/${item.dataset.key}`;
    };
  });

  el('crumb').innerHTML = `<span>${esc(user.dept_name || '星海科技')}</span>
    <span class="sep">/</span><b>${esc(current.label)}</b>
    <span class="tag gray" style="margin-left:6px">${esc(current.desc)}</span>`;

  el('user-chip').innerHTML = `
    <div class="avatar">${esc((user.name || 'U').slice(0, 1))}</div>
    <div><div class="u-name">${esc(user.name)}</div>
    <div class="u-role">${esc((user.roles || []).map((r) => r.name).join(' / ') || '普通用户')}</div></div>
    <button class="btn sm ghost" id="logout" title="退出登录" style="margin-left:4px">${icon('logout', 14)}</button>`;
  el('user-chip').querySelector('#logout').onclick = () => {
    store.token = '';
    location.hash = '';
    showLogin('已安全退出');
  };

  try {
    const health = await api.get('/api/system/health');
    const pill = el('engine-pill');
    const remote = health.engine === 'remote';
    pill.innerHTML = `<span class="dot" style="background:${remote ? '#12b76a' : '#f79009'}"></span>
      <span>${remote ? '远程大模型已接入' : '本地检索增强引擎'}</span>
      <span style="margin-left:auto;opacity:.6">v${esc(health.version)}</span>`;
  } catch (e) { /* ignore */ }

  const view = el('view');
  view.innerHTML = '';
  await current.render(view, { user, params: route.params, navigate: (k) => { location.hash = `#/${k}`; } });
}

function showHelp() {
  el('view').innerHTML = `<div class="page-head"><div><h2>验收演示路径</h2>
      <p>对照《项目实战》2.9.10 九条验收标准，按顺序演示即可全覆盖</p></div></div>
    <div class="card"><div class="card-body">
      ${[
        ['1', '登录 / 部门树 / 角色 / 按钮级权限', '用 admin 登录 → 组织与系统配置 → 部门树、角色授权树（菜单权限 + 操作权限）', 'org'],
        ['2', '单篇 / 批量导入并切片入库', '知识维护与导入 → 导入文档 → 拖入 PDF / DOCX / MD 多文件 → 查看进度与台账切片数', 'kb'],
        ['3', '四维数据权限混合配置', '台账中点击锁形按钮 → 勾选全局 / 部门 / 角色 / 个人 → 保存', 'kb'],
        ['4', '任一权限实体命中即可访问', '用 zhangsan 提问差旅标准（部门公开）正常回答；提问高管薪酬（未授权）提示权限受限', 'chat'],
        ['5', '对话强制鉴权 + 未授权提示', '同一问题切换 chenjing（管理层角色）提问，可看到答案与引用卡片', 'chat'],
        ['6', '运营看板 7 项指标', '运营看板查看 PV/UV、知识数、常见问题榜、知识热度榜、Token、时延', 'dashboard'],
        ['7', '高频问题挖掘 → 审核发布 → 缓存加速', '知识沉淀 → 立即挖掘 → 发布候选 FAQ → 再次提问同义问句观察毫秒直出', 'ops'],
        ['8', '知识缺口识别与频次', '知识沉淀 → 知识缺口，查看未命中提问与频次，一键转建补充任务', 'ops'],
        ['9', '端到端业务场景', '双账号对比：zhangsan 问差旅标准 vs 高管薪酬；lisi 问生鲜退款 → 沉淀 FAQ → 缺口补足', 'chat'],
      ].map(([n, title, detail, target]) => `
        <div class="rank-row" style="cursor:pointer" data-target="${target}">
          <span class="rank-no">${n}</span>
          <div class="r-main"><b>${esc(title)}</b><span>${esc(detail)}</span></div>
          <span class="tag blue">前往</span>
        </div>`).join('')}
    </div></div>`;
  el('view').querySelectorAll('[data-target]').forEach((row) => {
    row.onclick = () => { location.hash = `#/${row.dataset.target}`; };
  });
}

/* ---------------- 启动 ---------------- */
async function boot() {
  try {
    const me = await api.get('/api/auth/me');
    store.user = me.user;
    await showApp();
  } catch (e) {
    store.token = '';
    showLogin();
  }
}

function init() {
  setUnauthorizedHandler(() => {
    store.token = '';
    showLogin('登录已过期，请重新登录');
  });

  // 支持 ?token=xxx 快速登录（用于自动化截图与演示）
  const url = new URL(location.href);
  const urlToken = url.searchParams.get('token');
  if (urlToken) {
    store.token = urlToken;
    url.searchParams.delete('token');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
  const urlUser = url.searchParams.get('as');

  window.addEventListener('hashchange', () => {
    if (store.user) showApp(); else if (store.token) boot();
  });

  el('global-search').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const kw = e.target.value.trim();
    if (!kw) return;
    location.hash = `#/kb?keyword=${encodeURIComponent(kw)}`;
    e.target.value = '';
  });

  if (store.token) {
    boot();
  } else {
    showLogin();
  }
  if (urlUser) {
    // 允许通过 ?as=zhangsan 快速切换演示账号（密码固定 123456，仅演示环境）
    api.post('/api/auth/login', { username: urlUser, password: '123456' })
      .then((data) => { store.token = data.token; store.user = data.user; showApp(); })
      .catch(() => { /* ignore */ });
  }
}

init();
