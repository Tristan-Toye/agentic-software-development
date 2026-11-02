// The owner dashboard: inline HTML + JS + CSS, no external resources. All dynamic text goes in via textContent.
export const PAGE = String.raw`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Owner questions</title>
<style>
:root{color-scheme:light dark;--bg:#f4f5f7;--fg:#1b1d21;--mut:#6b7078;--card:#fff;--bd:#dfe2e7;--acc:#2563eb;--accbg:#e8f0fe;--ok:#15803d;--okbg:#e3f5e9;--warn:#b45309;--warnbg:#fdf0d9;--bad:#c62828;--badbg:#fde6e6;--chip:#eceef2;--sh:0 1px 2px rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#111316;--fg:#e6e8eb;--mut:#8f949c;--card:#1b1e23;--bd:#2c3037;--acc:#7aa7ff;--accbg:#1c2740;--ok:#56d364;--okbg:#17301f;--warn:#e3a54a;--warnbg:#33280f;--bad:#ff7b72;--badbg:#3a1a1a;--chip:#272b32;--sh:none}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--bd);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:1.05rem;margin:0;flex:1}
.pill{font-size:12px;color:var(--mut);display:inline-flex;align-items:center;gap:6px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mut)}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.dot.pending{background:var(--warn)}
.dot.pulse{background:var(--ok);animation:pulse 1.6s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(21,128,61,.5)}100%{box-shadow:0 0 0 8px rgba(21,128,61,0)}}
main{max-width:980px;margin:0 auto;padding:4px 16px 48px}
h2{font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:26px 0 8px;display:flex;align-items:center;gap:8px}
.count{background:var(--chip);border-radius:10px;padding:0 8px;font-size:12px;letter-spacing:0}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;margin:10px 0;box-shadow:var(--sh)}
.card.need{border-left:4px solid var(--warn)}
.card.done{opacity:.9}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.grow{flex:1;min-width:0}
.chip{display:inline-block;font-size:12px;padding:1px 9px;border-radius:10px;background:var(--chip);color:var(--fg);white-space:nowrap}
.chip.repo{font-weight:600}
.chip.building{background:var(--accbg);color:var(--acc)}.chip.review,.chip.pr{background:var(--warnbg);color:var(--warn)}
.chip.done{background:var(--okbg);color:var(--ok)}.chip.q{background:var(--warnbg);color:var(--warn);text-decoration:none}
.mut{color:var(--mut)}.small{font-size:12px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.q{font-size:1.05rem;font-weight:600;margin:.5em 0 .2em}
.blocks{color:var(--mut);font-size:13px;margin-bottom:8px}
.opts{display:grid;gap:8px;margin:10px 0}
.opt{display:flex;gap:10px;align-items:flex-start;text-align:left;width:100%;font:inherit;padding:10px 12px;border:1px solid var(--bd);border-radius:10px;background:var(--card);color:var(--fg);cursor:pointer}
.opt:hover:not(:disabled){border-color:var(--acc)}.opt:disabled{cursor:default}.opt.rec{border-color:var(--ok);background:var(--okbg)}
.opt .k{font-weight:700;min-width:1.4em}.opt .l{font-weight:600}.opt .c{display:block;color:var(--mut);font-size:13px;font-weight:400}
.opt .tag{font-size:11px;color:var(--ok);font-weight:600;margin-left:6px}
.thread{margin:8px 0}
.msg{max-width:88%;margin:6px 0;padding:7px 11px;border-radius:12px;white-space:pre-wrap;word-break:break-word;background:var(--chip)}
.msg.owner{margin-left:auto;background:var(--accbg)}
.who{font-size:11px;color:var(--mut);margin-bottom:2px}
textarea{width:100%;min-height:3.4em;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:10px;padding:8px;margin-top:6px;resize:vertical}
button.btn{font:inherit;font-size:14px;padding:6px 14px;margin:6px 8px 0 0;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer}
button.btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
.err{color:var(--bad);font-size:13px;min-height:1em}
.stepper{display:flex;gap:3px;margin:10px 0 6px}
.step{flex:1;height:22px;border-radius:5px;background:var(--chip);color:var(--mut);font-size:11px;display:flex;align-items:center;justify-content:center}
.step.past{background:var(--accbg);color:var(--acc)}.step.cur{background:var(--acc);color:#fff;font-weight:700}
.last{font-size:13px;color:var(--mut);overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.meta{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:13px;align-items:center}
a{color:var(--acc)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--bd);vertical-align:top}
th{font-size:12px;color:var(--mut);font-weight:600}
.tablewrap{background:var(--card);border:1px solid var(--bd);border-radius:12px;overflow-x:auto;padding:4px 8px}
details>summary{cursor:pointer;color:var(--mut);margin:10px 0;font-size:13px}
.empty{color:var(--mut);padding:8px 2px}
@media (max-width:560px){main{padding:4px 10px 40px}.msg{max-width:96%}.step{font-size:10px;height:20px}th:nth-child(n+4),td:nth-child(n+4){display:none}}
</style></head><body>
<header><h1>Pipeline</h1><span class="pill" id="slack"></span><span class="pill" id="scan"></span></header>
<main>
<h2>Needs you <span class="count" id="nneed">0</span></h2><div id="open"></div>
<details id="answered"><summary>Answered (<span id="nans">0</span>)</summary><div id="done"></div></details>
<h2>Runs <span class="count" id="nruns">0</span></h2><div id="runs"></div>
<h2>Queue <span class="count" id="nqueue">0</span></h2><div id="queue"></div>
<details id="recent"><summary>Recently done (<span id="nrecent">0</span>)</summary><div id="recentlist"></div></details>
</main>
<script>
const $ = (id) => document.getElementById(id);
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function ago(ts) {
  const t = Date.parse(ts); if (!t) return '';
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 45) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}
function when(parent, ts, prefix) { const s = el('span', 'when', (prefix || '') + ago(ts)); s.dataset.ts = ts; s.dataset.prefix = prefix || ''; parent.appendChild(s); return s; }
function tick() { document.querySelectorAll('.when').forEach(s => { s.textContent = s.dataset.prefix + ago(s.dataset.ts); }); }
const kfmt = (n) => n >= 1000 ? Math.round(n / 1000) + 'k' : String(n);

// Keyed list with signature diffing: unchanged cards keep their DOM (scroll, focus, drafts).
function sync(root, items, make, emptyText) {
  const old = new Map(); for (const c of Array.from(root.children)) if (c.dataset.key) old.set(c.dataset.key, c);
  const want = [];
  for (const it of items) {
    const sig = JSON.stringify(it.data); const node = old.get(it.key);
    if (node && node.dataset.sig === sig) { want.push(node); continue; }
    if (node && node.querySelector('textarea') === document.activeElement && document.activeElement) { want.push(node); continue; } // never rebuild under a focused text box
    const fresh = make(it.data); fresh.dataset.key = it.key; fresh.dataset.sig = sig;
    if (node) { const a = node.querySelector('textarea'), b = fresh.querySelector('textarea'); if (a && b) b.value = a.value; const e = node.querySelector('.err'), f = fresh.querySelector('.err'); if (e && f) f.textContent = e.textContent; }
    want.push(fresh);
  }
  const empty = root.querySelector(':scope > .empty'); if (empty) empty.remove();
  want.forEach((n, i) => { if (root.children[i] !== n) root.insertBefore(n, root.children[i] || null); });
  while (root.children.length > want.length) root.lastChild.remove();
  if (!want.length && emptyText) root.appendChild(el('div', 'empty', emptyText));
}

async function post(id, body, card) {
  const err = card.querySelector('.err'); err.textContent = '';
  const r = await fetch('/api/questions/' + encodeURIComponent(id) + '/messages', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(body)});
  if (!r.ok) err.textContent = (await r.json().catch(() => ({}))).error || ('HTTP ' + r.status);
  refresh();
}
function buildQ(q) {
  const open = q.state === 'open';
  const c = el('div', 'card ' + (open ? 'need' : 'done')); c.id = q.id;
  const top = el('div', 'row');
  top.appendChild(el('span', 'chip repo', q.repo)); top.appendChild(el('span', 'chip', q.dossier));
  top.appendChild(el('span', 'grow'));
  const id = el('span', 'mut small mono', q.id + ' · '); when(id, q.ts); top.appendChild(id); c.appendChild(top);
  c.appendChild(el('div', 'q', q.text));
  if (q.blocks) c.appendChild(el('div', 'blocks', 'Blocks: ' + q.blocks));
  const opts = el('div', 'opts');
  for (const o of q.options) {
    const rec = o.key === q.recommendation;
    const b = el('button', 'opt' + (rec ? ' rec' : '')); b.type = 'button';
    b.appendChild(el('span', 'k', o.key));
    const body = el('span'); const l = el('span', 'l', o.label); if (rec) l.appendChild(el('span', 'tag', 'recommended')); body.appendChild(l);
    if (o.consequence) body.appendChild(el('span', 'c', o.consequence));
    b.appendChild(body);
    if (open) b.onclick = () => post(q.id, {kind:'answer', option:o.key, text:o.label}, c); else b.disabled = true;
    opts.appendChild(b);
  }
  c.appendChild(opts);
  const th = el('div', 'thread');
  for (const l of q.lines.slice(1)) {
    const m = el('div', 'msg ' + l.from);
    const w = el('div', 'who', l.from + ' ' + l.kind + (l.option ? ' ' + l.option : '') + (l.via ? ' via ' + l.via : '') + ' · '); when(w, l.ts); m.appendChild(w);
    m.appendChild(el('div', '', l.text)); th.appendChild(m);
  }
  c.appendChild(th);
  if (open) {
    const t = el('textarea'); t.placeholder = 'Ask a follow-up, or write a free-text answer'; c.appendChild(t);
    const f = el('button', 'btn', 'Ask follow-up'); f.type = 'button'; f.onclick = () => { if (t.value.trim()) { post(q.id, {kind:'followup', text:t.value}, c); t.value = ''; } };
    const a = el('button', 'btn primary', 'Answer'); a.type = 'button'; a.onclick = () => { if (t.value.trim()) { post(q.id, {kind:'answer', text:t.value}, c); t.value = ''; } };
    c.appendChild(f); c.appendChild(a);
  }
  c.appendChild(el('div', 'err'));
  return c;
}

const PHASES = ['0','1','2','3','4','5','6','7','8','9'];
function buildRun(r) {
  const c = el('div', 'card');
  const top = el('div', 'row');
  top.appendChild(el('strong', '', r.id)); top.appendChild(el('span', 'grow', r.title));
  top.appendChild(el('span', 'chip ' + r.status, r.status)); c.appendChild(top);
  c.appendChild(el('div', 'mut small', r.repo + (r.jira ? ' · ' + r.jira : '')));
  const st = el('div', 'stepper'); const cur = r.phase.replace(/[a-z]$/, '');
  if (r.phase === 'plan') st.appendChild(el('span', 'chip', 'plan'));
  else PHASES.forEach((p, i) => { st.appendChild(el('div', 'step' + (p === cur ? ' cur' : (PHASES.indexOf(cur) > i ? ' past' : '')), p === cur ? r.phase : p)); });
  c.appendChild(st);
  if (r.lastActivity) c.appendChild(el('div', 'last', r.lastActivity));
  const m = el('div', 'meta');
  const ss = el('span', 'pill');
  if (r.session.state === 'active') { ss.appendChild(el('span', 'dot pulse')); ss.appendChild(el('span', '', 'active')); }
  else if (r.session.state === 'idle') { ss.appendChild(el('span', 'dot')); when(ss, r.session.lastTs, 'idle since '); }
  else ss.appendChild(el('span', '', 'no session'));
  m.appendChild(ss);
  if (r.session.context) m.appendChild(el('span', 'mut', 'context ' + kfmt(r.session.context)));
  if (r.session.requests) m.appendChild(el('span', 'mut', (r.session.requestsExact ? '' : '≥') + r.session.requests + ' requests'));
  if (r.pr) {
    const w = el('span', 'pill'); w.appendChild(el('span', 'dot ' + (r.pr.red ? 'bad' : r.pr.pending ? 'pending' : r.pr.green ? 'ok' : '')));
    const a = el('a', '', 'PR #' + r.pr.number); a.href = r.pr.url; a.target = '_blank'; a.rel = 'noopener'; w.appendChild(a);
    w.appendChild(el('span', '', r.pr.green + ' green / ' + r.pr.red + ' red / ' + r.pr.pending + ' pending')); m.appendChild(w);
  }
  for (const id of r.questions) { const a = el('a', 'chip q', 'question ' + id.split('.').pop()); a.href = '#' + id; m.appendChild(a); }
  c.appendChild(m);
  return c;
}
function renderTable(root, rows, cols, emptyText) {
  const sig = JSON.stringify(rows); if (root.dataset.sig === sig) return; root.dataset.sig = sig;
  if (!rows.length) return void root.replaceChildren(el('div', 'empty', emptyText));
  const w = el('div', 'tablewrap'), t = el('table'), h = el('tr');
  for (const c of cols) h.appendChild(el('th', '', c[0])); t.appendChild(h);
  for (const r of rows) { const tr = el('tr'); for (const c of cols) { const td = el('td'); const v = c[1](r); if (v instanceof Node) td.appendChild(v); else td.textContent = v; tr.appendChild(td); } t.appendChild(tr); }
  w.appendChild(t); root.replaceChildren(w);
}
const statusChip = (r) => el('span', 'chip ' + r.status, r.status);
const updatedCell = (r) => { const s = el('span'); if (r.updated) when(s, r.updated); return s; };
const QCOLS = [['Dossier', r => r.id], ['Title', r => r.title], ['Status', statusChip], ['Repo', r => r.repo], ['Updated', updatedCell]];

function renderRuns(v) {
  const runs = v.runs, now = Date.now();
  const lastAct = (r) => Date.parse(r.session.lastTs) || Date.parse(r.updated) || 0;
  const active = runs.filter(r => ['building','review','pr'].includes(r.status) || (r.session.state === 'active' && !['done','dropped'].includes(r.status))).sort((a, b) => lastAct(b) - lastAct(a));
  const queue = runs.filter(r => (r.status === 'planned' || r.status === 'ready') && !active.includes(r));
  const recent = runs.filter(r => r.status === 'done' && now - (Date.parse(r.updated) || 0) < 7 * 86400000).sort((a, b) => Date.parse(b.updated) - Date.parse(a.updated));
  sync($('runs'), active.map(r => ({ key: r.id + '@' + r.root, data: r })), buildRun, 'No active dossiers.');
  renderTable($('queue'), queue, QCOLS, 'Nothing queued.');
  renderTable($('recentlist'), recent, QCOLS, 'Nothing finished this week.');
  $('nruns').textContent = active.length; $('nqueue').textContent = queue.length; $('nrecent').textContent = recent.length;
  $('slack').replaceChildren(el('span', 'dot ' + (v.slack ? 'ok' : '')), el('span', '', v.slack ? 'Slack connected' : 'Slack off'));
  const sc = $('scan'); sc.replaceChildren(); if (v.scannedAt) when(sc, v.scannedAt, 'scanned ');
}

async function refresh() {
  try {
    const [qr, rr] = await Promise.all([fetch('/api/questions?state=all'), fetch('/api/runs')]);
    const all = await qr.json();
    const byDossier = (a, b) => (a.repo + a.dossier).localeCompare(b.repo + b.dossier) || a.ts.localeCompare(b.ts);
    const open = all.filter(q => q.state === 'open').sort(byDossier), done = all.filter(q => q.state === 'answered').sort(byDossier).reverse();
    sync($('open'), open.map(q => ({ key: q.id, data: q })), buildQ, 'Nothing is waiting for you.');
    sync($('done'), done.map(q => ({ key: q.id, data: q })), buildQ, 'None yet.');
    $('nneed').textContent = open.length; $('nans').textContent = done.length;
    if (rr.ok) renderRuns(await rr.json());
    tick();
  } catch (e) { /* server restarting; try again */ }
}
refresh(); setInterval(refresh, 5000); setInterval(tick, 15000);
</script></body></html>`
