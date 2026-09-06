/* 1H Swing Screener — EMA 4/9 + MACD + DMI/ADX */
'use strict';

const $ = (s) => document.querySelector(s);
const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };

const S = {
  cfg: null,
  rows: [],
  meta: {},
  params: {},
  tab: 'ALL',
  sort: null,
  dir: -1,
  sel: null,
  poll: null,
  auto: null,
  seen: null,      // symbol|signal_at of the previous scan
  fresh: new Set(),
};

/* NSE trades 09:15-15:30 IST; the 15:15 bar closes at 15:30. */
function istNow() {
  const p = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata', weekday: 'short', hour: '2-digit',
    minute: '2-digit', hour12: false,
  }).formatToParts(new Date());
  const g = (t) => p.find(x => x.type === t).value;
  return { day: g('weekday'), mins: Number(g('hour')) * 60 + Number(g('minute')) };
}

function marketOpen() {
  const { day, mins } = istNow();
  if (['Sat', 'Sun'].includes(day)) return false;
  return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 40;
}

function toast(title, body) {
  const t = el('div', 'toast');
  t.innerHTML = `<b>${title}</b><span>${body}</span>`;
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), 9000);
}

const BUCKETS = [
  { id: 'STRONG BUY', label: 'Strong buy', cls: 'strong' },
  { id: 'BUY', label: 'Buy', cls: 'buy' },
  { id: 'DIVERGENCE', label: 'Divergence', cls: 'div' },
  { id: 'WATCH', label: 'Watch', cls: 'watch' },
  { id: 'ALL', label: 'All scanned', cls: 'none' },
];

const CLS = { 'STRONG BUY': 'strong', 'BUY': 'buy', 'DIVERGENCE': 'div', 'WATCH': 'watch', 'NONE': 'none' };

const NOW = 'State of the last closed bar — not the bar the signal fired on.';
const COLS = [
  { k: 'symbol', t: 'Symbol', l: true },
  { k: 'ltp', t: 'LTP' },
  { k: 'day_chg_pct', t: 'Day %' },
  { k: 'bucket', t: 'Signal', l: true },
  { k: 'bars_ago', t: 'Bars ago' },
  { k: 'signal_at', t: 'Triggered', l: true, tip: 'Open of the 1-hour bar the signal fired on (IST).' },
  { k: 'emaBullTrend', t: 'EMA 4>9', tip: NOW, now: true },
  { k: 'priceAboveEMA', t: 'Px>EMA 9', tip: NOW, now: true },
  { k: 'macdAboveSignal', t: 'MACD>Sig', tip: NOW, now: true },
  { k: 'adx', t: 'ADX', tip: NOW, now: true },
  { k: 'di_spread', t: '+DI −DI', tip: NOW, now: true },
  { k: 'bullishDivergence', t: 'Div', tip: NOW, now: true },
  { k: 'trend', t: 'Daily trend', l: true,
    tip: 'Daily chart: above/below the 50 and 200 DMA and 6-month return. Context only — the signal does not use it.' },
  { k: 'score', t: 'Score' },
  { k: 'turnover_cr', t: 'Turnover' },
  { k: 'spark', t: '60 bars', nosort: true },
];

const num = (v, d = 2) => (v == null || Number.isNaN(v)) ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v) => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
const tick = (b) => b ? '<span class="yes chk">✓</span>' : '<span class="no chk">·</span>';
const fmtTime = (iso) => {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
};

/* ------------------------------------------------------------- bootstrap */
async function boot() {
  S.cfg = await (await fetch('/api/config')).json();
  S.params = { ...S.cfg.params };

  const u = $('#universe');
  S.cfg.universes.forEach(v => {
    const o = el('option', null, `${v.label} · ${v.count}`);
    o.value = v.id; o.title = v.note; u.appendChild(o);
  });

  buildParamForm();
  wire();

  const st = await (await fetch('/api/status')).json();
  if (st.has_results) {
    const r = await (await fetch('/api/results')).json();
    S.rows = r.rows; S.meta = r.meta;
    if (S.meta.lookback) $('#lookback').value = String(S.meta.lookback);
    if (S.meta.universe) $('#universe').value = S.meta.universe;
    render();
    const deep = decodeURIComponent(location.hash.slice(1)).toUpperCase();
    if (deep && S.rows.some(r => r.symbol === deep)) openDrawer(deep);
  } else {
    renderTabs(); showEmpty('No scan yet — hit <b>Run scan</b>.');
  }
  if (st.state === 'running') { setBusy(true); startPoll(); }
}

function wire() {
  $('#scan-btn').onclick = runScan;
  $('#search').oninput = render;
  ['minprice', 'minturn', 'minadx', 'trend'].forEach(id => {
    const saved = localStorage.getItem('flt_' + id);
    if (saved !== null && $('#' + id).querySelector(`option[value="${saved}"]`)) $('#' + id).value = saved;
    $('#' + id).onchange = () => { localStorage.setItem('flt_' + id, $('#' + id).value); render(); };
  });
  $('#lookback').onchange = () => { if (S.rows.length) status('Signal window changed — rescan to apply', true); };
  $('#auto').onchange = setupAuto;
  $('#drawer-close').onclick = closeDrawer;
  $('#scrim').onclick = closeDrawer;
  $('#csv-btn').onclick = exportCsv;
  $('#settings-btn').onclick = () => { $('#modal').hidden = false; };
  $('#modal-close').onclick = () => { $('#modal').hidden = true; };
  $('#reset-params').onclick = () => { S.params = { ...S.cfg.defaults }; buildParamForm(); };
  $('#apply-params').onclick = () => { readParamForm(); $('#modal').hidden = true; runScan(); };
  $('#theme-btn').onclick = () => {
    const cur = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = cur;
    localStorage.setItem('theme', cur);
    if (S.sel) drawAll();
  };
  const saved = localStorage.getItem('theme');
  if (saved) document.documentElement.dataset.theme = saved;

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeDrawer(); $('#modal').hidden = true; }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && S.sel) {
      const list = visible();
      const i = list.findIndex(r => r.symbol === S.sel);
      const n = list[i + (e.key === 'ArrowDown' ? 1 : -1)];
      if (n) { e.preventDefault(); openDrawer(n.symbol); }
    }
  });
}

/* ------------------------------------------------------------------ scan */
function setBusy(on) {
  const b = $('#scan-btn');
  b.disabled = on;
  b.querySelector('.spin').hidden = !on;
  b.querySelector('.label').textContent = on ? 'Scanning…' : 'Run scan';
}

function status(main, warn) {
  $('#status-main').innerHTML = main;
  $('#status-main').style.color = warn ? 'var(--watch)' : '';
}

async function runScan() {
  readParamForm();
  setBusy(true);
  status('Starting…');
  $('#progress').querySelector('.bar').style.width = '0%';
  const body = {
    universe: $('#universe').value,
    lookback: Number($('#lookback').value),
    params: S.params,
  };
  const r = await fetch('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) { setBusy(false); status('Scan already running', true); return; }
  startPoll();
}

function startPoll() {
  clearInterval(S.poll);
  S.poll = setInterval(async () => {
    const st = await (await fetch('/api/status')).json();
    const pctDone = st.total ? (st.done / st.total * 100) : 0;
    $('#progress').querySelector('.bar').style.width = pctDone + '%';
    if (st.state === 'running') {
      status(`Scanning ${st.done} / ${st.total || '…'}`);
      $('#status-sub').textContent = st.failed ? `${st.failed} skipped` : '';
      return;
    }
    clearInterval(S.poll); setBusy(false);
    $('#progress').querySelector('.bar').style.width = '0%';
    if (st.state === 'error') { status('Scan failed: ' + st.message, true); return; }
    const res = await (await fetch('/api/results')).json();
    S.rows = res.rows; S.meta = res.meta;
    detectFresh();
    render();
  }, 700);
}

/* --------------------------------------------------- auto rescan + alerts */
function setupAuto() {
  clearInterval(S.auto); S.auto = null;
  const mins = Number($('#auto').value);
  if (!mins) { toast('Auto rescan off', 'Scans only run when you ask'); return; }
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  S.auto = setInterval(() => {
    if ($('#scan-btn').disabled) return;                 // one already running
    if (!marketOpen()) { $('#status-sub').textContent = 'market closed — auto rescan paused'; return; }
    runScan();
  }, mins * 60000);
  toast('Auto rescan on', `Every ${mins} min while NSE is open`);
}

function detectFresh() {
  const key = r => r.symbol + '|' + r.signal_at;
  const live = new Set(S.rows.filter(r => r.signal).map(key));
  if (S.seen) {
    S.fresh = new Set();
    const added = [];
    live.forEach(k => {
      if (!S.seen.has(k)) { const s = k.split('|')[0]; S.fresh.add(s); added.push(s); }
    });
    if (added.length) {
      const list = added.slice(0, 6).join(', ') + (added.length > 6 ? ` +${added.length - 6} more` : '');
      const title = `${added.length} new 1H signal${added.length > 1 ? 's' : ''}`;
      toast(title, list);
      if ('Notification' in window && Notification.permission === 'granted') {
        try { new Notification(title, { body: list }); } catch (e) { /* ignore */ }
      }
    }
  }
  S.seen = live;
}

/* ---------------------------------------------------------------- filters */
/* The penny-stock floor. Kept separate from the tab and the search box so
   the bucket counts describe the same set the table is showing. */
function baseRows() {
  const mp = Number($('#minprice').value);
  const mt = Number($('#minturn').value);
  const ma = Number($('#minadx').value);
  const tr = $('#trend').value;
  return S.rows.filter(r => {
    if ((r.ltp || 0) < mp || (r.turnover_cr || 0) < mt || (r.adx || 0) < ma) return false;
    if (tr === 'UP' && r.trend !== 'UP') return false;
    if (tr === 'UPMIX' && !(r.trend === 'UP' || r.trend === 'MIXED')) return false;
    if (tr === 'DOWN' && r.trend !== 'DOWN') return false;
    return true;
  });
}

function visible() {
  const q = $('#search').value.trim().toLowerCase();
  let rows = baseRows().filter(r => {
    if (S.tab !== 'ALL' && r.bucket !== S.tab) return false;
    if (q && !(r.symbol.toLowerCase().includes(q) || (r.name || '').toLowerCase().includes(q))) return false;
    return true;
  });
  if (S.sort) {
    const k = S.sort;
    rows = rows.slice().sort((a, b) => {
      let x = a[k], y = b[k];
      if (k === 'bucket') { const o = { 'STRONG BUY': 0, 'BUY': 1, 'DIVERGENCE': 2, 'WATCH': 3, 'NONE': 4 }; x = o[x]; y = o[y]; }
      if (typeof x === 'boolean') { x = x ? 1 : 0; y = y ? 1 : 0; }
      if (x == null) x = -Infinity; if (y == null) y = -Infinity;
      if (typeof x === 'string') return S.dir * x.localeCompare(y);
      return S.dir * (x - y);
    });
  }
  return rows;
}

/* ----------------------------------------------------------------- render */
function renderTabs() {
  const t = $('#tabs'); t.innerHTML = '';
  const base = S.rows.length ? baseRows() : [];
  BUCKETS.forEach(b => {
    const n = b.id === 'ALL' ? base.length : base.filter(r => r.bucket === b.id).length;
    const btn = el('button', 'tab' + (S.tab === b.id ? ' on' : ''));
    btn.innerHTML = `<i class="dot ${b.cls}"></i> ${b.label} <span class="n">${n}</span>`;
    btn.onclick = () => { S.tab = b.id; render(); };
    t.appendChild(btn);
  });
}

function showEmpty(html) {
  $('#empty').hidden = false;
  $('#empty').innerHTML = html;
  $('#body').innerHTML = '';
  $('#head').innerHTML = '';
}

function render() {
  renderTabs();
  const rows = visible();
  const m = S.meta || {};
  if (m.finished_at) {
    status(`${m.scanned} scanned · ${m.universe === 'fno' ? 'F&O' : 'NSE'} · window ${m.lookback} bar${m.lookback > 1 ? 's' : ''}`);
    const skips = (m.skips || []).map(([why, n]) => `${n} ${why}`).join(', ');
    $('#status-sub').textContent = `last bar ${fmtTime(m.bar_time)} · ${m.seconds}s`
      + (m.failed ? ` · ${m.failed} skipped${skips ? ' (' + skips + ')' : ''}` : '');
  }
  const hidden = S.rows.length - baseRows().length;
  $('#count').textContent = (rows.length > 500 ? `${rows.length} matched` : `${rows.length} shown`)
    + (hidden ? ` · ${hidden} below floor` : '');

  if (!rows.length) {
    showEmpty(S.rows.length ? 'Nothing matches this filter.' : 'No scan yet — hit <b>Run scan</b>.');
    return;
  }
  $('#empty').hidden = true;

  const head = $('#head'); head.innerHTML = '';
  COLS.forEach(c => {
    const th = el('th', (c.l ? 'l' : '') + (c.now ? ' nowcol' : ''));
    if (c.tip) th.title = c.tip;
    th.innerHTML = c.t + (S.sort === c.k ? ` <span class="caret">${S.dir > 0 ? '▲' : '▼'}</span>` : '');
    if (!c.nosort) th.onclick = () => {
      if (S.sort === c.k) S.dir = -S.dir; else { S.sort = c.k; S.dir = -1; }
      render();
    };
    head.appendChild(th);
  });

  const body = $('#body'); body.innerHTML = '';
  const frag = document.createDocumentFragment();
  const MAX_ROWS = 500;
  const shown = rows.slice(0, MAX_ROWS);
  shown.forEach(r => {
    const tr = el('tr');
    tr.className = (r.symbol === S.sel ? 'sel ' : '') + (S.fresh.has(r.symbol) ? 'fresh' : '');
    tr.onclick = () => openDrawer(r.symbol);
    tr.innerHTML = `
      <td class="l"><span class="sym">${r.symbol}</span><span class="sub">${r.name || ''}</span></td>
      <td class="num">${num(r.ltp)}</td>
      <td class="num ${(r.day_chg_pct || 0) >= 0 ? 'up' : 'dn'}">${pct(r.day_chg_pct)}</td>
      <td class="l">${badge(r)}</td>
      <td class="num">${r.bars_ago == null ? '—' : r.bars_ago}</td>
      <td class="l num trig">${r.signal_at ? fmtTime(r.signal_at) : '<span class="no">—</span>'}</td>
      <td>${tick(r.emaBullTrend)}</td>
      <td>${tick(r.priceAboveEMA)}</td>
      <td>${tick(r.macdAboveSignal)}</td>
      <td class="num ${r.adxOK ? 'up' : ''}">${num(r.adx, 1)}</td>
      <td class="num ${r.diBull ? 'up' : 'dn'}">${num(r.di_spread, 1)}</td>
      <td>${tick(r.bullishDivergence)}</td>
      <td class="l">${trendCell(r)}</td>
      <td class="num">${num(r.score, 0)}<span class="meter"><i style="width:${r.score}%"></i></span></td>
      <td class="num">${r.turnover_cr == null ? '—' : '₹' + num(r.turnover_cr, r.turnover_cr < 10 ? 1 : 0) + ' Cr'}</td>
      <td>${sparkSvg(r.spark)}</td>`;
    frag.appendChild(tr);
  });
  body.appendChild(frag);
  if (rows.length > MAX_ROWS) {
    const tr = el('tr');
    const td = el('td');
    td.colSpan = COLS.length;
    td.className = 'more';
    td.textContent = `Showing the top ${MAX_ROWS} of ${rows.length} — sort or filter to narrow it. Export CSV includes all ${rows.length}.`;
    tr.appendChild(td); body.appendChild(tr);
  }
}

/* A signal fires on one bar; the tick columns describe the latest bar. When
   those have since diverged, say so on the badge rather than leaving a row of
   dots next to a STRONG BUY looking like a contradiction.

   "Setup" is the script's own `strongTrend`: emaBullTrend and macdAboveSignal
   and adx > minimumADX and plusDI > minusDI. Note that Pine leaves
   `priceAboveEMA` out of it even though the entry required it, so a setup can
   read as holding while price has slipped back under EMA 9 - which is worth
   spelling out rather than hiding behind the word "intact". */
function decayed(r) {
  return !!r.signal && r.bars_ago > 0 && !r.strongTrend;
}

function setupState(r) {
  const min = S.params.minimumADX;
  const checks = [
    ['EMA 4 > EMA 9', r.emaBullTrend],
    ['MACD > signal', r.macdAboveSignal],
    [`ADX ${num(r.adx, 1)} > ${num(min, 0)}`, r.adxOK],
    ['+DI > −DI', r.diBull],
  ];
  const tip = checks.map(([k, v]) => `${v ? '✓' : '✗'} ${k}`).join(' · ');
  if (!r.strongTrend) return { label: 'broken down', cls: 'dn', tip };
  return r.priceAboveEMA
    ? { label: 'still intact', cls: 'up', tip }
    : { label: 'intact, but price under EMA 9', cls: 'watch-txt', tip: tip + ' · ✗ price > EMA 9' };
}

function badge(r) {
  if (r.bucket === 'NONE') return '<span class="badge none">—</span>';
  const gone = decayed(r);
  const st = setupState(r);
  const tip = (gone ? `Fired ${r.bars_ago} bars ago. ` : '') + `Setup ${st.label} — ${st.tip}`;
  return `<span class="badge ${CLS[r.bucket]}${gone ? ' faded' : ''}" title="${tip}">`
    + `${r.bucket}${gone ? ' ↓' : ''}</span>`;
}

const TREND = { UP: ['up', '▲ Uptrend'], MIXED: ['dim', '– Mixed'], DOWN: ['dn', '▼ Downtrend'] };

function trendCell(r) {
  const t = TREND[r.trend];
  if (!t) return '<span class="no">—</span>';
  const tip = `${r.above_sma50 ? 'above' : 'below'} 50 DMA · ${r.above_sma200 ? 'above' : 'below'} 200 DMA`
    + ` · 6m ${r.ret_6m == null ? '—' : pct(r.ret_6m)} · ${r.off_52w_high == null ? '' : pct(r.off_52w_high) + ' off 52w high'}`;
  return `<span class="${t[0]}" title="${tip}">${t[1]}</span>`;
}

function sparkSvg(vals) {
  if (!vals || vals.length < 2) return '';
  const w = 72, h = 20, lo = Math.min(...vals), hi = Math.max(...vals), rng = (hi - lo) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - (v - lo) / rng * h).toFixed(1)}`).join(' ');
  const up = vals[vals.length - 1] >= vals[0];
  const col = up ? 'var(--strong)' : 'var(--down)';
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" stroke="${col}" stroke-width="1.3"/></svg>`;
}

/* ------------------------------------------------------------------ drawer */
let CHART = null, HOVER = null;

async function openDrawer(symbol) {
  const r = S.rows.find(x => x.symbol === symbol);
  if (!r) return;
  S.sel = symbol;
  history.replaceState(null, '', '#' + symbol);
  render();
  $('#drawer').hidden = false; $('#scrim').hidden = false;
  $('#drawer').scrollTop = 0;

  $('#d-symbol').textContent = r.symbol;
  $('#d-name').textContent = r.name || '';
  $('#d-ltp').textContent = num(r.ltp);
  const chg = $('#d-chg');
  chg.textContent = `${pct(r.day_chg_pct)} today · ${pct(r.chg_pct)} last bar`;
  chg.className = (r.day_chg_pct || 0) >= 0 ? 'up' : 'dn';

  const sig = $('#d-signal');
  const st = setupState(r);
  if (r.signal) {
    const move = r.signal_price ? (r.ltp / r.signal_price - 1) * 100 : null;
    sig.innerHTML = `
      <span class="badge ${CLS[r.bucket]} big">${r.signal}</span>
      <div class="kv"><span>Fired</span><b>${fmtTime(r.signal_at)}</b></div>
      <div class="kv"><span>Bars ago</span><b>${r.bars_ago}</b></div>
      <div class="kv"><span>Signal price</span><b>${num(r.signal_price)}</b></div>
      <div class="kv"><span>Since signal</span><b class="${move >= 0 ? 'up' : 'dn'}">${pct(move)}</b></div>
      <div class="kv" title="${st.tip}"><span>Setup now</span><b class="${st.cls}">${st.label}</b></div>`;
  } else {
    sig.innerHTML = `<span class="badge ${CLS[r.bucket]} big">${r.bucket === 'WATCH' ? 'IN TREND — NO ENTRY YET' : 'NO SIGNAL'}</span>
      <div class="kv" title="${st.tip}"><span>Setup now</span><b class="${st.cls}">${st.label}</b></div>
      <div class="kv"><span>Trend age</span><b>${r.trend_bars == null ? '—' : r.trend_bars + ' bars'}</b></div>
      <div class="kv"><span>Last divergence</span><b>${r.div_bars_ago == null ? '—' : r.div_bars_ago + ' bars ago'}</b></div>`;
  }

  const min = S.params.minimumADX;
  $('#d-bartime').textContent = `— last closed bar, ${fmtTime(r.bar_time)}`;
  const yn = (b) => b ? '<span class="yes">YES</span>' : '<span class="no">NO</span>';
  $('#d-info').innerHTML = `
    <tr><td>EMA 4 &gt; EMA 9</td><td>${yn(r.emaBullTrend)}</td></tr>
    <tr><td>Price &gt; EMA 9</td><td>${yn(r.priceAboveEMA)}</td></tr>
    <tr><td>MACD &gt; Signal</td><td>${yn(r.macdAboveSignal)}</td></tr>
    <tr><td>ADX</td><td class="${r.adxOK ? 'yes' : 'dn'}">${num(r.adx, 1)}</td></tr>
    <tr><td>+DI &gt; −DI</td><td>${yn(r.diBull)}</td></tr>
    <tr><td>MACD Divergence</td><td>${r.bullishDivergence ? '<span style="color:var(--div)">YES</span>' : '<span class="no">NO</span>'}</td></tr>
    <tr><td>SIGNAL</td><td class="${r.status === 'WAIT' ? 'no' : 'yes'}">${r.status}</td></tr>`;

  $('#d-nums').innerHTML = [
    ['EMA 4', num(r.ema4)], ['EMA 9', num(r.ema9)],
    ['EMA gap', pct(r.ema_gap_pct)], ['Price vs EMA 9', pct(r.px_vs_ema9_pct)],
    ['MACD', num(r.macdLine, 3)], ['Signal', num(r.signalLine, 3)],
    ['Histogram', num(r.macdHistogram, 3)],
    ['+DI', num(r.plusDI, 1)], ['−DI', num(r.minusDI, 1)],
    ['ADX', num(r.adx, 1) + (r.adx_rising ? ' ↑' : ' ↓')],
    ['ADX floor', num(min, 1)],
    ['Trend age', r.trend_bars == null ? '—' : r.trend_bars + ' bars'],
    ['Bar volume', (r.volume || 0).toLocaleString('en-IN')],
    ['Turnover/day', '₹' + num(r.turnover_cr, r.turnover_cr < 10 ? 1 : 0) + ' Cr'],
    ['Daily trend', r.trend || '—'],
    ['vs 50 DMA', r.above_sma50 == null ? '—' : (r.above_sma50 ? 'above' : 'below')],
    ['vs 200 DMA', r.above_sma200 == null ? '—' : (r.above_sma200 ? 'above' : 'below')],
    ['3-month', pct(r.ret_3m)], ['6-month', pct(r.ret_6m)],
    ['Off 52w high', pct(r.off_52w_high)],
  ].map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('');

  CHART = null; drawAll();
  try {
    CHART = await (await fetch('/api/chart?key=' + encodeURIComponent(r.instrument_key))).json();
  } catch (e) { CHART = null; }
  drawAll();
}

function closeDrawer() {
  $('#drawer').hidden = true; $('#scrim').hidden = true;
  history.replaceState(null, '', location.pathname);
  S.sel = null; CHART = null; render();
}

/* ------------------------------------------------------------------ charts */
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

function setup(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = Number(canvas.getAttribute('height'));
  canvas.width = w * dpr; canvas.height = h * dpr;
  canvas.style.height = h + 'px';
  const g = canvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  return { g, w, h };
}

const PAD = { l: 6, r: 52, t: 8, b: 8 };

function scaleY(h, lo, hi) {
  const rng = (hi - lo) || 1;
  return (v) => PAD.t + (hi - v) / rng * (h - PAD.t - PAD.b);
}

function axis(g, w, h, lo, hi, y, fmt) {
  g.font = '10px ui-monospace, monospace';
  g.fillStyle = css('--faint'); g.strokeStyle = css('--line'); g.lineWidth = 1;
  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const v = lo + (hi - lo) * i / steps, yy = Math.round(y(v)) + .5;
    g.beginPath(); g.moveTo(PAD.l, yy); g.lineTo(w - PAD.r, yy); g.stroke();
    g.fillText(fmt ? fmt(v) : v.toFixed(2), w - PAD.r + 6, yy + 3);
  }
}

function drawAll() {
  drawPrice(); drawMacd(); drawAdx();
}

function xs(w, n) {
  const span = w - PAD.l - PAD.r;
  const step = span / Math.max(n, 1);
  return { step, at: (i) => PAD.l + step * (i + .5) };
}

function drawPrice() {
  const c = $('#c-price'); if (!c.clientWidth) return;
  const { g, w, h } = setup(c);
  if (!CHART) { g.fillStyle = css('--faint'); g.font = '12px sans-serif'; g.fillText('loading…', 12, 22); return; }
  const d = CHART, n = d.c.length;
  const lo = Math.min(...d.l), hi = Math.max(...d.h);
  const pad = (hi - lo) * 0.06;
  const y = scaleY(h, lo - pad, hi + pad);
  axis(g, w, h, lo - pad, hi + pad, y);
  const { step, at } = xs(w, n);
  const bw = Math.max(1, Math.min(step * 0.62, 9));

  for (let i = 0; i < n; i++) {
    const up = d.c[i] >= d.o[i];
    g.strokeStyle = g.fillStyle = up ? css('--strong') : css('--down');
    const x = at(i);
    g.beginPath(); g.moveTo(x, y(d.h[i])); g.lineTo(x, y(d.l[i])); g.lineWidth = 1; g.stroke();
    const yo = y(d.o[i]), yc = y(d.c[i]);
    g.fillRect(x - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo)));
  }
  line(g, d.ema4, y, at, '#2ecc71', 1.5);
  line(g, d.ema9, y, at, '#f5a623', 1.5);

  const mark = (idx, col, label) => idx.forEach(i => {
    const x = at(i), yy = y(d.l[i]) + 12;
    g.fillStyle = col; g.beginPath();
    g.moveTo(x, yy - 7); g.lineTo(x - 5, yy + 2); g.lineTo(x + 5, yy + 2); g.closePath(); g.fill();
    g.font = 'bold 9px sans-serif'; g.fillText(label, x - 8, yy + 12);
  });
  mark(d.buy, css('--buy'), 'B');
  mark(d.strong, css('--strong'), 'S');
  mark(d.div, css('--div'), 'D');

  crosshair(g, w, h, at, n);
}

function drawMacd() {
  const c = $('#c-macd'); if (!c.clientWidth || !CHART) { if (c.clientWidth) setup(c); return; }
  const { g, w, h } = setup(c);
  const d = CHART, n = d.c.length;
  const vals = [...d.macd, ...d.signal, ...d.hist].filter(v => v != null);
  const lo = Math.min(...vals, 0), hi = Math.max(...vals, 0);
  const y = scaleY(h, lo, hi);
  axis(g, w, h, lo, hi, y, v => v.toFixed(1));
  const { step, at } = xs(w, n);
  const bw = Math.max(1, Math.min(step * 0.62, 9));
  for (let i = 0; i < n; i++) {
    if (d.hist[i] == null) continue;
    const pos = d.hist[i] >= 0;
    const grow = i > 0 && d.hist[i - 1] != null && Math.abs(d.hist[i]) >= Math.abs(d.hist[i - 1]);
    g.globalAlpha = grow ? 1 : .45;
    g.fillStyle = pos ? css('--strong') : css('--down');
    const y0 = y(0), y1 = y(d.hist[i]);
    g.fillRect(at(i) - bw / 2, Math.min(y0, y1), bw, Math.max(1, Math.abs(y1 - y0)));
  }
  g.globalAlpha = 1;
  line(g, d.macd, y, at, '#3aa0ff', 1.4);
  line(g, d.signal, y, at, '#ff8f3a', 1.4);
  crosshair(g, w, h, at, n);
}

function drawAdx() {
  const c = $('#c-adx'); if (!c.clientWidth || !CHART) { if (c.clientWidth) setup(c); return; }
  const { g, w, h } = setup(c);
  const d = CHART, n = d.c.length;
  const vals = [...d.adx, ...d.plusDI, ...d.minusDI].filter(v => v != null);
  const lo = 0, hi = Math.max(...vals, d.minADX + 5);
  const y = scaleY(h, lo, hi);
  axis(g, w, h, lo, hi, y, v => v.toFixed(0));
  const { at } = xs(w, n);
  g.setLineDash([4, 4]); g.strokeStyle = css('--watch'); g.lineWidth = 1;
  g.beginPath(); g.moveTo(PAD.l, y(d.minADX)); g.lineTo(w - PAD.r, y(d.minADX)); g.stroke();
  g.setLineDash([]);
  line(g, d.plusDI, y, at, '#2ecc71', 1.2);
  line(g, d.minusDI, y, at, '#ff5c5c', 1.2);
  line(g, d.adx, y, at, css('--text'), 1.7);
  crosshair(g, w, h, at, n);
}

function line(g, arr, y, at, col, lw) {
  g.strokeStyle = col; g.lineWidth = lw; g.beginPath();
  let started = false;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] == null) { started = false; continue; }
    const x = at(i), yy = y(arr[i]);
    if (!started) { g.moveTo(x, yy); started = true; } else g.lineTo(x, yy);
  }
  g.stroke();
}

function crosshair(g, w, h, at, n) {
  if (HOVER == null || HOVER < 0 || HOVER >= n) return;
  g.strokeStyle = css('--dim'); g.globalAlpha = .5; g.lineWidth = 1;
  g.setLineDash([3, 3]);
  const x = Math.round(at(HOVER)) + .5;
  g.beginPath(); g.moveTo(x, PAD.t); g.lineTo(x, h - PAD.b); g.stroke();
  g.setLineDash([]); g.globalAlpha = 1;
}

['#c-price', '#c-macd', '#c-adx'].forEach(sel => {
  document.addEventListener('mousemove', e => {
    const c = $(sel); if (!c || !CHART) return;
    const rect = c.getBoundingClientRect();
    if (e.clientY < rect.top || e.clientY > rect.bottom || e.clientX < rect.left || e.clientX > rect.right) return;
    const n = CHART.c.length;
    const span = rect.width - PAD.l - PAD.r;
    const i = Math.round((e.clientX - rect.left - PAD.l) / span * n - .5);
    if (i === HOVER) return;
    HOVER = i;
    const d = CHART;
    if (i >= 0 && i < n) {
      $('#d-hover').textContent =
        `${fmtTime(d.t[i])}  O ${num(d.o[i])}  H ${num(d.h[i])}  L ${num(d.l[i])}  C ${num(d.c[i])}  ·  ADX ${num(d.adx[i], 1)}  MACD ${num(d.macd[i], 2)}`;
    }
    drawAll();
  });
});

window.addEventListener('resize', () => { if (CHART) drawAll(); });

/* ------------------------------------------------------------- param form */
const GROUPS = [
  ['1. EMA Trend & Entry', [
    ['fastEmaLength', 'Fast EMA', 'int'], ['slowEmaLength', 'Slow EMA', 'int'],
    ['requirePriceAboveEMA', 'Require Price Above EMA 9', 'bool'],
    ['useEMACross', 'Use EMA 4/9 Cross', 'bool']]],
  ['2. MACD Confirmation', [
    ['macdFast', 'MACD Fast', 'int'], ['macdSlow', 'MACD Slow', 'int'],
    ['macdSignalLength', 'MACD Signal', 'int'],
    ['macdConfirmationMode', 'MACD Confirmation', 'opt']]],
  ['3. MACD Bullish Divergence', [
    ['useDivergence', 'Use Bullish MACD Divergence', 'bool'],
    ['divergencePivotLength', 'Pivot Length', 'int'],
    ['divergenceLookback', 'Maximum Bars Between Lows', 'int'],
    ['requirePriceHigherLow', 'Price Must Make Higher Low', 'bool'],
    ['requireMACDLowerLow', 'MACD Must Make Lower Low', 'bool']]],
  ['4. DMI / ADX Confirmation', [
    ['dmiLength', 'DMI Length', 'int'], ['adxSmoothing', 'ADX Smoothing', 'int'],
    ['minimumADX', 'Minimum ADX', 'float'],
    ['requireDIConfirmation', 'Require +DI > −DI', 'bool']]],
  ['5. Signal Logic', [
    ['signalMode', 'Primary Entry', 'opt'],
    ['requireADX', 'Require ADX Confirmation', 'bool'],
    ['requireTrend', 'Require Uptrend', 'bool'],
    ['confirmOnClose', 'Confirm Signal Only On Candle Close', 'bool']]],
];

function buildParamForm() {
  const box = $('#param-form'); box.innerHTML = '';
  GROUPS.forEach(([title, items]) => {
    const gd = el('div', 'pgroup');
    gd.appendChild(el('h4', null, title));
    items.forEach(([key, label, kind]) => {
      const row = el('div', 'prow');
      row.appendChild(el('label', null, label));
      let input;
      if (kind === 'bool') {
        input = el('input'); input.type = 'checkbox'; input.checked = !!S.params[key];
      } else if (kind === 'opt') {
        input = el('select');
        (S.cfg.options[key] || []).forEach(o => {
          const op = el('option', null, o); op.value = o; input.appendChild(op);
        });
        input.value = S.params[key];
      } else {
        input = el('input'); input.type = 'number';
        input.step = kind === 'float' ? '0.5' : '1';
        input.value = S.params[key];
      }
      input.dataset.key = key; input.dataset.kind = kind;
      row.appendChild(input);
      gd.appendChild(row);
    });
    box.appendChild(gd);
  });
}

function readParamForm() {
  $('#param-form').querySelectorAll('[data-key]').forEach(i => {
    const k = i.dataset.key, kind = i.dataset.kind;
    if (kind === 'bool') S.params[k] = i.checked;
    else if (kind === 'opt') S.params[k] = i.value;
    else if (kind === 'float') S.params[k] = parseFloat(i.value);
    else S.params[k] = parseInt(i.value, 10);
  });
}

/* --------------------------------------------------------------------- csv */
function exportCsv() {
  const rows = visible();
  if (!rows.length) return;
  const keys = ['symbol', 'name', 'bucket', 'status', 'signal', 'bars_ago', 'signal_at', 'signal_price',
    'ltp', 'day_chg_pct', 'adx', 'plusDI', 'minusDI', 'ema4', 'ema9', 'macdLine', 'signalLine',
    'macdHistogram', 'emaBullTrend', 'priceAboveEMA', 'macdAboveSignal', 'bullishDivergence',
    'turnover_cr', 'score', 'trend', 'above_sma50', 'above_sma200', 'ret_3m',
    'ret_6m', 'off_52w_high', 'bar_time'];
  const esc = v => v == null ? '' : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
  const csv = [keys.join(','), ...rows.map(r => keys.map(k => esc(r[k])).join(','))].join('\n');
  const a = el('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = `screener_${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

boot();
