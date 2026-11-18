'use strict';
/* CampaignFlow single-page UI. Vanilla JS, no build step.
   Rule: every dynamic string that reaches innerHTML goes through esc(). */

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const S = { user: null, channels: [], clients: [], cleanup: [], theme: 'light',
  cf: { page: 1, q: '', status: '', client: '', channel: '', sort: 'created_at', dir: 'desc' } };

const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
  mega: '<path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
  brief: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', search: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>', download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  out: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01"/>',
  refresh: '<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  play: '<path d="M5 3l14 9-14 9V3z"/>', menu: '<path d="M3 12h18M3 6h18M3 18h18"/>', check: '<path d="M20 6L9 17l-5-5"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
  click: '<path d="M9 9l5 12 1.8-5.2L21 14zM7.2 2.2L8 5.1M2.2 7.2l2.9.8M12.1 5.1l2.1-2.1M5.1 12.1L3 14.2"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  dollar: '<path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  pct: '<path d="M19 5L5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  bolt: '<path d="M13 2L3 14h9l-1 8 10-12h-9z"/>',
};
const icon = (n) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ''}</svg>`;

const can = (p) => !!S.user && S.user.permissions.includes(p);
const isViewer = () => S.user && S.user.role === 'client_viewer';
const money = (c, compact = false) => {
  if (c == null) return '–';
  const v = c / 100;
  if (compact && Math.abs(v) >= 1000) return '$' + (Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : (v / 1000).toFixed(v >= 1e4 ? 0 : 1) + 'k');
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 });
};
const int = (n) => (n == null ? '–' : Number(n).toLocaleString('en-US'));
const compact = (n) => (n == null ? '–' : Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(n));
const pct = (n, d = 1) => (n == null ? '–' : `${Number(n).toFixed(d)}%`);
const dollarsToCents = (v) => Math.round(parseFloat(String(v).replace(/[$,\s]/g, '')) * 100);
const centsToInput = (c) => (c / 100).toFixed(2);
const todayISO = () => new Date().toISOString().slice(0, 10);
const addDays = (iso, n) => { const d = new Date(iso + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const fmtDate = (iso, y = false) => iso ? new Date(iso + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(y ? { year: 'numeric' } : {}), timeZone: 'UTC' }) : '–';
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '–';
const ago = (iso) => {
  if (!iso) return '–';
  const s = Math.round((new Date(iso) - Date.now()) / 1000), a = Math.abs(s);
  const t = a < 60 ? [a, 'second'] : a < 3600 ? [Math.round(a / 60), 'minute'] : a < 86400 ? [Math.round(a / 3600), 'hour'] : [Math.round(a / 86400), 'day'];
  const label = `${t[0]} ${t[1]}${t[0] === 1 ? '' : 's'}`;
  return s > 0 ? `in ${label}` : `${label} ago`;
};
const cap = (s) => String(s).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const qs = (o) => Object.entries(o).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

const pill = (status) => `<span class="pill s-${esc(status)}">${esc(cap(status))}</span>`;
const ALERTS = {
  budget_80: ['80%+ of budget used', 'warn'], ending_soon: ['Ends within 3 days', 'info'],
  overpacing: ['Spending ahead of schedule', 'bad'], underpacing: ['Spending behind schedule', 'warn'],
};
const alertTags = (a) => (a || []).map((k) => `<span class="tag ${ALERTS[k]?.[1] || ''}">${esc(ALERTS[k]?.[0] || k)}</span>`).join('');
const budgetBar = (c) => {
  const p = Math.min(100, c.budget_used_pct);
  const live = ['active', 'paused', 'scheduled'].includes(c.status);   // a finished campaign using its whole budget is normal
  const cls = !live ? '' : c.budget_used_pct >= 90 ? 'bad' : c.budget_used_pct >= 80 ? 'warn' : '';
  const mark = ['active', 'paused', 'scheduled'].includes(c.status) && c.time_elapsed_pct > 0 && c.time_elapsed_pct < 100
    ? `<span class="mark" style="left:${c.time_elapsed_pct}%" title="${c.time_elapsed_pct}% of flight elapsed"></span>` : '';
  return `<div class="track ${cls}" role="progressbar" aria-valuenow="${p}" aria-valuemin="0" aria-valuemax="100"><i style="width:${p}%"></i>${mark}</div>`;
};

// ------------------------------------------------------------------ api
async function api(path, { method = 'GET', body } = {}) {
  const opt = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let res;
  try { res = await fetch('/api' + path, opt); } catch { throw Object.assign(new Error('Cannot reach the server.'), { status: 0, code: 'network', details: {} }); }
  const data = (res.headers.get('content-type') || '').includes('json') ? await res.json() : null;
  if (!res.ok) {
    const e = (data && data.error) || { code: 'error', message: res.statusText, details: {} };
    if (res.status === 401 && S.user && !path.startsWith('/auth/')) { S.user = null; showAuth('Your session expired. Please sign in again.'); }
    throw Object.assign(new Error(e.message), { status: res.status, code: e.code, details: e.details || {} });
  }
  return data;
}

function toast(msg, kind = 'ok') {
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.innerHTML = kind === 'ok' ? `<i>✓</i><span>${esc(msg)}</span>` : `<span>${esc(msg)}</span>`;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), kind === 'ok' ? 3200 : 6000);
}
const fail = (e) => { if (e.status !== 401) toast(e.message, 'err'); };

// ------------------------------------------------------------------ modal / drawer
function openLayer(html, { cls = '', drawer = false, title = '', onClose } = {}) {
  const bd = document.createElement('div');
  bd.className = 'backdrop' + (drawer ? ' right' : '');
  bd.innerHTML = drawer ? `<aside class="drawer" role="dialog" aria-modal="true" aria-label="${esc(title)}">${html}</aside>`
    : `<div class="modal ${cls}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="modal-head"><h3>${esc(title)}</h3><button class="btn ghost icon sm" data-close aria-label="Close">${icon('x')}</button></div><div class="modal-body">${html}</div></div>`;
  const prevFocus = document.activeElement;
  const close = () => { document.removeEventListener('keydown', onKey); bd.remove(); prevFocus?.focus?.(); onClose?.(); };
  const onKey = (e) => { if (e.key === 'Escape' && bd === [...document.querySelectorAll('.backdrop')].pop()) close(); };
  bd.addEventListener('mousedown', (e) => { if (e.target === bd) close(); });
  bd.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(bd);
  (bd.querySelector('input:not([disabled]),select,textarea') || bd.querySelector('button'))?.focus();
  return { el: bd, close };
}

function fieldHTML(f) {
  const id = 'f_' + f.name, req = f.required ? ' required' : '', dis = f.disabled ? ' disabled' : '';
  let ctl;
  if (f.options) ctl = `<select class="input" id="${id}" name="${f.name}"${req}${dis}>${f.options.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(f.value ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  else if (f.type === 'textarea') ctl = `<textarea class="input" id="${id}" name="${f.name}" maxlength="${f.max || 300}" placeholder="${esc(f.placeholder || '')}"${req}${dis}>${esc(f.value ?? '')}</textarea>`;
  else if (f.type === 'checkbox') return `<div class="field"><label><input type="checkbox" name="${f.name}"${f.value ? ' checked' : ''}> ${esc(f.label)}</label>${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ''}</div>`;
  else ctl = `<input class="input" id="${id}" name="${f.name}" type="${f.type || 'text'}" value="${esc(f.value ?? '')}" placeholder="${esc(f.placeholder || '')}"${f.step ? ` step="${f.step}"` : ''}${f.min != null ? ` min="${f.min}"` : ''}${f.minlength ? ` minlength="${f.minlength}"` : ''}${f.autocomplete ? ` autocomplete="${f.autocomplete}"` : ''}${req}${dis}>`;
  return `<div class="field" data-field="${f.name}"><label class="f" for="${id}">${esc(f.label)}</label>${ctl}${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ''}</div>`;
}

function formModal({ title, fields, submit = 'Save', intro = '', danger = false, onSubmit, size = '' }) {
  const m = openLayer(`<form class="form">${intro}${fields.map(fieldHTML).join('')}<div class="form-err" hidden role="alert"></div>
    <div class="modal-foot"><button type="button" class="btn" data-close>Cancel</button><button class="btn ${danger ? 'danger solid' : 'primary'}" type="submit">${esc(submit)}</button></div></form>`, { title, cls: size });
  const form = $('form', m.el), err = $('.form-err', m.el), btn = $('button[type=submit]', m.el);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    err.hidden = true; btn.disabled = true;
    try { await onSubmit(Object.fromEntries(new FormData(form)), form); m.close(); }
    catch (ex) { if (ex.status === 401) return m.close(); err.textContent = ex.message; err.hidden = false; }
    finally { btn.disabled = false; }
  });
  return { ...m, form };
}

function confirmBox({ title, message, confirm = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    const m = openLayer(`<p style="margin-bottom:18px">${esc(message)}</p><div class="modal-foot"><button class="btn" data-close>Keep as is</button><button class="btn ${danger ? 'danger solid' : 'primary'}" id="ok">${esc(confirm)}</button></div>`,
      { title, onClose: () => { if (!done) resolve(false); } });
    $('#ok', m.el).onclick = () => { done = true; m.close(); resolve(true); };
  });
}

// ------------------------------------------------------------------ charts (hand-rolled SVG)
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}
let chartSeq = 0;
/** points: [{x: label, y: number, tip: [[label, value], ...]}] */
function areaChart(el, points, { height = 240, fmt = (v) => v, color = 'var(--accent)' } = {}) {
  const id = 'g' + ++chartSeq;
  const draw = () => {
    const W = Math.max(280, el.clientWidth || 600), H = height, m = { l: 46, r: 10, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const max = niceMax(Math.max(...points.map((p) => p.y), 0)), n = points.length;
    const X = (i) => m.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1)), Y = (v) => m.t + ih - (v / max) * ih;
    const line = points.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join('');
    const area = `${line}L${X(n - 1).toFixed(1)},${m.t + ih}L${X(0).toFixed(1)},${m.t + ih}Z`;
    const ticks = [0, 1, 2, 3, 4].map((k) => (max * k) / 4);
    const step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))));
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Chart">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      <g class="grid">${ticks.map((t) => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 8}" y="${Y(t) + 4}" text-anchor="end">${esc(fmt(t))}</text>`).join('')}</g>
      ${points.map((p, i) => (i % step === 0 ? `<text x="${X(i)}" y="${H - 6}" text-anchor="middle">${esc(p.x)}</text>` : '')).join('')}
      <path d="${area}" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
      <line class="cur" y1="${m.t}" y2="${m.t + ih}" stroke="${color}" stroke-width="1" stroke-dasharray="3 3" visibility="hidden"/>
      <circle class="dot" r="5" fill="var(--surface)" stroke="${color}" stroke-width="2.2" visibility="hidden"/>
      <rect class="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/></svg><div class="tip" hidden></div>`;
    const svg = $('svg', el), tip = $('.tip', el), cur = $('.cur', el), dot = $('.dot', el);
    const show = (clientX) => {
      const r = svg.getBoundingClientRect(), x = ((clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(n === 1 ? 0 : ((x - m.l) / iw) * (n - 1))));
      const p = points[i];
      cur.setAttribute('x1', X(i)); cur.setAttribute('x2', X(i)); cur.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', X(i)); dot.setAttribute('cy', Y(p.y)); dot.setAttribute('visibility', 'visible');
      tip.innerHTML = `<b>${esc(p.label || p.x)}</b>${(p.tip || []).map(([k, v]) => `<div><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('')}`;
      tip.hidden = false;
      tip.style.left = Math.max(80, Math.min(el.clientWidth - 80, (X(i) / W) * r.width)) + 'px';
      tip.style.top = (Y(p.y) / H) * r.height + 'px';
    };
    const hide = () => { tip.hidden = true; cur.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); };
    const hit = $('.hit', el);
    hit.addEventListener('mousemove', (e) => show(e.clientX));
    hit.addEventListener('touchmove', (e) => show(e.touches[0].clientX), { passive: true });
    hit.addEventListener('mouseleave', hide);
  };
  draw();
  const rd = debounce(() => el.isConnected && draw(), 120);
  window.addEventListener('resize', rd);
  S.cleanup.push(() => window.removeEventListener('resize', rd));
}

// ------------------------------------------------------------------ shell & routing
const NAV = [
  ['dashboard', 'Dashboard', 'grid'], ['campaigns', 'Campaigns', 'mega'], ['clients', 'Clients', 'brief'],
  ['reports', 'Reports', 'file'],
  ['—', 'Admin'],
  ['audit', 'Audit log', 'shield', 'audit.read'], ['team', 'Team', 'users', 'users.manage'], ['scheduler', 'Scheduler', 'clock', 'tasks.read'],
];
const VIEWS = {};

function showApp() {
  const u = S.user;
  const nav = NAV.map(([key, label, ic, perm]) => {
    if (key === '—') return NAV.slice(NAV.indexOf(NAV.find((n) => n[0] === key)) + 1).some((n) => !n[3] || can(n[3])) ? `<div class="sect">${label}</div>` : '';
    if (perm && !can(perm)) return '';
    return `<a href="#/${key}" data-nav="${key}">${icon(ic)}${esc(label)}</a>`;
  }).join('');
  $('#root').innerHTML = `<div class="app">
    <aside class="sidebar" id="sidebar">
      <div class="brand"><span class="logo"><svg viewBox="0 0 24 24" fill="#fff"><path d="M7 13l5-9v6h5l-5 10v-7z"/></svg></span>CampaignFlow</div>
      <div class="orgcard"><b>${esc(u.agency_name)}</b><span>${isViewer() ? 'Client portal' : 'Agency workspace'}</span></div>
      <nav class="nav">${nav}</nav><div class="grow"></div>
      <div class="me"><div class="avatar">${esc(u.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase())}</div>
        <div class="who"><b>${esc(u.name)}</b><span>${esc(u.role.replace('_', ' '))}</span></div>
        <button class="btn ghost icon sm" id="theme" title="Toggle theme" aria-label="Toggle theme">${icon(S.theme === 'dark' ? 'sun' : 'moon')}</button>
        <button class="btn ghost icon sm" id="logout" title="Sign out" aria-label="Sign out">${icon('out')}</button></div>
    </aside>
    <div><div class="mobilebar"><button class="btn icon ghost" id="burger" aria-label="Menu">${icon('menu')}</button><b>CampaignFlow</b><span></span></div>
    <main class="main" id="view"></main></div></div>`;
  $('#logout').onclick = async () => { try { await api('/auth/logout', { method: 'POST' }); } catch {} S.user = null; location.hash = ''; showAuth(); };
  $('#theme').onclick = () => { setTheme(S.theme === 'dark' ? 'light' : 'dark'); $('#theme').innerHTML = icon(S.theme === 'dark' ? 'sun' : 'moon'); };
  $('#burger').onclick = () => $('#sidebar').classList.toggle('open');
  $('#sidebar').addEventListener('click', (e) => { if (e.target.closest('a')) $('#sidebar').classList.remove('open'); });
  window.onhashchange = route;
  route();
}

function route() {
  if (!S.user) return;
  const [name = 'dashboard', arg] = location.hash.replace(/^#\/?/, '').split('/');
  const key = VIEWS[name] ? name : 'dashboard';
  const guard = { audit: 'audit.read', team: 'users.manage', scheduler: 'tasks.read' }[key];
  if (guard && !can(guard)) { location.hash = '#/dashboard'; return; }
  $$('[data-nav]').forEach((a) => a.classList.toggle('on', a.dataset.nav === key));
  if (S.current === key && key === 'campaigns') { syncDrawer(arg); return; }   // only the drawer changes
  S.cleanup.forEach((f) => f()); S.cleanup = [];
  S.current = key;
  document.title = `${cap(key)} · CampaignFlow`;
  $('#view').innerHTML = '';
  VIEWS[key](arg);
}

function setTheme(t) {
  S.theme = t;
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('cf_theme', t); } catch {}
}

// ------------------------------------------------------------------ auth screen
function showAuth(notice = '') {
  S.current = null; window.onhashchange = null; S.cleanup.forEach((f) => f()); S.cleanup = [];
  drawer = null; S.drawerId = null; $$('.backdrop').forEach((b) => b.remove());   // nothing from the previous user may linger
  S.cf = { page: 1, q: '', status: '', client: '', channel: '', sort: 'created_at', dir: 'desc' }; S.days = 30;
  document.title = 'Sign in · CampaignFlow';
  const demos = [['Admin', 'admin@northwind.test'], ['Manager', 'manager@northwind.test'], ['Analyst', 'analyst@northwind.test'], ['Client viewer', 'kova@client.test']];
  $('#root').innerHTML = `<div class="auth">
    <section class="auth-art"><div class="brand" style="padding:0"><span class="logo"><svg viewBox="0 0 24 24" fill="#fff"><path d="M7 13l5-9v6h5l-5 10v-7z"/></svg></span>CampaignFlow</div>
      <div><h2>Every client, campaign and dollar in one place.</h2><p>Plan flights, guard budgets, and send reports on schedule, without the spreadsheet sprawl.</p>
      <ul><li>${icon('check')}Budgets that can never be overspent</li><li>${icon('check')}Role-based access for staff and clients</li><li>${icon('check')}Automatic launches, pauses and weekly reports</li><li>${icon('check')}A tamper-proof audit trail</li></ul></div>
      <span style="color:rgba(255,255,255,.5);font-size:13px">Advertising operations for agencies</span></section>
    <section class="auth-form"><div class="auth-box">
      <div class="seg" style="margin-bottom:22px"><button class="on" data-mode="in">Sign in</button><button data-mode="up">Create agency</button></div>
      <h1 id="a-title">Welcome back</h1><p id="a-sub">Sign in to your workspace.</p>
      ${notice ? `<div class="banner">${esc(notice)}</div>` : ''}
      <form id="auth-form"><div class="field" data-up hidden><label class="f" for="agency">Agency name</label><input class="input" id="agency" name="agency_name" minlength="2" autocomplete="organization"></div>
        <div class="field" data-up hidden><label class="f" for="nm">Your name</label><input class="input" id="nm" name="name" autocomplete="name"></div>
        <div class="field"><label class="f" for="em">Email</label><input class="input" id="em" name="email" type="email" required autocomplete="username"></div>
        <div class="field"><label class="f" for="pw">Password</label><input class="input" id="pw" name="password" type="password" required minlength="8" autocomplete="current-password"></div>
        <div class="form-err" hidden role="alert"></div>
        <button class="btn primary" style="width:100%;justify-content:center;height:42px" type="submit">Sign in</button></form>
      <div class="demo"><div class="sect-t">Demo accounts · password <span class="mono">demo1234</span></div><div class="chips">${demos.map(([l, e]) => `<button type="button" class="chip" data-demo="${e}">${l}</button>`).join('')}</div></div>
    </div></section></div>`;
  let mode = 'in';
  const form = $('#auth-form'), err = $('.form-err', form);
  const setMode = (m) => {
    mode = m;
    $$('[data-mode]').forEach((b) => b.classList.toggle('on', b.dataset.mode === m));
    $$('[data-up]').forEach((d) => { d.hidden = m !== 'up'; $('input', d).required = m === 'up'; });
    $('#a-title').textContent = m === 'in' ? 'Welcome back' : 'Create your agency';
    $('#a-sub').textContent = m === 'in' ? 'Sign in to your workspace.' : "You'll be the admin and can invite your team next.";
    $('button[type=submit]', form).textContent = m === 'in' ? 'Sign in' : 'Create agency';
    $('#pw').autocomplete = m === 'in' ? 'current-password' : 'new-password';
  };
  $$('[data-mode]').forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
  $$('[data-demo]').forEach((b) => (b.onclick = () => { setMode('in'); $('#em').value = b.dataset.demo; $('#pw').value = 'demo1234'; form.requestSubmit(); }));
  form.onsubmit = async (e) => {
    e.preventDefault(); err.hidden = true;
    const v = Object.fromEntries(new FormData(form)), btn = $('button[type=submit]', form);
    btn.disabled = true;
    try {
      const r = await api(mode === 'in' ? '/auth/login' : '/auth/register', { method: 'POST', body: mode === 'in' ? { email: v.email, password: v.password } : v });
      S.user = r.user; await boot(true);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; }
  };
}

// ------------------------------------------------------------------ dashboard
VIEWS.dashboard = async function () {
  const view = $('#view');
  let days = S.days || 30;
  view.innerHTML = `<header class="page-head"><div><h1>Dashboard</h1><p>${isViewer() ? 'Performance of your campaigns.' : 'Performance across all clients.'}</p></div>
    <div class="actions"><div class="seg" id="range">${[7, 30, 90].map((d) => `<button data-d="${d}" class="${d === days ? 'on' : ''}">${d}d</button>`).join('')}</div>
    ${can('campaigns.write') ? `<button class="btn primary" id="newc">${icon('plus')}New campaign</button>` : ''}</div></header><div id="dash"><div class="kpis">${'<div class="card kpi"><div class="skel" style="width:50%"></div><div class="skel" style="height:34px;margin:14px 0"></div></div>'.repeat(3)}</div></div>`;
  $('#newc')?.addEventListener('click', () => campaignForm());
  $('#range').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; S.days = days = +b.dataset.d; $$('#range button').forEach((x) => x.classList.toggle('on', x === b)); load(); };
  async function load() {
    let d;
    try { d = await api(`/dashboard?days=${days}`); } catch (e) { return fail(e); }
    const k = d.kpis;
    const delta = (x, invert = false) => {
      if (x.delta_pct == null) return '<span class="muted">no prior data</span>';
      const up = x.delta_pct >= 0, good = invert ? !up : up;
      const big = Math.abs(x.delta_pct) >= 500, shown = big ? '500%+' : `${up ? '+' : ''}${x.delta_pct}%`;
      return `<span class="delta ${good ? 'up' : 'down'}">${up ? '▲' : '▼'} ${big && !up ? '−' : ''}${shown}</span> vs previous ${days}d`;
    };
    const card = (cls, ic, label, val, x, inv) => `<div class="card kpi ${cls}"><div class="lbl"><span class="ic">${icon(ic)}</span>${label}</div><div class="val num">${val}</div><div class="foot">${delta(x, inv)}</div></div>`;
    const sc = d.status_counts, order = ['active', 'scheduled', 'paused', 'draft', 'completed', 'cancelled'];
    const colors = { active: '#22c55e', scheduled: '#3b82f6', paused: '#f59e0b', draft: '#94a3b8', completed: '#8b5cf6', cancelled: '#ef4444' };
    const totalC = d.total_campaigns || 1;
    const maxSpend = Math.max(...d.channels.map((c) => c.spend_cents), 1);
    const activePct = d.active_budget_cents ? Math.round((d.active_spent_cents * 100) / d.active_budget_cents) : 0;
    $('#dash').innerHTML = `
      <div class="kpis">${card('hero', 'dollar', 'Total spend', money(k.spend_cents.value), k.spend_cents)}
        ${card('', 'eye', 'Impressions', compact(k.impressions.value), k.impressions)}${card('', 'click', 'Clicks', int(k.clicks.value), k.clicks)}</div>
      <div class="kpis">${card('', 'target', 'Conversions', int(k.conversions.value), k.conversions)}${card('', 'pct', 'CTR', pct(k.ctr.value, 2), k.ctr)}${card('', 'bolt', 'Cost per conversion', k.cpa_cents.value == null ? '–' : money(k.cpa_cents.value), k.cpa_cents, true)}</div>
      <div class="cols"><section class="card card-pad"><div class="card-head"><h3>Daily spend</h3><span class="muted">${fmtDate(d.period.start)} – ${fmtDate(d.period.end, true)}</span></div><div class="chart" id="chart"></div></section>
        <section class="card card-pad"><div class="card-head"><h3>Spend by channel</h3></div><div class="bars">${d.channels.length ? d.channels.map((c) => `<div class="row"><span>${esc(c.name)}</span><div class="track"><i style="width:${(c.spend_cents / maxSpend) * 100}%"></i></div><b class="num">${money(c.spend_cents, true)}</b></div>`).join('') : '<div class="empty">No spend in this period.</div>'}</div></section></div>
      <div class="cols even"><section class="card card-pad"><div class="card-head"><h3>Campaign status</h3><span class="muted">${d.total_campaigns} total</span></div>
          <div class="mix">${order.filter((s) => sc[s]).map((s) => `<i style="width:${(sc[s] / totalC) * 100}%;background:${colors[s]}" title="${cap(s)}: ${sc[s]}"></i>`).join('')}</div>
          <div class="legend">${order.filter((s) => sc[s]).map((s) => `<span><i style="background:${colors[s]}"></i>${cap(s)} <b class="num" style="color:var(--text)">${sc[s]}</b></span>`).join('')}</div>
          ${d.active_budget_cents ? `<div style="margin-top:22px"><div class="card-head" style="margin-bottom:8px"><span class="muted">Live budget used</span><b class="num">${money(d.active_spent_cents)} of ${money(d.active_budget_cents)}</b></div><div class="track"><i style="width:${activePct}%"></i></div></div>` : ''}</section>
        <section class="card card-pad"><div class="card-head"><h3>Needs attention</h3><span class="muted">${d.attention.length ? d.attention.length + ' active' : ''}</span></div>
          ${d.attention.length ? d.attention.map((c) => `<div class="attn" data-open="${c.id}" tabindex="0" role="button"><div class="grow"><b>${esc(c.name)}</b> <span class="muted">· ${esc(c.client_name)}</span><div class="tags">${alertTags(c.alerts)}</div></div><div style="width:90px">${budgetBar(c)}<div class="muted num" style="font-size:12px;margin-top:4px;text-align:right">${c.budget_used_pct}% used</div></div></div>`).join('') : `<div class="empty"><b>All clear</b>No active campaigns need attention.</div>`}</section></div>`;
    $$('[data-open]').forEach((el) => { const go = () => (location.hash = `#/campaigns/${el.dataset.open}`); el.onclick = go; el.onkeydown = (e) => e.key === 'Enter' && go(); });
    areaChart($('#chart'), d.daily.map((p) => ({ x: fmtDate(p.date), label: fmtDate(p.date, true), y: p.spend_cents,
      tip: [['Spend', money(p.spend_cents)], ['Impressions', int(p.impressions)], ['Clicks', int(p.clicks)], ['Conversions', int(p.conversions)]] })), { fmt: (v) => money(v, true) });
  }
  load();
};

// ------------------------------------------------------------------ campaigns
const TRANSITION_LABEL = { scheduled: 'Schedule', active: 'Activate', paused: 'Pause', completed: 'Mark completed', cancelled: 'Cancel campaign', draft: 'Back to draft' };

VIEWS.campaigns = async function (arg) {
  const f = S.cf, view = $('#view');
  const statuses = ['', 'active', 'scheduled', 'paused', 'draft', 'completed', 'cancelled'];
  view.innerHTML = `<header class="page-head"><div><h1>Campaigns</h1><p>${isViewer() ? 'Everything running for your account.' : 'Plan, launch and track every flight.'}</p></div>
    <div class="actions"><a class="btn" id="export" href="#">${icon('download')}Export CSV</a>${can('campaigns.write') ? `<button class="btn primary" id="newc">${icon('plus')}New campaign</button>` : ''}</div></header>
    <div class="toolbar"><div class="search">${icon('search')}<input class="input" id="q" placeholder="Search campaigns or clients" value="${esc(f.q)}" aria-label="Search"></div>
      ${isViewer() ? '' : `<select class="input" id="fclient" aria-label="Client"><option value="">All clients</option>${S.clients.map((c) => `<option value="${c.id}"${String(f.client) === String(c.id) ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}</select>`}
      <select class="input" id="fchan" aria-label="Channel"><option value="">All channels</option>${S.channels.map((c) => `<option value="${c.code}"${f.channel === c.code ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}</select></div>
    <div class="chips" style="margin-bottom:14px" id="statuschips">${statuses.map((s) => `<button class="chip ${f.status === s ? 'on' : ''}" data-s="${s}">${s ? cap(s) : 'All'}</button>`).join('')}</div>
    <div class="card"><div id="ctable"></div></div>`;
  $('#newc')?.addEventListener('click', () => campaignForm());
  $('#q').oninput = debounce((e) => { f.q = e.target.value; f.page = 1; loadCampaigns(); });
  $('#fclient')?.addEventListener('change', (e) => { f.client = e.target.value; f.page = 1; loadCampaigns(); });
  $('#fchan').onchange = (e) => { f.channel = e.target.value; f.page = 1; loadCampaigns(); };
  $('#statuschips').onclick = (e) => { const b = e.target.closest('.chip'); if (!b) return; f.status = b.dataset.s; f.page = 1; $$('#statuschips .chip').forEach((x) => x.classList.toggle('on', x === b)); loadCampaigns(); };
  loadCampaigns();
  syncDrawer(arg);
};

async function loadCampaigns() {
  const f = S.cf, box = $('#ctable');
  if (!box) return;
  const params = { page: f.page, per_page: 12, q: f.q, status: f.status, client_id: f.client, channel: f.channel, sort: f.sort, dir: f.dir };
  $('#export').href = '/api/campaigns/export.csv?' + qs({ q: f.q, status: f.status, client_id: f.client, channel: f.channel });
  let r;
  try { r = await api('/campaigns?' + qs(params)); } catch (e) { box.innerHTML = `<div class="empty"><b>Could not load campaigns</b>${esc(e.message)}</div>`; return; }
  if (!$('#ctable')) return;
  S.campaignRows = r.items;
  if (!r.items.length) { box.innerHTML = `<div class="empty"><b>No campaigns found</b>${f.q || f.status || f.client || f.channel ? 'Try clearing the filters.' : 'Create your first campaign to get started.'}</div>`; return; }
  const th = (key, label, cls = '') => `<th class="sortable ${cls}" data-sort="${key}" aria-sort="${f.sort === key ? (f.dir === 'asc' ? 'ascending' : 'descending') : 'none'}">${label}${f.sort === key ? `<span class="arr">${f.dir === 'asc' ? '▲' : '▼'}</span>` : ''}</th>`;
  const pages = Math.max(1, Math.ceil(r.total / r.per_page));
  box.innerHTML = `<div class="table-wrap"><table><thead><tr>${th('name', 'Campaign')}<th>Channel</th>${th('status', 'Status')}${th('end_date', 'Flight')}${th('spent_cents', 'Budget', 'r')}<th class="r">CTR</th><th class="r">Conv.</th></tr></thead>
    <tbody>${r.items.map((c) => `<tr class="row-click" data-id="${c.id}" tabindex="0"><td><b>${esc(c.name)}</b><div class="sub">${esc(c.client_name)}</div></td><td>${esc(c.channel_name)}</td><td>${pill(c.status)}</td>
      <td class="num">${fmtDate(c.start_date)} → ${fmtDate(c.end_date)}<div class="sub">${c.status === 'active' || c.status === 'paused' ? c.time_elapsed_pct + '% elapsed' : ''}</div></td>
      <td style="min-width:170px"><div style="display:flex;justify-content:space-between;gap:10px" class="num"><b>${money(c.spent_cents, true)}</b><span class="muted">of ${money(c.budget_cents, true)}</span></div><div style="margin-top:6px">${budgetBar(c)}</div></td>
      <td class="r num">${c.ctr == null ? '–' : pct(c.ctr, 2)}</td><td class="r num">${int(c.conversions)}${c.alerts.length ? ` <span title="${esc(c.alerts.map((a) => ALERTS[a]?.[0]).join(', '))}" style="color:var(--warn)">${icon('alert').replace('class="i"', 'class="i" style="width:15px;height:15px;vertical-align:-3px"')}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>
    <div class="pager"><span>${(r.page - 1) * r.per_page + 1}–${Math.min(r.total, r.page * r.per_page)} of ${r.total}</span><div class="actions"><button class="btn sm" id="prev" ${r.page <= 1 ? 'disabled' : ''}>Previous</button><span>Page ${r.page} / ${pages}</span><button class="btn sm" id="next" ${r.page >= pages ? 'disabled' : ''}>Next</button></div></div>`;
  $$('th[data-sort]', box).forEach((h) => (h.onclick = () => { const k = h.dataset.sort; f.dir = f.sort === k && f.dir === 'asc' ? 'desc' : 'asc'; f.sort = k; loadCampaigns(); }));
  $$('tr[data-id]', box).forEach((tr) => { const go = () => (location.hash = `#/campaigns/${tr.dataset.id}`); tr.onclick = go; tr.onkeydown = (e) => e.key === 'Enter' && go(); });
  $('#prev', box).onclick = () => { f.page--; loadCampaigns(); };
  $('#next', box).onclick = () => { f.page++; loadCampaigns(); };
}

function campaignForm(existing) {
  const editing = !!existing;
  const clients = S.clients.filter((c) => c.status === 'active');
  if (!editing && !clients.length) return toast('Add a client first.', 'err');
  const datesLocked = editing && !['draft', 'scheduled'].includes(existing.status);
  formModal({
    title: editing ? 'Edit campaign' : 'New campaign', submit: editing ? 'Save changes' : 'Create campaign',
    fields: [
      { name: 'name', label: 'Campaign name', required: true, value: existing?.name, placeholder: 'e.g. Spring Launch · EU' },
      ...(editing ? [] : [{ name: 'client_id', label: 'Client', required: true, options: clients.map((c) => [c.id, c.name]) },
        { name: 'channel_id', label: 'Channel', required: true, options: S.channels.map((c) => [c.id, c.name]) }]),
      { name: 'objective', label: 'Objective', options: [['awareness', 'Awareness'], ['traffic', 'Traffic'], ['leads', 'Leads'], ['sales', 'Sales']], value: existing?.objective || 'traffic' },
      { name: 'start_date', label: 'Start date', type: 'date', required: true, value: existing?.start_date || todayISO(), disabled: datesLocked },
      { name: 'end_date', label: 'End date', type: 'date', required: true, value: existing?.end_date || addDays(todayISO(), 30), disabled: datesLocked },
      ...(editing ? [] : [{ name: 'budget', label: 'Total budget (USD)', type: 'number', step: '0.01', min: 1, required: true, placeholder: '5000', hint: 'Spend can never exceed this amount.' },
        { name: 'schedule', label: 'Schedule it now (activates automatically on the start date)', type: 'checkbox', value: true }]),
    ],
    async onSubmit(v) {
      if (editing) {
        const body = { name: v.name, objective: v.objective, version: existing.version };
        if (!datesLocked) { body.start_date = v.start_date; body.end_date = v.end_date; }
        await api(`/campaigns/${existing.id}`, { method: 'PATCH', body });
        toast('Campaign updated');
      } else {
        const budget = dollarsToCents(v.budget);
        if (!Number.isFinite(budget)) throw new Error('Enter a valid budget.');
        const c = await api('/campaigns', { method: 'POST', body: { name: v.name, client_id: +v.client_id, channel_id: +v.channel_id, objective: v.objective, start_date: v.start_date, end_date: v.end_date, budget_cents: budget, schedule: v.schedule === 'on' } });
        toast('Campaign created');
        if (S.current === 'campaigns') location.hash = `#/campaigns/${c.id}`;
      }
      refreshAfterChange();
    },
  });
}

function refreshAfterChange() {
  if (S.current === 'campaigns') loadCampaigns();
  if (S.current === 'dashboard') VIEWS.dashboard();
  if (S.drawerId) loadDrawer(S.drawerId);
}

// ---- campaign drawer
let drawer = null;
function syncDrawer(id) {
  if (!id) { if (drawer) { const d = drawer; drawer = null; S.drawerId = null; d.close(); } return; }
  if (S.drawerId === +id && drawer) return;
  if (!drawer) {
    drawer = openLayer('<div class="drawer-body"><div class="skel" style="height:30px"></div><div class="skel" style="height:120px"></div></div>', { drawer: true, title: 'Campaign', onClose: () => { if (drawer) { drawer = null; S.drawerId = null; if (location.hash.startsWith('#/campaigns/')) location.hash = '#/campaigns'; } } });
  }
  S.drawerId = +id;
  loadDrawer(+id);
}

async function loadDrawer(id) {
  if (!drawer) return;
  let c;
  try { c = await api(`/campaigns/${id}`); } catch (e) { drawer?.close(); return fail(e); }
  if (!drawer || S.drawerId !== id) return;
  const staff = !isViewer();
  const canW = can('campaigns.write');
  const trans = canW ? c.allowed_transitions.filter((t) => !(t === 'active' && c.start_date > todayISO())) : [];
  const live = ['active'].includes(c.status);
  $('.drawer', drawer.el).innerHTML = `<div class="drawer-head"><div><h2>${esc(c.name)}</h2><div class="muted" style="margin-top:4px">${esc(c.client_name)} · ${esc(c.channel_name)} · ${esc(cap(c.objective))}</div><div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">${pill(c.status)}${alertTags(c.alerts)}</div></div><button class="btn ghost icon" data-close aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-body">
      <section><div class="sect-t">Budget</div><div class="card card-pad">
        <div style="display:flex;justify-content:space-between;align-items:baseline"><b class="num" style="font-size:26px;letter-spacing:-.02em">${money(c.spent_cents)}</b><span class="muted num">of ${money(c.budget_cents)} · ${money(c.remaining_cents)} left</span></div>
        <div style="margin:12px 0 8px">${budgetBar(c)}</div>
        <div class="muted" style="font-size:12.5px">${c.budget_used_pct}% of budget used · ${c.time_elapsed_pct}% of flight elapsed (${fmtDate(c.start_date, true)} → ${fmtDate(c.end_date, true)})</div></div></section>
      <section><div class="sect-t">Performance</div><div class="stats">
        <div class="stat"><span>Impressions</span><b class="num">${compact(c.impressions)}</b></div><div class="stat"><span>Clicks</span><b class="num">${int(c.clicks)}</b></div><div class="stat"><span>CTR</span><b class="num">${c.ctr == null ? '–' : pct(c.ctr, 2)}</b></div>
        <div class="stat"><span>Conversions</span><b class="num">${int(c.conversions)}</b></div><div class="stat"><span>Cost / conversion</span><b class="num">${c.cpa_cents == null ? '–' : money(c.cpa_cents)}</b></div><div class="stat"><span>Days with data</span><b class="num">${c.daily.length}</b></div></div></section>
      ${c.daily.length > 1 ? `<section><div class="sect-t">Daily spend</div><div class="chart" id="dchart"></div></section>` : ''}
      ${canW || can('budgets.write') || can('metrics.record') ? `<section><div class="sect-t">Actions</div><div class="actions">
        ${trans.map((t) => `<button class="btn sm ${t === 'cancelled' ? 'danger' : ''}" data-tr="${t}">${esc(t === 'active' && c.status === 'paused' ? 'Resume' : TRANSITION_LABEL[t])}</button>`).join('')}
        ${canW && !['completed', 'cancelled'].includes(c.status) ? '<button class="btn sm" id="a-edit">Edit details</button>' : ''}
        ${can('budgets.write') && !['completed', 'cancelled'].includes(c.status) ? '<button class="btn sm" id="a-budget">Adjust budget</button>' : ''}
        ${can('metrics.record') && live ? '<button class="btn sm" id="a-metrics">Record delivery</button>' : ''}
        ${c.status === 'scheduled' ? `<span class="muted" style="font-size:12.5px;align-self:center">Starts automatically on ${fmtDate(c.start_date, true)}.</span>` : ''}</div></section>` : ''}
      ${staff && c.budget_history?.length ? `<section><div class="sect-t">Budget history</div><div class="timeline">${c.budget_history.map((b) => `<div class="ev"><b class="num">${money(b.old_budget_cents)} → ${money(b.new_budget_cents)}</b> <span class="muted">· ${esc(b.changed_by || 'system')} · ${ago(b.created_at)}</span>${b.reason ? `<div class="muted">${esc(b.reason)}</div>` : ''}</div>`).join('')}</div></section>` : ''}
      ${staff && c.activity?.length ? `<section><div class="sect-t">Activity</div><div class="timeline">${c.activity.map((a) => `<div class="ev"><b>${esc(actionLabel(a.action))}</b> <span class="muted">· ${esc(a.actor_label)} · ${ago(a.created_at)}</span><div>${fmtDetail(a)}</div></div>`).join('')}</div></section>` : ''}
    </div>`;
  if (c.daily.length > 1) areaChart($('#dchart', drawer.el), c.daily.map((p) => ({ x: fmtDate(p.date), label: fmtDate(p.date, true), y: p.spend_cents, tip: [['Spend', money(p.spend_cents)], ['Clicks', int(p.clicks)], ['Conversions', int(p.conversions)]] })), { height: 170, fmt: (v) => money(v, true) });
  $$('[data-tr]', drawer.el).forEach((b) => (b.onclick = async () => {
    const to = b.dataset.tr;
    if (['cancelled', 'completed'].includes(to) && !(await confirmBox({ title: TRANSITION_LABEL[to], message: `This is permanent: a ${to} campaign can't be edited or restarted.`, confirm: TRANSITION_LABEL[to], danger: to === 'cancelled' }))) return;
    try { await api(`/campaigns/${id}/status`, { method: 'POST', body: { status: to, version: c.version } }); toast(`Campaign ${to === 'active' ? 'is live' : 'moved to ' + to}`); refreshAfterChange(); } catch (e) { fail(e); if (e.code === 'version_conflict') loadDrawer(id); }
  }));
  $('#a-edit', drawer.el)?.addEventListener('click', () => campaignForm(c));
  $('#a-budget', drawer.el)?.addEventListener('click', () => budgetForm(c));
  $('#a-metrics', drawer.el)?.addEventListener('click', () => metricsForm(c));
}

function budgetForm(c) {
  formModal({
    title: 'Adjust budget', submit: 'Update budget',
    intro: `<div class="banner">Spent so far: <b>${money(c.spent_cents)}</b>. The budget can't go below this.</div>`,
    fields: [{ name: 'budget', label: 'New total budget (USD)', type: 'number', step: '0.01', min: 0.01, required: true, value: centsToInput(c.budget_cents) },
      { name: 'reason', label: 'Reason', type: 'textarea', placeholder: 'e.g. Client approved extra spend', hint: 'Saved in the budget history and audit log.' }],
    async onSubmit(v) {
      const cents = dollarsToCents(v.budget);
      if (!Number.isFinite(cents)) throw new Error('Enter a valid amount.');
      await api(`/campaigns/${c.id}/budget`, { method: 'POST', body: { budget_cents: cents, reason: v.reason, version: c.version } });
      toast('Budget updated'); refreshAfterChange();
    },
  });
}

function metricsForm(c) {
  const day = [todayISO(), c.end_date].sort()[0];
  formModal({
    title: 'Record delivery', submit: 'Record',
    intro: `<div class="banner"><b>${money(c.remaining_cents)}</b> of budget remaining. Entries for the same day are added together; spend beyond the budget is rejected, and a fully spent campaign pauses itself.</div>`,
    fields: [{ name: 'date', label: 'Date', type: 'date', required: true, value: day },
      { name: 'spend', label: 'Spend (USD)', type: 'number', step: '0.01', min: 0, required: true, placeholder: '250' },
      { name: 'impressions', label: 'Impressions', type: 'number', min: 0, value: 0, step: '1' },
      { name: 'clicks', label: 'Clicks', type: 'number', min: 0, value: 0, step: '1' },
      { name: 'conversions', label: 'Conversions', type: 'number', min: 0, value: 0, step: '1' }],
    async onSubmit(v) {
      const spend = dollarsToCents(v.spend);
      if (!Number.isFinite(spend)) throw new Error('Enter a valid spend.');
      const r = await api(`/campaigns/${c.id}/metrics`, { method: 'POST', body: { date: v.date, spend_cents: spend, impressions: +v.impressions || 0, clicks: +v.clicks || 0, conversions: +v.conversions || 0 } });
      toast(r.auto_paused ? 'Recorded. Budget exhausted, so the campaign was paused.' : 'Delivery recorded'); refreshAfterChange();
    },
  });
}

// ------------------------------------------------------------------ clients
VIEWS.clients = async function () {
  const view = $('#view');
  view.innerHTML = `<header class="page-head"><div><h1>Clients</h1><p>${isViewer() ? 'Your account.' : 'Every account your agency manages.'}</p></div>${can('clients.write') ? `<button class="btn primary" id="newcl">${icon('plus')}New client</button>` : ''}</header><div class="card"><div id="cl"></div></div>`;
  $('#newcl')?.addEventListener('click', () => clientForm());
  await loadClients();
};
async function loadClients() {
  try { S.clients = (await api('/clients')).items; } catch (e) { return fail(e); }
  const box = $('#cl'); if (!box) return;
  if (!S.clients.length) { box.innerHTML = '<div class="empty"><b>No clients yet</b>Add your first client to start creating campaigns.</div>'; return; }
  box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Client</th><th>Contact</th><th class="r">Campaigns</th><th style="min-width:200px">Budget used</th><th>Status</th>${can('clients.write') ? '<th></th>' : ''}</tr></thead><tbody>
    ${S.clients.map((c) => { const p = c.budget_cents ? Math.round((c.spent_cents * 100) / c.budget_cents) : 0; return `<tr><td><b>${esc(c.name)}</b><div class="sub">${esc(c.industry || '')}</div></td><td>${esc(c.contact_name || '–')}<div class="sub">${esc(c.contact_email || '')}</div></td>
      <td class="r num"><a href="#/campaigns" data-cl="${c.id}">${c.campaigns}</a><div class="sub">${c.active_campaigns} active</div></td>
      <td><div class="num" style="display:flex;justify-content:space-between"><b>${money(c.spent_cents, true)}</b><span class="muted">of ${money(c.budget_cents, true)}</span></div><div class="track" style="margin-top:6px"><i style="width:${Math.min(p, 100)}%"></i></div></td>
      <td><span class="pill ${c.status === 'active' ? 'ok' : ''}">${cap(c.status)}</span></td>
      ${can('clients.write') ? `<td class="r"><button class="btn sm" data-edit="${c.id}">Edit</button></td>` : ''}</tr>`; }).join('')}</tbody></table></div>`;
  $$('[data-edit]', box).forEach((b) => (b.onclick = () => clientForm(S.clients.find((c) => c.id === +b.dataset.edit))));
  $$('[data-cl]', box).forEach((a) => (a.onclick = () => { S.cf.client = a.dataset.cl; S.cf.page = 1; }));
}
function clientForm(c) {
  formModal({
    title: c ? 'Edit client' : 'New client', submit: c ? 'Save changes' : 'Add client',
    fields: [{ name: 'name', label: 'Client name', required: true, value: c?.name }, { name: 'industry', label: 'Industry', value: c?.industry },
      { name: 'contact_name', label: 'Contact name', value: c?.contact_name }, { name: 'contact_email', label: 'Contact email', type: 'email', value: c?.contact_email },
      ...(c ? [{ name: 'status', label: 'Status', options: [['active', 'Active'], ['archived', 'Archived']], value: c.status, hint: 'Archiving needs all live campaigns to be completed or cancelled first.' }] : [])],
    async onSubmit(v) {
      await api(c ? `/clients/${c.id}` : '/clients', { method: c ? 'PATCH' : 'POST', body: v });
      toast(c ? 'Client updated' : 'Client added'); loadClients();
    },
  });
}

// ------------------------------------------------------------------ reports
VIEWS.reports = async function () {
  $('#view').innerHTML = `<header class="page-head"><div><h1>Reports</h1><p>Weekly reports are generated automatically every Monday; create one-off reports any time.</p></div>${can('reports.generate') ? `<button class="btn primary" id="newr">${icon('plus')}Generate report</button>` : ''}</header><div class="card"><div id="rl"></div></div>`;
  $('#newr')?.addEventListener('click', reportForm);
  loadReports();
};
async function loadReports(page = 1) {
  let r; try { r = await api(`/reports?page=${page}&per_page=15`); } catch (e) { return fail(e); }
  const box = $('#rl'); if (!box) return;
  if (!r.items.length) { box.innerHTML = '<div class="empty"><b>No reports yet</b>They will appear here after the first weekly run.</div>'; return; }
  const pages = Math.ceil(r.total / r.per_page);
  box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Report</th><th>Period</th><th>Type</th><th>Generated</th><th></th></tr></thead><tbody>${r.items.map((x) => `<tr class="row-click" data-id="${x.id}" tabindex="0"><td><b>${esc(x.title.split(' · ')[0])}</b></td><td class="num">${fmtDate(x.period_start)} → ${fmtDate(x.period_end, true)}</td><td><span class="pill nodot ${x.kind === 'scheduled' ? 'info' : ''}">${x.kind === 'scheduled' ? 'Scheduled' : 'On demand'}</span></td><td>${ago(x.created_at)} <span class="muted">· ${esc(x.generated_by)}</span></td><td class="r"><button class="btn sm">View</button></td></tr>`).join('')}</tbody></table></div>
    ${pages > 1 ? `<div class="pager"><span>${r.total} reports</span><div class="actions"><button class="btn sm" id="pp" ${page <= 1 ? 'disabled' : ''}>Previous</button><span>Page ${page} / ${pages}</span><button class="btn sm" id="pn" ${page >= pages ? 'disabled' : ''}>Next</button></div></div>` : ''}`;
  $$('tr[data-id]', box).forEach((tr) => { tr.onclick = () => openReport(+tr.dataset.id); tr.onkeydown = (e) => e.key === 'Enter' && openReport(+tr.dataset.id); });
  $('#pp', box)?.addEventListener('click', () => loadReports(page - 1)); $('#pn', box)?.addEventListener('click', () => loadReports(page + 1));
}
function reportForm() {
  formModal({
    title: 'Generate report', submit: 'Generate',
    fields: [{ name: 'client_id', label: 'Client', options: [['', 'All clients'], ...S.clients.map((c) => [c.id, c.name])] },
      { name: 'period_start', label: 'From', type: 'date', required: true, value: addDays(todayISO(), -29) }, { name: 'period_end', label: 'To', type: 'date', required: true, value: todayISO() },
      { name: 'async', label: 'Build in the background', type: 'checkbox', hint: 'Queues the job for the scheduler worker. Best for long periods.' }],
    async onSubmit(v) {
      const body = { period_start: v.period_start, period_end: v.period_end, async: v.async === 'on' };
      if (v.client_id) body.client_id = +v.client_id;
      const r = await api('/reports', { method: 'POST', body });
      if (r.task_id) { toast('Queued. It will appear in the list shortly.'); setTimeout(() => loadReports(), 2500); }
      else { toast('Report ready'); loadReports(); openReport(r.id); }
    },
  });
}
async function openReport(id) {
  let r; try { r = await api(`/reports/${id}`); } catch (e) { return fail(e); }
  const p = r.payload, s = p.summary;
  const m = openLayer(`
    <div class="actions" style="justify-content:space-between;margin-bottom:16px"><div><b style="font-size:16px">${esc(r.title.split(' · ')[0])}</b><div class="muted">${fmtDate(p.period.start, true)} → ${fmtDate(p.period.end, true)} · generated ${fmtTime(p.generated_at)}</div></div><a class="btn" href="/api/reports/${r.id}/csv">${icon('download')}Download CSV</a></div>
    <div class="stats" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));margin-bottom:18px">
      <div class="stat"><span>Spend</span><b class="num">${money(s.spend_cents)}</b></div><div class="stat"><span>Impressions</span><b class="num">${compact(s.impressions)}</b></div><div class="stat"><span>Clicks</span><b class="num">${int(s.clicks)}</b></div>
      <div class="stat"><span>CTR</span><b class="num">${s.ctr == null ? '–' : pct(s.ctr, 2)}</b></div><div class="stat"><span>Conversions</span><b class="num">${int(s.conversions)}</b></div><div class="stat"><span>CPA</span><b class="num">${s.cpa_cents == null ? '–' : money(s.cpa_cents)}</b></div></div>
    <div class="sect-t">Daily spend</div><div class="chart" id="rchart" style="margin-bottom:20px"></div>
    <div class="sect-t">By channel</div><div class="card table-wrap" style="margin-bottom:20px"><table><thead><tr><th>Channel</th><th class="r">Spend</th><th class="r">Impr.</th><th class="r">Clicks</th><th class="r">CTR</th><th class="r">Conv.</th><th class="r">CPA</th></tr></thead><tbody>
      ${p.channels.map((c) => `<tr><td>${esc(c.name)}</td><td class="r num">${money(c.spend_cents)}</td><td class="r num">${int(c.impressions)}</td><td class="r num">${int(c.clicks)}</td><td class="r num">${c.ctr == null ? '–' : pct(c.ctr, 2)}</td><td class="r num">${int(c.conversions)}</td><td class="r num">${c.cpa_cents == null ? '–' : money(c.cpa_cents)}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">No delivery in this period.</td></tr>'}</tbody></table></div>
    <div class="sect-t">By campaign</div><div class="card table-wrap"><table><thead><tr><th>Campaign</th><th>Status</th><th class="r">Spend</th><th class="r">Clicks</th><th class="r">CTR</th><th class="r">Conv.</th><th class="r">CPA</th></tr></thead><tbody>
      ${p.campaigns.map((c) => `<tr><td><b>${esc(c.name)}</b><div class="sub">${esc(c.client_name)} · ${esc(c.channel)}</div></td><td>${pill(c.status)}</td><td class="r num">${money(c.spend_cents)}</td><td class="r num">${int(c.clicks)}</td><td class="r num">${c.ctr == null ? '–' : pct(c.ctr, 2)}</td><td class="r num">${int(c.conversions)}</td><td class="r num">${c.cpa_cents == null ? '–' : money(c.cpa_cents)}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">No campaigns.</td></tr>'}</tbody></table></div>`, { title: 'Report', cls: 'lg' });
  areaChart($('#rchart', m.el), p.daily.map((d) => ({ x: fmtDate(d.date), label: fmtDate(d.date, true), y: d.spend_cents, tip: [['Spend', money(d.spend_cents)], ['Clicks', int(d.clicks)], ['Conversions', int(d.conversions)]] })), { height: 190, fmt: (v) => money(v, true) });
}

// ------------------------------------------------------------------ audit log
const actionLabel = (a) => cap(a.replace('.', ' · '));
function fmtDetail(a) {
  const d = a.detail; if (!d) return '';
  const money_keys = a.action === 'campaign.budget_changed' ? ['from', 'to'] : [];
  return `<div class="kv">${Object.entries(d).map(([k, v]) => {
    const isMoney = k.endsWith('_cents') || money_keys.includes(k);
    return `<span>${esc(k.replace(/_cents$/, '').replace(/_/g, ' '))} <b>${esc(isMoney && typeof v === 'number' ? money(v) : typeof v === 'object' ? JSON.stringify(v) : v)}</b></span>`;
  }).join('')}</div>`;
}
VIEWS.audit = async function () {
  const st = { page: 1, q: '', entity_type: '' };
  $('#view').innerHTML = `<header class="page-head"><div><h1>Audit log</h1><p>An append-only record of every change. Entries can't be edited or deleted.</p></div></header>
    <div class="toolbar"><div class="search">${icon('search')}<input class="input" id="aq" placeholder="Search actor, action or detail" aria-label="Search audit log"></div>
      <select class="input" id="ae" aria-label="Entity type"><option value="">All entities</option>${['campaign', 'client', 'user', 'report', 'task', 'agency'].map((e) => `<option value="${e}">${cap(e)}</option>`).join('')}</select></div><div class="card"><div id="al"></div></div>`;
  async function load() {
    let r; try { r = await api('/audit-logs?' + qs({ ...st, per_page: 25 })); } catch (e) { return fail(e); }
    const box = $('#al'); if (!box) return;
    if (!r.items.length) { box.innerHTML = '<div class="empty"><b>No matching entries</b></div>'; return; }
    const pages = Math.ceil(r.total / r.per_page);
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Details</th></tr></thead><tbody>${r.items.map((a) => `<tr><td class="num" style="white-space:nowrap">${fmtTime(a.created_at)}<div class="sub">${ago(a.created_at)}</div></td><td>${esc(a.actor_label)}</td><td><span class="tag mono">${esc(a.action)}</span><div class="sub">${esc(a.entity_type)}${a.entity_id ? ' #' + a.entity_id : ''}</div></td><td>${fmtDetail(a)}</td></tr>`).join('')}</tbody></table></div>
      <div class="pager"><span>${r.total} entries</span><div class="actions"><button class="btn sm" id="pp" ${st.page <= 1 ? 'disabled' : ''}>Previous</button><span>Page ${st.page} / ${pages}</span><button class="btn sm" id="pn" ${st.page >= pages ? 'disabled' : ''}>Next</button></div></div>`;
    $('#pp', box).onclick = () => { st.page--; load(); }; $('#pn', box).onclick = () => { st.page++; load(); };
  }
  $('#aq').oninput = debounce((e) => { st.q = e.target.value; st.page = 1; load(); });
  $('#ae').onchange = (e) => { st.entity_type = e.target.value; st.page = 1; load(); };
  load();
};

// ------------------------------------------------------------------ team
VIEWS.team = async function () {
  $('#view').innerHTML = `<header class="page-head"><div><h1>Team</h1><p>Staff and client logins for your agency.</p></div><button class="btn primary" id="newu">${icon('plus')}Add user</button></header>
    <div class="banner"><b>Admin</b> manages everything · <b>Manager</b> runs clients, campaigns and budgets · <b>Analyst</b> records metrics and builds reports · <b>Client viewer</b> sees only their own client, read-only.</div><div class="card"><div id="ul"></div></div>`;
  $('#newu').onclick = () => userForm();
  loadUsers();
};
async function loadUsers() {
  let r; try { r = await api('/users'); } catch (e) { return fail(e); }
  const box = $('#ul'); if (!box) return;
  box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Last sign-in</th><th>Status</th><th></th></tr></thead><tbody>${r.items.map((u) => `<tr><td><b>${esc(u.name)}</b><div class="sub">${esc(u.email)}</div></td><td>${esc(cap(u.role))}${u.client_name ? `<div class="sub">${esc(u.client_name)}</div>` : ''}</td><td>${u.last_login_at ? ago(u.last_login_at) : '<span class="muted">Never</span>'}</td><td><span class="pill ${u.is_active ? 'ok' : ''}">${u.is_active ? 'Active' : 'Deactivated'}</span></td><td class="r"><button class="btn sm" data-u="${u.id}">Edit</button></td></tr>`).join('')}</tbody></table></div>`;
  $$('[data-u]', box).forEach((b) => (b.onclick = () => userForm(r.items.find((u) => u.id === +b.dataset.u))));
}
function userForm(u) {
  const roles = [['admin', 'Admin'], ['manager', 'Manager'], ['analyst', 'Analyst'], ['client_viewer', 'Client viewer']];
  const m = formModal({
    title: u ? 'Edit user' : 'Add user', submit: u ? 'Save changes' : 'Create user',
    fields: [{ name: 'name', label: 'Name', required: true, value: u?.name },
      ...(u ? [] : [{ name: 'email', label: 'Email', type: 'email', required: true, autocomplete: 'off' }]),
      { name: 'role', label: 'Role', options: roles, value: u?.role || 'analyst' },
      { name: 'client_id', label: 'Client', options: S.clients.map((c) => [c.id, c.name]), value: u?.client_id, hint: 'Client viewers can only see this client.' },
      { name: 'password', label: u ? 'New password (optional)' : 'Password', type: 'password', required: !u, minlength: 8, autocomplete: 'new-password', hint: u ? 'Setting a password signs the user out everywhere.' : 'At least 8 characters.' },
      ...(u ? [{ name: 'is_active', label: 'Account is active', type: 'checkbox', value: !!u.is_active }] : [])],
    async onSubmit(v) {
      const body = { name: v.name, role: v.role, ...(v.role === 'client_viewer' ? { client_id: +v.client_id } : {}) };
      if (v.password) body.password = v.password;
      if (u) body.is_active = v.is_active === 'on'; else body.email = v.email;
      await api(u ? `/users/${u.id}` : '/users', { method: u ? 'PATCH' : 'POST', body });
      toast(u ? 'User updated' : 'User created'); loadUsers();
    },
  });
  const sync = () => { $('[data-field=client_id]', m.el).hidden = $('[name=role]', m.el).value !== 'client_viewer'; };
  $('[name=role]', m.el).onchange = sync; sync();
}

// ------------------------------------------------------------------ scheduler
const TASK_INFO = {
  sweep_campaigns: 'Starts scheduled campaigns on their start date and completes ended ones.',
  weekly_reports: "Builds last week's report for every client (safe to re-run).",
  sync_delivery: 'Demo: simulates ad-platform delivery, honouring budgets.',
  generate_report: 'A report you queued to build in the background.',
};
VIEWS.scheduler = async function () {
  $('#view').innerHTML = `<header class="page-head"><div><h1>Scheduler</h1><p>Background jobs. They survive restarts, retry with backoff, and never run twice at once.</p></div><button class="btn" id="rf">${icon('refresh')}Refresh</button></header><div class="card"><div id="tl"></div></div>`;
  async function load() {
    let r; try { r = await api('/tasks'); } catch (e) { return fail(e); }
    const box = $('#tl'); if (!box) return;
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Job</th><th>Status</th><th>Next run</th><th>Last finished</th><th>Attempts</th><th></th></tr></thead><tbody>${r.items.map((t) => `<tr><td><b>${esc(cap(t.kind))}</b><div class="sub">${esc(TASK_INFO[t.kind] || '')}</div>${t.last_error ? `<div class="sub" style="color:var(--bad)">${esc(t.last_error)}</div>` : ''}</td><td>${pill(t.status)}${t.interval_seconds ? `<div class="sub">every ${t.interval_seconds >= 3600 ? t.interval_seconds / 3600 + 'h' : t.interval_seconds / 60 + 'm'}</div>` : ''}</td><td>${t.status === 'done' ? '–' : ago(t.run_at)}</td><td>${t.finished_at ? ago(t.finished_at) : '–'}</td><td class="num">${t.attempts}/${t.max_attempts}</td><td class="r">${can('tasks.run') ? `<button class="btn sm" data-run="${t.id}" ${t.status === 'running' ? 'disabled' : ''}>${icon('play').replace('class="i"', 'class="i" style="width:14px;height:14px"')}Run now</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
    $$('[data-run]', box).forEach((b) => (b.onclick = async () => { try { await api(`/tasks/${b.dataset.run}/run`, { method: 'POST' }); toast('Queued. The worker picks it up within a second.'); setTimeout(load, 1800); } catch (e) { fail(e); } }));
  }
  $('#rf').onclick = load;
  load();
  const iv = setInterval(() => { if (S.current === 'scheduler') load(); }, 8000);
  S.cleanup.push(() => clearInterval(iv));
};

// ------------------------------------------------------------------ boot
async function boot(afterLogin = false) {
  try {
    if (!afterLogin) S.user = (await api('/auth/me')).user;
    [S.channels, S.clients] = await Promise.all([api('/channels').then((r) => r.items), api('/clients').then((r) => r.items)]);
    showApp();
  } catch (e) { if (e.status === 401 || !S.user) showAuth(); else { $('#root').innerHTML = `<div class="empty"><b>Something went wrong</b>${esc(e.message)}</div>`; } }
}
(() => {
  let t = 'light';
  try { t = localStorage.getItem('cf_theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); } catch {}
  setTheme(t);
  boot();
})();
