/* 知识维护与导入中心 */
import { api, store } from '../api.js';
import {
  esc, icon, timeAgo, openDrawer, openModal, confirmDialog, toast, emptyState, tag,
} from '../ui.js';

const state = {
  page: 1,
  size: 10,
  keyword: '',
  format: '',
  category: '',
  perm: '',
  items: [],
  total: 0,
  filters: { depts: [], roles: [], users: [], categories: [], formats: [] },
};

export async function renderKB(el, ctx) {
  state.keyword = ctx.params?.get('keyword') || '';
  el.innerHTML = `<div class="page-head">
      <div><h2>知识维护与导入</h2><p>多格式文档解析切片、四维数据权限配置与知识单元全生命周期管理</p></div>
      <div class="actions">
        <button class="btn" id="refresh-kb">${icon('refresh')}刷新</button>
        <button class="btn primary" id="import-btn">${icon('upload')}导入文档</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head" style="border:0;padding-bottom:0">
        <div class="toolbar" style="flex:1">
          <input class="input" id="kb-search" placeholder="搜索标题 / 编号 / 摘要" style="min-width:220px" value="${esc(state.keyword)}" />
          <select class="select" id="f-format"><option value="">全部格式</option></select>
          <select class="select" id="f-category"><option value="">全部分类</option></select>
          <select class="select" id="f-perm">
            <option value="">全部权限</option>
            <option value="global">全局公开</option>
            <option value="department">部门共享</option>
            <option value="role">角色可见</option>
            <option value="user">个人专属</option>
          </select>
          <button class="btn" id="search-btn">${icon('search')}查询</button>
        </div>
      </div>
      <div class="table-wrap" id="kb-table" style="min-height:420px"></div>
      <div class="row between" style="padding:12px 18px;border-top:1px solid var(--border-2)">
        <span class="small muted" id="kb-total"></span>
        <div class="row gap8" id="kb-pager"></div>
      </div>
    </div>`;

  await loadFilters();
  await loadTable();

  el.querySelector('#refresh-kb').onclick = () => loadTable();
  el.querySelector('#search-btn').onclick = () => {
    state.keyword = el.querySelector('#kb-search').value.trim();
    state.page = 1;
    loadTable();
  };
  el.querySelector('#kb-search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') el.querySelector('#search-btn').click();
  });
  [['#f-format', 'format'], ['#f-category', 'category'], ['#f-perm', 'perm']].forEach(([sel, key]) => {
    el.querySelector(sel).onchange = (e) => {
      state[key] = e.target.value;
      state.page = 1;
      loadTable();
    };
  });
  el.querySelector('#import-btn').onclick = () => openImport();

  async function loadFilters() {
    const f = await api.get('/api/kb/filters');
    state.filters = f;
    const fmtSel = el.querySelector('#f-format');
    f.formats.forEach((x) => fmtSel.insertAdjacentHTML('beforeend', `<option value="${esc(x)}">${esc(String(x).toUpperCase())}</option>`));
    const catSel = el.querySelector('#f-category');
    f.categories.forEach((x) => catSel.insertAdjacentHTML('beforeend', `<option value="${esc(x)}">${esc(x)}</option>`));
  }

  async function loadTable() {
    const params = new URLSearchParams({
      page: state.page, size: state.size,
      keyword: state.keyword, format: state.format, category: state.category, perm: state.perm,
    });
    const data = await api.get(`/api/kb?${params}`);
    state.items = data.items;
    state.total = data.total;
    renderTable();
  }

  function permTags(p) {
    const tags = [];
    if (p?.global) tags.push(tag('全局公开', 'green'));
    if (p?.dept?.length) tags.push(tag(`部门 ${p.dept.length}`, 'blue'));
    if (p?.role?.length) tags.push(tag(`角色 ${p.role.length}`, 'purple'));
    if (p?.user?.length) tags.push(tag(`个人 ${p.user.length}`, 'orange'));
    return tags.length ? tags.join(' ') : tag('未授权', 'gray');
  }

  function renderTable() {
    const wrap = el.querySelector('#kb-table');
    el.querySelector('#kb-total').textContent = `共 ${state.total} 个知识单元 · 第 ${state.page} 页`;
    if (!state.items.length) {
      wrap.innerHTML = emptyState('没有匹配的知识单元，试试调整筛选条件或导入新文档');
    } else {
      wrap.innerHTML = `<table class="tbl">
        <thead><tr>
          <th style="width:34%">知识单元</th><th>格式</th><th>分类</th>
          <th>数据权限</th><th>切片</th><th>更新时间</th><th style="width:150px">操作</th>
        </tr></thead>
        <tbody>${state.items.map((k) => `
          <tr data-id="${k.id}">
            <td><div class="t-title">${esc(k.title)}</div>
              <div class="t-sub mono">${esc(k.code)} · ${esc(k.source_filename || '')}</div></td>
            <td>${tag(String(k.format || '').toUpperCase(), 'gray')}</td>
            <td>${esc(k.category || '未分类')}</td>
            <td>${permTags(k.perms)}</td>
            <td>${k.chunk_count} 片<div class="t-sub">${k.token_count} tokens</div></td>
            <td>${timeAgo(k.updated_at)}<div class="t-sub">${esc(k.dept_name || '')}</div></td>
            <td class="actions">
              <button class="btn sm ghost" data-act="detail" title="详情">${icon('eye', 13)}</button>
              <button class="btn sm ghost" data-act="perm" title="权限">${icon('lock', 13)}</button>
              <button class="btn sm ghost" data-act="edit" title="编辑">${icon('edit', 13)}</button>
              <button class="btn sm ghost" data-act="del" title="删除">${icon('trash', 13)}</button>
            </td>
          </tr>`).join('')}</tbody></table>`;
      wrap.querySelectorAll('tr[data-id]').forEach((row) => {
        const id = Number(row.dataset.id);
        const item = state.items.find((x) => x.id === id);
        row.querySelector('[data-act="detail"]').onclick = () => openDetail(item);
        row.querySelector('[data-act="perm"]').onclick = () => openPerm(item);
        row.querySelector('[data-act="edit"]').onclick = () => openEdit(item);
        row.querySelector('[data-act="del"]').onclick = async () => {
          if (!await confirmDialog({
            title: '删除知识单元',
            message: `确定删除《${esc(item.title)}》吗？其切片、权限配置将一并移除，且不可恢复。`,
            okText: '删除', danger: true,
          })) return;
          await api.del(`/api/kb/${id}`);
          toast('已删除', 'ok');
          loadTable();
        };
      });
    }
    renderPager();
  }

  function renderPager() {
    const pages = Math.max(1, Math.ceil(state.total / state.size));
    const pager = el.querySelector('#kb-pager');
    pager.innerHTML = `<button class="btn sm" ${state.page <= 1 ? 'disabled' : ''} id="prev">上一页</button>
      <span class="small muted">${state.page} / ${pages}</span>
      <button class="btn sm" ${state.page >= pages ? 'disabled' : ''} id="next">下一页</button>`;
    pager.querySelector('#prev').onclick = () => { if (state.page > 1) { state.page -= 1; loadTable(); } };
    pager.querySelector('#next').onclick = () => { if (state.page < pages) { state.page += 1; loadTable(); } };
  }

  /* ---------------- 权限配置 ---------------- */
  async function openPerm(item) {
    const detail = await api.get(`/api/kb/${item.id}`);
    const perms = detail.item.perms;
    const f = state.filters;
    const tree = buildTree(f.depts);
    const rows = [];
    const walk = (nodes, depth) => {
      nodes.forEach((n) => {
        rows.push(`<div class="tree-node" style="padding-left:${depth * 16}px">
          <label class="check-item ${perms.departments.includes(n.id) ? 'on' : ''}" style="border:0;padding:4px 6px">
            <input type="checkbox" value="${n.id}" ${perms.departments.includes(n.id) ? 'checked' : ''}/>
            <span>${esc(n.name)}</span></label></div>`);
        if (n.children) walk(n.children, depth + 1);
      });
    };
    walk(tree, 0);

    const body = `
      <div class="perm-block">
        <div class="pb-head">${icon('shield', 15)}<b>全局公开</b>
          <span class="small muted">（全员可访问）</span>
          <div class="switch ${perms.global ? 'on' : ''}" id="sw-global" style="margin-left:auto"></div></div>
        <div class="pb-body small muted">默认情况下知识单元不公开；开启后所有登录用户均可检索到该知识单元。</div>
      </div>
      <div class="perm-block">
        <div class="pb-head">${icon('org', 15)}<b>部门共享</b><span class="tag blue" style="margin-left:auto">OR 语义</span></div>
        <div class="pb-body">${rows.join('') || '<div class="muted small">暂无部门</div>'}</div>
      </div>
      <div class="perm-block">
        <div class="pb-head">${icon('users', 15)}<b>角色可见</b></div>
        <div class="pb-body"><div class="check-list">
          ${f.roles.map((r) => `<label class="check-item ${perms.roles.includes(r.id) ? 'on' : ''}">
            <input type="checkbox" name="role" value="${r.id}" ${perms.roles.includes(r.id) ? 'checked' : ''}/>
            ${esc(r.name)}</label>`).join('')}
        </div></div>
      </div>
      <div class="perm-block">
        <div class="pb-head">${icon('user', 15)}<b>个人专属</b></div>
        <div class="pb-body"><div class="check-list" style="max-height:150px;overflow:auto">
          ${f.users.map((u) => `<label class="check-item ${perms.users.includes(u.id) ? 'on' : ''}">
            <input type="checkbox" name="user" value="${u.id}" ${perms.users.includes(u.id) ? 'checked' : ''}/>
            ${esc(u.name)}<span class="muted small">${esc(u.username)}</span></label>`).join('')}
        </div></div>
      </div>
      <div class="msg-note blue">${icon('alert', 14)}
        <span>命中任一权限实体即可访问（OR）。权限变更即时生效，并同步失效鉴权缓存。</span></div>`;

    const drawer = openDrawer({
      title: `数据权限 · ${item.title}`,
      body,
      footer: `<button class="btn" data-close>取消</button>
               <button class="btn primary" id="save-perm">保存权限</button>`,
      onMount(root) {
        root.querySelector('[data-close]').onclick = () => drawer.close();
        const sw = root.querySelector('#sw-global');
        sw.onclick = () => sw.classList.toggle('on');
        root.querySelectorAll('.tree-node .check-item').forEach((label) => {
          label.onclick = (e) => {
            if (e.target.tagName !== 'INPUT') {
              const cb = label.querySelector('input');
              cb.checked = !cb.checked;
            }
            label.classList.toggle('on', label.querySelector('input').checked);
          };
        });
        root.querySelector('#save-perm').onclick = async () => {
          const collect = (name) => [...root.querySelectorAll(`input[name="${name}"]:checked`)].map((x) => Number(x.value));
          const departments = [...root.querySelectorAll('.tree-node input:checked')].map((x) => Number(x.value));
          await api.put(`/api/kb/${item.id}/perms`, {
            global: sw.classList.contains('on'),
            departments,
            roles: collect('role'),
            users: collect('user'),
          });
          toast('权限已保存并即时生效', 'ok');
          drawer.close();
          loadTable();
        };
      },
    });
  }

  function buildTree(depts) {
    const map = new Map(depts.map((d) => [d.id, { ...d, children: [] }]));
    const roots = [];
    map.forEach((n) => {
      if (n.parent_id && map.has(n.parent_id)) map.get(n.parent_id).children.push(n);
      else roots.push(n);
    });
    return roots;
  }

  /* ---------------- 详情 ---------------- */
  async function openDetail(item) {
    const detail = await api.get(`/api/kb/${item.id}`);
    const k = detail.item;
    const body = `
      <div class="kv-list">
        <div class="k">编号</div><div class="mono">${esc(k.code)}</div>
        <div class="k">标题</div><div><b>${esc(k.title)}</b></div>
        <div class="k">分类 / 格式</div><div>${esc(k.category || '未分类')} · ${esc(String(k.format).toUpperCase())}</div>
        <div class="k">归属部门</div><div>${esc(k.dept_name || '—')}</div>
        <div class="k">规模</div><div>${k.chunk_count} 个切片 · ${k.char_count} 字符 · ${k.token_count} tokens</div>
        <div class="k">状态</div><div>${k.status === 'enabled' ? tag('已启用', 'green') : tag('已停用', 'gray')}</div>
        <div class="k">摘要</div><div>${esc(k.summary || '—')}</div>
      </div>
      <h4 style="margin:18px 0 8px;font-size:13px">切片预览（前 3 片）</h4>
      ${k.chunks.map((c) => `<div class="chunk-card">
          <div class="ch-head"><span>#${c.chunk_index + 1}</span><span>P${c.page_no}</span>
            <span>第 ${c.para_index + 1} 段</span><span>字符 ${c.char_start}-${c.char_end}</span>
            <span style="margin-left:auto">${c.token_count} tokens</span></div>
          <div class="ch-text">${esc(c.preview)}</div></div>`).join('') || emptyState('暂无切片')}
      <div class="row gap8 mt12">
        <button class="btn" id="reparse">${icon('refresh')}重建索引</button>
        <button class="btn" id="toggle-status">${icon('shield')}${k.status === 'enabled' ? '停用' : '启用'}</button>
      </div>`;
    const drawer = openDrawer({
      title: '知识单元详情', body, wide: true,
      onMount(root) {
        root.querySelector('#reparse').onclick = async () => {
          await api.post(`/api/kb/${item.id}/reparse`);
          toast('索引已重建', 'ok');
          drawer.close();
          loadTable();
        };
        root.querySelector('#toggle-status').onclick = async () => {
          await api.put(`/api/kb/${item.id}/status`, { status: k.status === 'enabled' ? 'disabled' : 'enabled' });
          toast('状态已更新', 'ok');
          drawer.close();
          loadTable();
        };
      },
    });
  }

  function openEdit(item) {
    openModal({
      title: '编辑知识单元',
      body: `<div class="field"><label>标题</label><input class="input" id="e-title" value="${esc(item.title)}"/></div>
        <div class="field"><label>分类</label><input class="input" id="e-cat" value="${esc(item.category || '')}"/></div>`,
      footer: `<button class="btn" data-act="cancel">取消</button>
               <button class="btn primary" data-act="save">保存</button>`,
      onMount(root, close) {
        root.querySelector('[data-act="cancel"]').onclick = close;
        root.querySelector('[data-act="save"]').onclick = async () => {
          await api.put(`/api/kb/${item.id}`, {
            title: root.querySelector('#e-title').value.trim(),
            category: root.querySelector('#e-cat').value.trim(),
          });
          toast('已保存', 'ok');
          close();
          loadTable();
        };
      },
    });
  }

  /* ---------------- 导入 ---------------- */
  function openImport() {
    const body = `
      <div class="field"><label>归属分类</label>
        <input class="input" id="up-cat" placeholder="例如：财务制度 / 客服政策" value="未分类"/></div>
      <div class="dropzone" id="dz">
        <div class="dz-icon">${icon('upload', 22)}</div>
        <b>拖拽文件到此处，或点击选择</b>
        <span>支持 PDF / DOCX / Markdown / TXT，可多选批量上传；自动清洗、切片与向量化</span>
        <input type="file" id="file-input" multiple accept=".pdf,.docx,.md,.markdown,.txt" class="hidden"/>
      </div>
      <div class="upload-list" id="upload-list"></div>
      <div class="small muted mt12">默认安全策略：导入后仅创建者可见，请在台账中为知识单元配置四维权限。</div>`;
    const drawer = openDrawer({ title: '批量导入知识文档', body, wide: true, footer: '' });
    const dz = drawer.el.querySelector('#dz');
    const fileInput = drawer.el.querySelector('#file-input');
    const list = drawer.el.querySelector('#upload-list');

    dz.onclick = () => fileInput.click();
    dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('drag'); };
    dz.ondragleave = () => dz.classList.remove('drag');
    dz.ondrop = (e) => {
      e.preventDefault();
      dz.classList.remove('drag');
      upload([...e.dataTransfer.files]);
    };
    fileInput.onchange = () => upload([...fileInput.files]);

    function upload(files) {
      if (!files.length) return;
      const category = drawer.el.querySelector('#up-cat').value.trim() || '未分类';
      list.innerHTML = files.map((f) => `<div class="upload-row" data-name="${esc(f.name)}">
          <span class="tag gray">${esc((f.name.split('.').pop() || '').toUpperCase())}</span>
          <span class="fname">${esc(f.name)}</span>
          <div class="progress" style="width:110px"><i style="width:0%"></i></div>
          <span class="status small muted" style="width:64px;text-align:right">等待中</span>
        </div>`).join('');

      const fd = new FormData();
      files.forEach((f) => fd.append('files', f, f.name));
      fd.append('category', category);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/ingest/upload');
      xhr.setRequestHeader('Authorization', `Bearer ${store.token}`);
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round((e.loaded / e.total) * 100);
        list.querySelectorAll('.upload-row').forEach((row) => {
          row.querySelector('.progress i').style.width = `${pct}%`;
          row.querySelector('.status').textContent = pct < 100 ? `${pct}%` : '解析中';
        });
      };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = {}; }
        if (xhr.status !== 200 || data.code) {
          toast(data.message || '上传失败', 'err');
          list.querySelectorAll('.status').forEach((s) => { s.textContent = '失败'; });
          return;
        }
        const results = data.data.results || [];
        list.innerHTML = results.map((r) => `<div class="upload-row">
            <span class="tag ${r.ok ? 'green' : r.duplicate ? 'orange' : 'red'}">${r.ok ? '成功' : r.duplicate ? '重复' : '失败'}</span>
            <span class="fname">${esc(r.filename)}</span>
            <span class="status small ${r.ok ? 'muted' : ''}" style="margin-left:auto">
              ${r.ok ? '已切片入库 · 可配置权限' : esc(r.message || '')}</span>
          </div>`).join('') + `<div class="row gap8 mt12">
            <button class="btn" id="close-import">完成</button>
            <span class="small muted">如需调整权限，请在台账中点击锁形按钮</span></div>`;
        list.querySelector('#close-import').onclick = () => drawer.close();
        const okCount = results.filter((r) => r.ok).length;
        if (okCount) toast(`成功导入 ${okCount} 个文档`, 'ok');
        loadTable();
      };
      xhr.onerror = () => toast('网络异常，上传失败', 'err');
      xhr.send(fd);
    }
  }
}
