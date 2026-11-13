/* 知识沉淀与运营：候选 FAQ、已发布 FAQ、知识缺口、补充任务 */
import { api } from '../api.js';
import {
  esc, icon, timeAgo, openModal, confirmDialog, toast, emptyState, tag, renderMarkdown,
} from '../ui.js';

const state = { tab: 'candidates', candidates: [], faqs: [], gaps: [], tasks: [], kuMap: {} };

export async function renderOps(el, ctx) {
  if (ctx.params?.get('tab')) state.tab = ctx.params.get('tab');
  el.innerHTML = `<div class="page-head">
      <div><h2>知识沉淀与运营</h2>
        <p>把高频提问沉淀为标准 FAQ 并缓存加速，把未命中的提问变成知识建设任务</p></div>
      <div class="actions">
        <button class="btn" id="refresh-ops">${icon('refresh')}刷新</button>
        <button class="btn primary" id="mine-btn">${icon('spark')}立即挖掘高频问题</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head" style="border:0;padding:6px 18px 0">
        <div class="tabs" id="ops-tabs" style="border:0">
          <div class="tab on" data-tab="candidates">候选 FAQ<span class="n" id="n-cand">0</span></div>
          <div class="tab" data-tab="faqs">已发布 FAQ<span class="n" id="n-faq">0</span></div>
          <div class="tab" data-tab="gaps">知识缺口<span class="n" id="n-gap">0</span></div>
          <div class="tab" data-tab="tasks">补充任务<span class="n" id="n-task">0</span></div>
        </div>
      </div>
      <div class="card-body" id="ops-body" style="min-height:480px"></div>
    </div>`;

  const body = el.querySelector('#ops-body');

  el.querySelectorAll('#ops-tabs .tab').forEach((t) => {
    t.classList.toggle('on', t.dataset.tab === state.tab);
    t.onclick = () => {
      state.tab = t.dataset.tab;
      el.querySelectorAll('#ops-tabs .tab').forEach((x) => x.classList.toggle('on', x === t));
      render();
    };
  });
  el.querySelector('#refresh-ops').onclick = () => loadAll();
  el.querySelector('#mine-btn').onclick = async () => {
    const btn = el.querySelector('#mine-btn');
    btn.disabled = true;
    btn.innerHTML = '聚类分析中…';
    try {
      const res = await api.post('/api/faq/mine', { days: 30 });
      toast(`完成：${res.clusters} 个问题簇，新增 ${res.created} 条候选 FAQ`, 'ok', 3600);
      await loadAll();
    } finally {
      btn.disabled = false;
      btn.innerHTML = `${icon('spark')}立即挖掘高频问题`;
    }
  };

  await loadAll();

  async function loadAll() {
    const [cands, faqs, gaps, tasks, kbs] = await Promise.all([
      api.get('/api/faq/candidates'),
      api.get('/api/faqs'),
      api.get('/api/gaps?status=all'),
      api.get('/api/tasks'),
      api.get('/api/kb?size=100'),
    ]);
    state.candidates = cands.items;
    state.faqs = faqs.items;
    state.gaps = gaps.items;
    state.tasks = tasks.items;
    state.kuMap = Object.fromEntries(kbs.items.map((k) => [k.id, k.title]));
    el.querySelector('#n-cand').textContent = state.candidates.filter((c) => c.status === 'pending').length;
    el.querySelector('#n-faq').textContent = state.faqs.length;
    el.querySelector('#n-gap').textContent = state.gaps.filter((g) => g.status === 'open').length;
    el.querySelector('#n-task').textContent = state.tasks.filter((t) => t.status !== 'done').length;
    render();
  }

  function render() {
    if (state.tab === 'candidates') renderCandidates();
    else if (state.tab === 'faqs') renderFaqs();
    else if (state.tab === 'gaps') renderGaps();
    else renderTasks();
  }

  /* ---------------- 候选 FAQ ---------------- */
  function renderCandidates() {
    const list = state.candidates;
    if (!list.length) {
      body.innerHTML = emptyState('暂无候选 FAQ，点击右上角「立即挖掘高频问题」开始聚类分析');
      return;
    }
    body.innerHTML = `<div class="small muted" style="margin-bottom:12px">
        对近 30 天提问做语义聚类（阈值 0.60 / 词形 + 向量融合），簇内频次 ≥ 5 自动生成候选，需人工审核后发布。</div>
      ${list.map((c) => `
        <div class="faq-card" data-id="${c.id}">
          <div class="f-top">
            <div class="f-q">${esc(c.question)}</div>
            ${c.status === 'pending' ? tag('待审核', 'orange') : c.status === 'published' ? tag('已发布', 'green') : tag('已驳回', 'gray')}
          </div>
          <div class="row gap8 wrap" style="margin-bottom:10px">
            <span class="tag blue">频次 ${c.freq}</span>
            <span class="tag purple">置信度 ${(c.confidence || 0).toFixed(2)}</span>
            <span class="tag gray">关联 ${(c.related_ku || []).length} 个知识单元</span>
            <span class="small muted">代表问法：${esc(c.sample_query || '')}</span>
          </div>
          <div class="f-body">${renderMarkdown(c.answer || '')}</div>
          <div class="f-foot">
            <span class="meta">${timeAgo(c.updated_at || c.created_at)}</span>
            <div style="margin-left:auto" class="row gap8">
              <button class="btn sm" data-act="edit">${icon('edit', 12)}编辑</button>
              ${c.status === 'pending' ? `
                <button class="btn sm" data-act="reject">驳回</button>
                <button class="btn sm primary" data-act="publish">${icon('check', 12)}审核发布</button>` : ''}
            </div>
          </div>
        </div>`).join('')}`;

    body.querySelectorAll('.faq-card[data-id]').forEach((card) => {
      const id = Number(card.dataset.id);
      const c = list.find((x) => x.id === id);
      const edit = card.querySelector('[data-act="edit"]');
      if (edit) edit.onclick = () => editCandidate(c);
      const pub = card.querySelector('[data-act="publish"]');
      if (pub) {
        pub.onclick = async () => {
          await api.post(`/api/faq/candidates/${id}/publish`);
          toast('已发布并写入 FAQ 缓存，同类提问将毫秒直出', 'ok', 3200);
          await loadAll();
        };
      }
      const rej = card.querySelector('[data-act="reject"]');
      if (rej) {
        rej.onclick = async () => {
          if (!await confirmDialog({ title: '驳回候选 FAQ', message: '驳回后该问题簇不再重复推荐。', danger: true })) return;
          await api.post(`/api/faq/candidates/${id}/reject`, { reason: '人工判定不适用' });
          toast('已驳回', 'ok');
          await loadAll();
        };
      }
    });
  }

  function editCandidate(c) {
    openModal({
      title: '审核与编辑标准问答',
      subtitle: '可修正标准问法与答案，发布后立即生效并写入缓存',
      body: `<div class="field"><label>标准问题</label>
          <input class="input" id="c-q" value="${esc(c.question)}"/></div>
        <div class="field"><label>标准答案</label>
          <textarea class="textarea" id="c-a" rows="10">${esc(c.answer || '')}</textarea></div>
        <div class="msg-note blue">${icon('alert', 14)}
          <span>答案应直接、可执行，并注明来源文档，便于后续溯源。</span></div>`,
      footer: `<button class="btn" data-act="cancel">取消</button>
               <button class="btn" data-act="save">保存</button>
               <button class="btn primary" data-act="publish">保存并发布</button>`,
      onMount(root, close) {
        root.querySelector('[data-act="cancel"]').onclick = close;
        root.querySelector('[data-act="save"]').onclick = async () => {
          await api.put(`/api/faq/candidates/${c.id}`, {
            question: root.querySelector('#c-q').value.trim(),
            answer: root.querySelector('#c-a').value.trim(),
          });
          toast('已保存草稿', 'ok');
          close();
          await loadAll();
        };
        root.querySelector('[data-act="publish"]').onclick = async () => {
          await api.put(`/api/faq/candidates/${c.id}`, {
            question: root.querySelector('#c-q').value.trim(),
            answer: root.querySelector('#c-a').value.trim(),
          });
          await api.post(`/api/faq/candidates/${c.id}/publish`);
          toast('已发布并写入缓存', 'ok');
          close();
          await loadAll();
        };
      },
    });
  }

  /* ---------------- 已发布 FAQ ---------------- */
  function renderFaqs() {
    if (!state.faqs.length) {
      body.innerHTML = emptyState('暂无已发布 FAQ，请先在候选 FAQ 中审核发布');
      return;
    }
    body.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th style="width:38%">标准问题</th><th>命中次数</th><th>缓存加速</th><th>发布时间</th><th>操作</th></tr></thead>
      <tbody>${state.faqs.map((f) => `
        <tr data-id="${f.id}">
          <td><div class="t-title">${esc(f.question)}</div>
            <div class="t-sub">${esc((f.answer || '').replace(/\n/g, ' ').slice(0, 70))}…</div></td>
          <td>${f.hit_count} 次</td>
          <td><div class="switch ${f.cache_enabled ? 'on' : ''}" data-act="cache" style="display:inline-block;vertical-align:middle"></div></td>
          <td>${timeAgo(f.published_at)}</td>
          <td class="actions">
            <button class="btn sm ghost" data-act="view">${icon('eye', 13)}</button>
            <button class="btn sm ghost" data-act="del">${icon('trash', 13)}</button>
          </td>
        </tr>`).join('')}</tbody></table></div>`;

    body.querySelectorAll('tr[data-id]').forEach((row) => {
      const id = Number(row.dataset.id);
      const f = state.faqs.find((x) => x.id === id);
      row.querySelector('[data-act="cache"]').onclick = async (e) => {
        const el2 = e.currentTarget;
        const next = el2.classList.contains('on') ? 0 : 1;
        await api.put(`/api/faqs/${id}`, { cache_enabled: next });
        el2.classList.toggle('on', next === 1);
        toast(next ? '已开启缓存加速' : '已关闭缓存加速', 'ok');
      };
      row.querySelector('[data-act="view"]').onclick = () => {
        openModal({
          title: f.question,
          body: `<div class="f-body">${renderMarkdown(f.answer)}</div>
            <div class="small muted mt12">命中 ${f.hit_count} 次 · 发布 ${timeAgo(f.published_at)}</div>`,
          footer: '<button class="btn" data-act="close">关闭</button>',
          onMount(r, close) { r.querySelector('[data-act="close"]').onclick = close; },
        });
      };
      row.querySelector('[data-act="del"]').onclick = async () => {
        if (!await confirmDialog({ title: '下线 FAQ', message: `确定删除「${esc(f.question)}」吗？`, danger: true })) return;
        await api.del(`/api/faqs/${id}`);
        toast('已删除', 'ok');
        await loadAll();
      };
    });
  }

  /* ---------------- 知识缺口 ---------------- */
  function renderGaps() {
    if (!state.gaps.length) {
      body.innerHTML = emptyState('暂无知识缺口，知识覆盖良好');
      return;
    }
    const statusTag = (s) => ({
      open: tag('待补充', 'red'), task: tag('已建任务', 'orange'),
      resolved: tag('已补充', 'green'), ignored: tag('已忽略', 'gray'),
    }[s] || tag(s, 'gray'));
    body.innerHTML = `<div class="small muted" style="margin-bottom:12px">
        检索置信度低于阈值（0.34）或未命中任何切片的提问自动归入缺口池，按出现频次排序。</div>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th style="width:36%">提问</th><th>频次</th><th>提问部门</th><th>最高相似度</th>
          <th>最近出现</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${state.gaps.map((g) => `
          <tr data-id="${g.id}">
            <td><div class="t-title">${esc(g.question)}</div>
              <div class="t-sub">建议分类：${esc(g.suggested_category || '待定')}</div></td>
            <td><b>${g.freq}</b></td>
            <td>${esc(g.dept_name || '—')}</td>
            <td class="mono">${(g.top1_sim || 0).toFixed(2)}</td>
            <td>${timeAgo(g.last_seen)}</td>
            <td>${statusTag(g.status)}</td>
            <td class="actions">
              ${g.status === 'open' ? `
                <button class="btn sm primary" data-act="task">${icon('plus', 12)}转建任务</button>
                <button class="btn sm ghost" data-act="ignore">忽略</button>` : '<span class="small muted">已处理</span>'}
            </td>
          </tr>`).join('')}</tbody></table></div>`;

    body.querySelectorAll('tr[data-id]').forEach((row) => {
      const id = Number(row.dataset.id);
      const taskBtn = row.querySelector('[data-act="task"]');
      if (taskBtn) {
        taskBtn.onclick = async () => {
          await api.post(`/api/gaps/${id}/task`, { owner: '知识管理员' });
          toast('已生成知识补充任务', 'ok');
          await loadAll();
        };
      }
      const ignore = row.querySelector('[data-act="ignore"]');
      if (ignore) {
        ignore.onclick = async () => {
          await api.put(`/api/gaps/${id}`, { status: 'ignored' });
          toast('已忽略', 'ok');
          await loadAll();
        };
      }
    });
  }

  /* ---------------- 补充任务 ---------------- */
  function renderTasks() {
    if (!state.tasks.length) {
      body.innerHTML = emptyState('暂无知识补充任务');
      return;
    }
    const label = { todo: tag('待处理', 'orange'), doing: tag('处理中', 'blue'), done: tag('已完成', 'green') };
    body.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th style="width:40%">任务</th><th>分类</th><th>负责人</th><th>优先级</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${state.tasks.map((t) => `
        <tr data-id="${t.id}">
          <td><div class="t-title">${esc(t.title)}</div><div class="t-sub">创建于 ${timeAgo(t.created_at)}</div></td>
          <td>${esc(t.category || '—')}</td>
          <td>${esc(t.owner || '—')}</td>
          <td>${t.priority === 'high' ? tag('高', 'red') : tag('中', 'gray')}</td>
          <td>${label[t.status] || t.status}</td>
          <td class="actions">
            <select class="select" data-act="status" style="height:28px;padding:0 8px;width:100px">
              <option value="todo" ${t.status === 'todo' ? 'selected' : ''}>待处理</option>
              <option value="doing" ${t.status === 'doing' ? 'selected' : ''}>处理中</option>
              <option value="done" ${t.status === 'done' ? 'selected' : ''}>已完成</option>
            </select>
          </td>
        </tr>`).join('')}</tbody></table></div>`;
    body.querySelectorAll('tr[data-id]').forEach((row) => {
      const id = Number(row.dataset.id);
      row.querySelector('[data-act="status"]').onchange = async (e) => {
        await api.put(`/api/tasks/${id}`, { status: e.target.value });
        toast('任务状态已更新', 'ok');
        await loadAll();
      };
    });
  }
}
