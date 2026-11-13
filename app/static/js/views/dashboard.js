/* 运营看板视图 */
import { api } from '../api.js';
import {
  esc, icon, fmtNum, fmtPct, fmtMs, lineChart, donut, barList, emptyState,
} from '../ui.js';

const KPI = [
  { key: 'pv', label: '访问次数 PV', icon: 'chat', accent: true, foot: () => '问答请求总量' },
  { key: 'uv', label: '独立提问人数 UV', icon: 'users', foot: () => '按用户去重' },
  { key: 'ku_total', label: '知识单元', icon: 'book', foot: (d) => `${d.kpi.chunk_total} 个切片` },
  { key: 'cache_hit_rate', label: 'FAQ 缓存命中率', icon: 'spark', suffix: '%', foot: () => '命中即毫秒直出' },
  { key: 'token_total', label: 'Token 消耗', icon: 'layers', foot: (d) => `输入 ${fmtNum(d.kpi.token_in)} / 输出 ${fmtNum(d.kpi.token_out)}` },
  { key: 'latency_avg', label: '平均响应时长', icon: 'clock', raw: true, foot: (d) => `P50 ${fmtMs(d.kpi.latency_p50)} · P95 ${fmtMs(d.kpi.latency_p95)}` },
];

export async function renderDashboard(el, ctx) {
  const state = { days: 7 };
  el.innerHTML = `<div class="page-head">
      <div><h2>运营看板</h2><p>平台访问、知识规模、问答质量与沉淀效果的统一视图</p></div>
      <div class="actions">
        <div class="btn-group" id="range">
          <button data-d="7" class="on">近 7 天</button>
          <button data-d="14">近 14 天</button>
          <button data-d="30">近 30 天</button>
        </div>
        <button class="btn" id="refresh">${icon('refresh')}刷新</button>
      </div>
    </div>
    <div id="dash-body"><div class="card"><div class="card-body"><div class="empty">数据加载中…</div></div></div></div>`;

  const body = el.querySelector('#dash-body');
  const load = async () => {
    const data = await api.get(`/api/dashboard/overview?days=${state.days}`);
    body.innerHTML = grid(data);
  };
  el.querySelector('#range').addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    el.querySelectorAll('#range button').forEach((b) => b.classList.toggle('on', b === btn));
    state.days = Number(btn.dataset.d);
    load();
  });
  el.querySelector('#refresh').onclick = load;
  await load();

  function grid(d) {
    if (!d.trend.length) return emptyState('暂无运营数据');
    const labels = d.trend.map((t) => t.date.slice(5));
    const kpis = KPI.map((k) => {
      let v = d.kpi[k.key];
      if (k.raw) v = fmtMs(v);
      else v = fmtNum(v);
      return `<div class="kpi ${k.accent ? 'accent' : ''}">
        <div class="k-label">${esc(k.label)}</div>
        <div class="k-value">${v}${k.suffix ? `<small>${k.suffix}</small>` : ''}</div>
        <div class="k-foot">${esc(k.foot(d))}</div>
        <div class="k-icon">${icon(k.icon, 15)}</div>
      </div>`;
    }).join('');

    const topQ = d.top_questions.map((q) => ({ label: q.question, value: q.count }));
    const kbRank = d.top_kb.length
      ? d.top_kb.map((k, i) => `<div class="rank-row">
          <span class="rank-no">${i + 1}</span>
          <div class="r-main"><b class="ellipsis">${esc(k.title)}</b>
            <span>${esc(k.category || '未分类')} · ${esc(k.format || '')}</span></div>
          <span class="r-val">${k.count} 次引用</span>
        </div>`).join('')
      : emptyState('暂无引用数据');

    const fmtDist = d.format_dist.map((f) => ({ name: (f.format || '未知').toUpperCase(), value: f.cnt }));

    return `<div class="kpi-grid">${kpis}</div>
      <div class="dash-grid">
        <div class="card">
          <div class="card-head"><h3>访问趋势</h3><span class="sub">PV / UV 按日聚合</span>
            <div class="actions legend">
              <span><i style="background:#2f5cff"></i>PV</span>
              <span><i style="background:#7a5af8"></i>UV</span>
            </div>
          </div>
          <div class="card-body">${lineChart(labels, [
            { name: 'PV', values: d.trend.map((t) => t.pv), color: '#2f5cff' },
            { name: 'UV', values: d.trend.map((t) => t.uv), color: '#7a5af8', area: false },
          ], { height: 210 })}</div>
        </div>
        <div class="card">
          <div class="card-head"><h3>知识格式分布</h3><span class="sub">${fmtNum(d.kpi.ku_total)} 个知识单元</span></div>
          <div class="card-body">${donut(fmtDist)}</div>
        </div>
      </div>
      <div class="dash-grid three">
        <div class="card">
          <div class="card-head"><h3>常见问题 TOP</h3><span class="sub">按规范化提问聚类计数</span></div>
          <div class="card-body">${topQ.length ? barList(topQ) : emptyState('暂无提问')}</div>
        </div>
        <div class="card">
          <div class="card-head"><h3>高频引用知识</h3><span class="sub">被问答引用的知识单元</span></div>
          <div class="card-body" style="padding:8px 18px">${kbRank}</div>
        </div>
        <div class="card">
          <div class="card-head"><h3>问答质量</h3><span class="sub">覆盖率与时延分布</span></div>
          <div class="card-body">
            <div class="trace-stats" style="grid-template-columns:1fr 1fr">
              <div class="trace-stat"><span>知识覆盖率</span><b>${fmtPct(d.kpi.coverage)}</b></div>
              <div class="trace-stat"><span>有效回答率</span><b>${fmtPct(d.kpi.answer_rate)}</b></div>
              <div class="trace-stat"><span>首 token 平均</span><b>${fmtMs(d.kpi.first_token_avg)}</b></div>
              <div class="trace-stat"><span>权限拦截（累计）</span><b>${fmtNum(d.kpi.denied_total)}</b></div>
            </div>
            ${lineChart(labels, [{ name: 'Token', values: d.trend.map((t) => t.tokens), color: '#06aed4' }], { height: 130, showX: false, yTicks: 2 })}
            <div class="row between mt12 small muted">
              <span>待审核 FAQ <b style="color:var(--ink)">${d.kpi.pending_candidates}</b></span>
              <span>未闭环缺口 <b style="color:var(--ink)">${d.kpi.gap_open}</b></span>
              <span>在线 FAQ <b style="color:var(--ink)">${d.kpi.faq_online}</b></span>
            </div>
          </div>
        </div>
      </div>
      <div class="card mt16"><div class="card-body row gap16 small muted">
        <b style="color:var(--ink-2)">指标口径</b>
        <span>PV = 问答请求次数</span><span>UV = 去重提问用户数</span>
        <span>Token = 输入 + 输出</span><span>覆盖率 = 有召回的提问 / 总提问</span>
        <span>缓存命中率 = FAQ 直出 / 总提问</span>
        <span style="margin-left:auto">聚合范围：${esc(d.scope)}</span>
      </div></div>`;
  }
}
