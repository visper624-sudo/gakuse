/* 组织架构与系统配置 */
import {
  esc, icon, timeAgo, openModal, confirmDialog, toast, emptyState, tag, fmtNum,
} from '../ui.js';
import { api } from '../api.js';

const state = { tab: 'depts', tree: [], users: [], roles: [], catalog: [], depts: [], config: null, auditPage: 1 };

export async function renderOrg(el, ctx) {
  if (ctx.params?.get('tab')) state.tab = ctx.params.get('tab');
  el.innerHTML = `<div class="page-head">
      <div><h2>组织架构与系统配置</h2>
        <p>部门树、账号角色、功能权限树、模型服务参数与审计流水</p></div>
      <div class="actions">
        <button class="btn" id="refresh-org">${icon('refresh')}刷新</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head" style="border:0;padding:6px 18px 0">
        <div class="tabs" id="org-tabs" style="border:0">
          <div class="tab on" data-tab="depts">组织架构</div>
          <div class="tab" data-tab="users">账号管理</div>
          <div class="tab" data-tab="roles">角色与功能权限</div>
          <div class="tab" data-tab="config">模型与系统参数</div>
          <div class="tab" data-tab="audit">审计日志</div>
        </div>
      </div>
      <div class="card-body" id="org-body" style="min-height:480px"></div>
    </div>`;

  const body = el.querySelector('#org-body');
  el.querySelectorAll('#org-tabs .tab').forEach((t) => {
    t.classList.toggle('on', t.dataset.tab === state.tab);
    t.onclick = () => {
      state.tab = t.dataset.tab;
      el.querySelectorAll('#org-tabs .tab').forEach((x) => x.classList.toggle('on', x === t));
      render();
    };
  });
  el.querySelector('#refresh-org').onclick = () => loadAll();

  await loadAll();

  async function loadAll() {
    const [tree, users, roles, config] = await Promise.all([
      api.get('/api/org/tree'),
      api.get('/api/org/users'),
      api.get('/api/org/roles'),
      api.get('/api/system/config'),
    ]);
    state.tree = tree.tree;
    state.users = users.items;
    state.roles = roles.items;
    state.catalog = roles.catalog;
    state.config = config;
    state.depts = flatten(tree.tree);
    render();
  }

  function flatten(nodes, depth = 0, out = []) {
    nodes.forEach((n) => {
      out.push({ ...n, depth });
      if (n.children?.length) flatten(n.children, depth + 1, out);
    });
    return out;
  }

  function render() {
    if (state.tab === 'depts') renderDepts();
    else if (state.tab === 'users') renderUsers();
    else if (state.tab === 'roles') renderRoles();
    else if (state.tab === 'config') renderConfig();
    else renderAudit();
  }

  /* ---------------- 部门 ---------------- */
  function renderDepts() {
    const rows = state.depts.map((d) => `<div class="dept-node" data-id="${d.id}" style="padding-left:${12 + d.depth * 16}px">
        ${icon('org', 14)}<span>${esc(d.name)}</span>
        <span class="count">${d.user_count} 人 · ${d.ku_count} 单元</span></div>`).join('');
    body.innerHTML = `<div class="org-grid">
      <div class="card" style="box-shadow:none">
        <div class="card-head"><h3>部门树</h3>
          <button class="btn sm primary" id="add-dept" style="margin-left:auto">${icon('plus', 12)}新增</button></div>
        <div class="dept-tree" id="dept-tree">${rows || emptyState('暂无部门')}</div>
      </div>
      <div class="card" style="box-shadow:none">
        <div class="card-head"><h3>部门详情</h3><span class="sub">部门共享权限按「向上包含」生效</span></div>
        <div class="card-body" id="dept-detail">
          ${emptyState('左侧选择一个部门查看详情', 'org')}
        </div>
      </div>
    </div>`;
    body.querySelector('#add-dept').onclick = () => deptForm(null);
    body.querySelectorAll('.dept-node').forEach((node) => {
      node.onclick = () => {
        body.querySelectorAll('.dept-node').forEach((n) => n.classList.toggle('active', n === node));
        const d = state.depts.find((x) => x.id === Number(node.dataset.id));
        body.querySelector('#dept-detail').innerHTML = `
          <div class="kv-list">
            <div class="k">部门名称</div><div><b>${esc(d.name)}</b></div>
            <div class="k">上级部门</div><div>${esc(state.depts.find((x) => x.id === d.parent_id)?.name || '—')}</div>
            <div class="k">成员数量</div><div>${d.user_count} 人</div>
            <div class="k">知识单元</div><div>${d.ku_count} 个</div>
          </div>
          <div class="row gap8 mt16">
            <button class="btn" id="add-child">${icon('plus')}新增子部门</button>
            <button class="btn" id="rename">${icon('edit')}重命名</button>
            <button class="btn danger" id="del">${icon('trash')}删除</button>
          </div>`;
        body.querySelector('#add-child').onclick = () => deptForm(d.id);
        body.querySelector('#rename').onclick = () => deptForm(null, d);
        body.querySelector('#del').onclick = async () => {
          if (!await confirmDialog({ title: '删除部门', message: `确定删除「${esc(d.name)}」？该部门下不能有成员或子部门。`, danger: true })) return;
          try {
            await api.del(`/api/org/depts/${d.id}`);
            toast('已删除', 'ok');
            await loadAll();
          } catch (err) { toast(err.message, 'err'); }
        };
      };
    });
  }

  function deptForm(parentId, dept) {
    openModal({
      title: dept ? '重命名部门' : '新增部门',
      body: `<div class="field"><label>部门名称</label>
          <input class="input" id="d-name" value="${esc(dept?.name || '')}" placeholder="例如：财务部 / 结算组"/></div>`,
      footer: `<button class="btn" data-act="cancel">取消</button>
               <button class="btn primary" data-act="ok">保存</button>`,
      onMount(root, close) {
        root.querySelector('[data-act="cancel"]').onclick = close;
        root.querySelector('[data-act="ok"]').onclick = async () => {
          const name = root.querySelector('#d-name').value.trim();
          if (!name) return toast('请输入部门名称', 'err');
          if (dept) await api.put(`/api/org/depts/${dept.id}`, { name });
          else await api.post('/api/org/depts', { name, parent_id: parentId });
          toast('已保存', 'ok');
          close();
          await loadAll();
        };
      },
    });
  }

  /* ---------------- 用户 ---------------- */
  function renderUsers() {
    body.innerHTML = `<div class="row between" style="margin-bottom:12px">
        <div class="small muted">共 ${state.users.length} 个账号 · 角色决定功能权限，四维数据权限决定可见知识范围</div>
        <button class="btn primary" id="add-user">${icon('plus')}新增账号</button>
      </div>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th>账号</th><th>部门</th><th>岗位</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead>
        <tbody>${state.users.map((u) => `
          <tr data-id="${u.id}">
            <td><div class="row gap8"><div class="avatar sm">${esc(u.name.slice(0, 1))}</div>
              <div><div class="t-title">${esc(u.name)}</div><div class="t-sub mono">${esc(u.username)}</div></div></div></td>
            <td>${esc(u.dept_name || '—')}</td>
            <td>${esc(u.title || '—')}</td>
            <td>${(u.roles || []).map((r) => tag(r.name, r.code === 'admin' ? 'red' : r.code === 'knowledge_admin' ? 'blue' : 'gray')).join(' ')}</td>
            <td>${u.status === 'active' ? tag('启用', 'green') : tag('停用', 'gray')}</td>
            <td>${timeAgo(u.last_login)}</td>
            <td class="actions">
              <button class="btn sm ghost" data-act="edit">${icon('edit', 13)}</button>
              <button class="btn sm ghost" data-act="pwd" title="重置密码">${icon('lock', 13)}</button>
            </td>
          </tr>`).join('')}</tbody></table></div>`;

    body.querySelector('#add-user').onclick = () => userForm(null);
    body.querySelectorAll('tr[data-id]').forEach((row) => {
      const id = Number(row.dataset.id);
      const u = state.users.find((x) => x.id === id);
      row.querySelector('[data-act="edit"]').onclick = () => userForm(u);
      row.querySelector('[data-act="pwd"]').onclick = async () => {
        if (!await confirmDialog({ title: '重置密码', message: `确认将「${esc(u.username)}」的密码重置为 123456？` })) return;
        await api.post(`/api/org/users/${id}/reset-password`, { password: '123456' });
        toast('密码已重置为 123456', 'ok');
      };
    });
  }

  function userForm(user) {
    const roleOptions = state.roles.map((r) => `
      <label class="check-item ${user?.roles?.some((x) => x.id === r.id) ? 'on' : ''}">
        <input type="checkbox" name="role" value="${r.id}" ${user?.roles?.some((x) => x.id === r.id) ? 'checked' : ''}/>
        ${esc(r.name)}</label>`).join('');
    const deptOptions = state.depts.map((d) => `<option value="${d.id}" ${user?.dept_id === d.id ? 'selected' : ''}>${'　'.repeat(d.depth)}${esc(d.name)}</option>`).join('');
    openModal({
      title: user ? '编辑账号' : '新增账号',
      subtitle: user ? '调整部门、岗位与角色后立即生效' : '默认初始密码 123456，首次登录后请修改',
      body: `<div class="form-grid">
          ${user ? '' : `<div class="field"><label>登录账号</label><input class="input" id="u-username" placeholder="英文账号" value=""/></div>`}
          <div class="field"><label>姓名</label><input class="input" id="u-name" value="${esc(user?.name || '')}"/></div>
          <div class="field"><label>所属部门</label><select class="select" id="u-dept">${deptOptions}</select></div>
          <div class="field"><label>岗位</label><input class="input" id="u-title" value="${esc(user?.title || '')}"/></div>
          <div class="field full"><label>角色（决定功能权限）</label>
            <div class="check-list">${roleOptions}</div></div>
          ${user ? `<div class="field full"><label>账号状态</label>
            <select class="select" id="u-status">
              <option value="active" ${user.status === 'active' ? 'selected' : ''}>启用</option>
              <option value="disabled" ${user.status === 'disabled' ? 'selected' : ''}>停用</option>
            </select></div>` : ''}
        </div>`,
      footer: `<button class="btn" data-act="cancel">取消</button>
               <button class="btn primary" data-act="ok">保存</button>`,
      onMount(root, close) {
        root.querySelector('[data-act="cancel"]').onclick = close;
        root.querySelectorAll('.check-item').forEach((label) => {
          label.onclick = (e) => {
            if (e.target.tagName !== 'INPUT') {
              const cb = label.querySelector('input');
              cb.checked = !cb.checked;
            }
            label.classList.toggle('on', label.querySelector('input').checked);
          };
        });
        root.querySelector('[data-act="ok"]').onclick = async () => {
          const roleIds = [...root.querySelectorAll('input[name="role"]:checked')].map((x) => Number(x.value));
          const payload = {
            name: root.querySelector('#u-name').value.trim(),
            dept_id: Number(root.querySelector('#u-dept').value),
            title: root.querySelector('#u-title').value.trim(),
            role_ids: roleIds,
          };
          if (user) {
            payload.status = root.querySelector('#u-status').value;
            await api.put(`/api/org/users/${user.id}`, payload);
          } else {
            payload.username = root.querySelector('#u-username').value.trim();
            if (!payload.username) return toast('请输入登录账号', 'err');
            await api.post('/api/org/users', payload);
          }
          toast('已保存', 'ok');
          close();
          await loadAll();
        };
      },
    });
  }

  /* ---------------- 角色 ---------------- */
  function renderRoles() {
    body.innerHTML = `<div class="org-grid" style="grid-template-columns:300px minmax(0,1fr)">
      <div class="card" style="box-shadow:none">
        <div class="card-head"><h3>角色列表</h3></div>
        <div class="dept-tree" id="role-list">
          ${state.roles.map((r) => `<div class="dept-node" data-id="${r.id}">
            ${icon('shield', 14)}<span>${esc(r.name)}</span>
            <span class="count">${r.user_count} 人</span></div>`).join('')}
        </div>
      </div>
      <div class="card" style="box-shadow:none">
        <div class="card-head"><h3 id="role-title">功能权限</h3>
          <button class="btn sm primary" id="save-perms" style="margin-left:auto">保存权限</button></div>
        <div class="card-body" id="perm-panel">${emptyState('左侧选择角色', 'shield')}</div>
      </div>
    </div>`;
    let current = state.roles[0];
    const select = (role) => {
      current = role;
      body.querySelector('#role-title').textContent = `${role.name} · 功能权限`;
      body.querySelectorAll('#role-list .dept-node').forEach((n) => n.classList.toggle('active', n.dataset.id === String(role.id)));
      body.querySelector('#perm-panel').innerHTML = state.catalog.map((group) => `
        <div class="perm-block">
          <div class="pb-head"><b>${esc(group.group)}</b></div>
          <div class="pb-body"><div class="check-list">
            ${group.items.map((item) => `<label class="check-item ${role.perms.includes(item.code) ? 'on' : ''}">
              <input type="checkbox" value="${item.code}" ${role.perms.includes(item.code) ? 'checked' : ''}/>
              ${esc(item.name)}<span class="small muted mono">${esc(item.code)}</span></label>`).join('')}
          </div></div>
        </div>`).join('');
      body.querySelectorAll('#perm-panel .check-item').forEach((label) => {
        label.onclick = (e) => {
          if (e.target.tagName !== 'INPUT') {
            const cb = label.querySelector('input');
            cb.checked = !cb.checked;
          }
          label.classList.toggle('on', label.querySelector('input').checked);
        };
      });
    };
    body.querySelectorAll('#role-list .dept-node').forEach((node) => {
      node.onclick = () => select(state.roles.find((r) => r.id === Number(node.dataset.id)));
    });
    if (current) select(current);
    body.querySelector('#save-perms').onclick = async () => {
      if (!current) return;
      const perms = [...body.querySelectorAll('#perm-panel input:checked')].map((x) => x.value);
      await api.put(`/api/org/roles/${current.id}/perms`, { perms });
      toast('权限已保存，相关用户刷新后生效', 'ok');
      await loadAll();
    };
  }

  /* ---------------- 模型配置 ---------------- */
  function renderConfig() {
    const cfg = state.config;
    const llm = cfg.llm;
    const emb = cfg.embedding || llm.embedding || {};
    const isSemantic = llm.embedding_mode && llm.embedding_mode !== 'local';
    body.innerHTML = `<div class="org-grid" style="grid-template-columns:minmax(0,1fr) 320px">
      <div class="card" style="box-shadow:none">
        <div class="card-head"><h3>大模型与向量化服务</h3>
          <span class="tag ${llm.provider === 'openai' ? 'green' : 'gray'}">${esc(llm.engine)}</span>
          <span class="tag ${isSemantic ? 'purple' : 'gray'}" style="margin-left:6px">
            向量：${esc(isSemantic ? (emb.model || '语义向量') : '本地哈希')}</span></div>
        <div class="card-body">
          ${cfg.reindex_hint ? `<div class="msg-note red" style="margin:0 0 14px">${icon('alert', 14)}
            <span>${esc(cfg.reindex_hint)}</span></div>` : ''}
          <div class="form-grid">
            <div class="field"><label>推理模式（对话）</label>
              <select class="select" id="c-provider">
                <option value="local" ${llm.provider === 'local' ? 'selected' : ''}>本地检索增强引擎（离线可用）</option>
                <option value="openai" ${llm.provider === 'openai' ? 'selected' : ''}>OpenAI 兼容接口（DeepSeek / Qwen / vLLM / Ollama）</option>
              </select></div>
            <div class="field"><label>对话模型</label>
              <input class="input" id="c-model" value="${esc(llm.model || '')}" placeholder="deepseek-chat"/></div>
            <div class="field full"><label>对话 Base URL</label>
              <input class="input" id="c-base" value="${esc(llm.base_url || '')}" placeholder="https://api.deepseek.com/v1"/></div>
            <div class="field full"><label>对话 API Key（当前：${esc(llm.api_key_masked || '未配置')}）</label>
              <input class="input" id="c-key" type="password" placeholder="留空表示不修改"/></div>
          </div>

          <div class="row gap8 mt16" style="margin-bottom:10px">
            <b style="font-size:12.5px">向量化（Embedding）</b>
            <span class="tag gray">${esc(emb.dim ? (emb.dim + ' 维') : '')}</span>
            <span class="small muted">对话与向量化可以是不同厂商，互不影响</span>
          </div>
          <div class="form-grid">
            <div class="field"><label>向量模式</label>
              <select class="select" id="c-embed">
                <option value="local" ${llm.embedding_mode === 'local' ? 'selected' : ''}>本地哈希向量（零依赖，无需网络）</option>
                <option value="openai" ${llm.embedding_mode === 'openai' ? 'selected' : ''}>OpenAI 兼容 /embeddings（BGE-M3 托管服务）</option>
                <option value="sentence_transformers" ${llm.embedding_mode === 'sentence_transformers' ? 'selected' : ''}>本地加载 BGE-M3（需安装 sentence-transformers）</option>
              </select></div>
            <div class="field"><label>向量模型</label>
              <input class="input" id="c-emodel" value="${esc(llm.embedding_model || 'BAAI/bge-m3')}" placeholder="BAAI/bge-m3"/></div>
            <div class="field full"><label>向量服务地址（留空则复用对话 Base URL）</label>
              <input class="input" id="c-ebase" value="${esc(llm.embedding_base_url || '')}"
                     placeholder="https://api.siliconflow.cn/v1 或 http://127.0.0.1:11434/v1"/></div>
            <div class="field"><label>向量 Key（当前：${esc(llm.embedding_api_key_masked || '未配置')}；Ollama 可留空）</label>
              <input class="input" id="c-ekey" type="password" placeholder="留空表示不修改"/></div>
            <div class="field"><label>向量维度</label>
              <input class="input" id="c-edim" value="${esc(llm.embedding_dim || 1024)}" placeholder="BGE-M3 为 1024"/></div>
          </div>
          <div class="row gap8 mt12">
            <button class="btn primary" id="save-cfg">保存配置</button>
            <button class="btn" id="test-cfg">${icon('refresh')}对话连通测试</button>
            <button class="btn" id="test-embed">${icon('layers')}向量服务测试</button>
            <button class="btn" id="do-reindex">${icon('refresh')}重建向量索引</button>
            <span class="small muted" id="test-result"></span>
          </div>
          <div class="msg-note blue mt16">${icon('alert', 14)}
            <span>切换向量模式后必须重建索引（<code>python scripts/reindex_embeddings.py</code>），
            否则库内向量与查询向量不在同一空间，相似度会失真。</span></div>
        </div>
      </div>
      <div class="card" style="box-shadow:none">
        <div class="card-head"><h3>检索与治理参数</h3></div>
        <div class="card-body">
          <div class="kv-list" style="grid-template-columns:1fr auto;row-gap:14px">
            <span class="k">切片长度</span><b>${cfg.thresholds.chunk_size} tokens</b>
            <span class="k">切片重叠</span><b>${Math.round(cfg.thresholds.overlap * 100)}%</b>
            <span class="k">FAQ 聚类阈值</span><b>${cfg.thresholds.cluster}</b>
            <span class="k">FAQ 缓存阈值</span><b>${cfg.thresholds.cache}</b>
            <span class="k">知识缺口阈值</span><b>${cfg.thresholds.gap}</b>
            <span class="k">知识单元</span><b>${fmtNum(cfg.stats.ku_total)}</b>
            <span class="k">切片总数</span><b>${fmtNum(cfg.stats.chunk_total)}</b>
            <span class="k">索引 token</span><b>${fmtNum(cfg.stats.token_total)}</b>
          </div>
          <div class="mt16 small muted" style="line-height:1.9">
            向量化：${esc(emb.mode === 'local' ? '本地 signed-hashing（512 维）' : (emb.model || '') + (emb.dim ? '（' + emb.dim + ' 维）' : ''))}<br/>
            ${emb.base_url ? `服务地址：${esc(emb.base_url)}<br/>` : ''}
            检索：向量 Top-20 + BM25 Top-10 → RRF 融合<br/>
            鉴权：召回后按四维权限裁剪，再拼装 Prompt<br/>
            阈值随向量口径自动切换（本地 0.60/0.72/0.34，语义 0.88/0.90/0.50）
          </div>
        </div>
      </div>
    </div>`;

    body.querySelector('#save-cfg').onclick = async () => {
      const payload = {
        provider: body.querySelector('#c-provider').value,
        model: body.querySelector('#c-model').value.trim(),
        base_url: body.querySelector('#c-base').value.trim(),
        embedding_mode: body.querySelector('#c-embed').value,
        embedding_model: body.querySelector('#c-emodel').value.trim(),
        embedding_base_url: body.querySelector('#c-ebase').value.trim(),
        embedding_dim: body.querySelector('#c-edim').value.trim(),
      };
      const key = body.querySelector('#c-key').value.trim();
      if (key) payload.api_key = key;
      const ekey = body.querySelector('#c-ekey').value.trim();
      if (ekey) payload.embedding_api_key = ekey;
      const res = await api.put('/api/system/config', payload);
      toast('配置已保存' + (res.reindex_hint ? '，请重建向量索引' : ''), res.reindex_hint ? 'info' : 'ok', 3600);
      await loadAll();
    };
    body.querySelector('#test-cfg').onclick = async () => {
      const res = await api.post('/api/system/config/test', {});
      body.querySelector('#test-result').textContent = res.message;
      toast(res.ok ? '对话模型连通正常' : '对话模型连接失败', res.ok ? 'ok' : 'err');
    };
    body.querySelector('#test-embed').onclick = async () => {
      const res = await api.post('/api/system/config/test-embedding', {});
      body.querySelector('#test-result').textContent = res.message;
      toast(res.ok ? `向量服务正常（${res.dim} 维）` : '向量服务不可用', res.ok ? 'ok' : 'err', 4000);
    };
    body.querySelector('#do-reindex').onclick = async () => {
      if (!await confirmDialog({
        title: '重建向量索引',
        message: '将按当前向量配置重算全部切片与 FAQ 向量，期间问答可能短暂变慢。确定继续？',
        okText: '开始重建',
      })) return;
      const btn = body.querySelector('#do-reindex');
      btn.disabled = true;
      btn.textContent = '重建中…';
      try {
        const res = await api.post('/api/system/reindex', {});
        toast(`重建完成：${res.chunks} 切片 / ${res.faqs} FAQ / ${res.dim} 维`, 'ok', 4200);
        await loadAll();
      } catch (err) {
        toast(err.message, 'err', 5000);
      } finally {
        btn.disabled = false;
        btn.innerHTML = `${icon('refresh')}重建向量索引`;
      }
    };
  }

  /* ---------------- 审计 ---------------- */
  async function renderAudit() {
    const data = await api.get(`/api/system/audit?page=${state.auditPage}&size=15`);
    body.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead>
      <tbody>${data.items.map((a) => `
        <tr>
          <td class="mono small">${esc(a.created_at)}</td>
          <td>${esc(a.username || 'system')}</td>
          <td>${tag(a.action, a.action.includes('denied') ? 'red' : a.action.includes('delete') ? 'orange' : 'gray')}</td>
          <td class="mono small">${esc(a.target || '-')}</td>
          <td class="small">${esc((a.detail || '').slice(0, 60))}</td>
          <td class="mono small">${esc(a.ip || '-')}</td>
        </tr>`).join('') || `<tr><td colspan="6">${emptyState('暂无审计记录')}</td></tr>`}</tbody>
    </table></div>
    <div class="row between mt12">
      <span class="small muted">共 ${data.total} 条</span>
      <div class="row gap8">
        <button class="btn sm" id="a-prev" ${state.auditPage <= 1 ? 'disabled' : ''}>上一页</button>
        <span class="small muted">${state.auditPage} / ${Math.max(1, Math.ceil(data.total / 15))}</span>
        <button class="btn sm" id="a-next" ${state.auditPage >= Math.ceil(data.total / 15) ? 'disabled' : ''}>下一页</button>
      </div>
    </div>`;
    body.querySelector('#a-prev').onclick = () => { if (state.auditPage > 1) { state.auditPage -= 1; renderAudit(); } };
    body.querySelector('#a-next').onclick = () => {
      if (state.auditPage < Math.ceil(data.total / 15)) { state.auditPage += 1; renderAudit(); }
    };
  }
}
