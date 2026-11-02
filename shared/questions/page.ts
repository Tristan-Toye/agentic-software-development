// The owner dashboard: inline HTML + JS + CSS, no external resources. All dynamic text goes in via textContent.
export const PAGE = String.raw`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Owner questions</title>
<style>
:root{color-scheme:light dark;--bg:#f6f7f9;--fg:#1b1d21;--mut:#6b7078;--card:#fff;--bd:#e1e4e9;--acc:#2563eb;--accbg:#e8f0fe;--ok:#15803d;--okbg:#e3f5e9;--warn:#b45309;--warnbg:#fdf0d9;--bad:#c62828;--badbg:#fde6e6;--chip:#eceef2}
@media (prefers-color-scheme:dark){:root{--bg:#111316;--fg:#e6e8eb;--mut:#8f949c;--card:#1b1e23;--bd:#2c3037;--acc:#7aa7ff;--accbg:#1c2740;--ok:#56d364;--okbg:#17301f;--warn:#e3a54a;--warnbg:#33280f;--bad:#ff7b72;--badbg:#3a1a1a;--chip:#272b32}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--bd);padding:8px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:1rem;margin:0}
.counts{flex:1;color:var(--mut);font-size:13px}.counts b{color:var(--fg)}
.pill{font-size:12px;color:var(--mut);display:inline-flex;align-items:center;gap:6px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mut)}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.dot.pending{background:var(--warn)}
.dot.pulse{background:var(--ok);animation:pulse 1.6s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(21,128,61,.5)}100%{box-shadow:0 0 0 8px rgba(21,128,61,0)}}
main{max-width:1280px;margin:0 auto;padding:0 16px 48px}
h2{font-size:.74rem;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:22px 0 8px;display:flex;align-items:center;gap:8px}
.count{background:var(--chip);border-radius:10px;padding:0 8px;font-size:12px;letter-spacing:0}
.band{display:grid;grid-template-columns:minmax(0,1.9fr) minmax(0,1fr);gap:24px;align-items:start}
@media (max-width:900px){.band{grid-template-columns:1fr}}
.sec{border:1px solid var(--bd);border-radius:8px;background:var(--card)}
.need{border-left:3px solid var(--warn);padding:12px 14px;margin:8px 0;background:var(--card);border-radius:8px;border-top:1px solid var(--bd);border-right:1px solid var(--bd);border-bottom:1px solid var(--bd)}
.thr{border-left:3px solid var(--acc)}
.rowi{padding:9px 12px;border-bottom:1px solid var(--bd)}.rowi:last-child{border-bottom:0}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.grow{flex:1;min-width:0}
.chip{display:inline-block;font-size:11.5px;padding:0 8px;border-radius:9px;background:var(--chip);color:var(--fg);white-space:nowrap;line-height:1.7}
.chip.repo{font-weight:600}
.chip.building,.chip.running,.chip.accepting{background:var(--accbg);color:var(--acc)}
.chip.review,.chip.pr,.chip.waiting,.chip.integrating,.chip.q{background:var(--warnbg);color:var(--warn);text-decoration:none}
.chip.done,.chip.merged{background:var(--okbg);color:var(--ok)}
.chip.failed,.chip.stopped,.chip.dropped{background:var(--badbg);color:var(--bad)}
.mut{color:var(--mut)}.small{font-size:12px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.sum{color:var(--mut);margin:3px 0}
.meta{display:flex;gap:12px;flex-wrap:wrap;margin-top:4px;font-size:12px;align-items:center;color:var(--mut)}
.upd{list-style:none;margin:6px 0 0;padding:0;font-size:12.5px}
.upd li{display:flex;gap:8px;padding:1px 0}.upd .t{color:var(--mut);min-width:5.5em}
.upd .k{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;min-width:3.8em;color:var(--mut)}
.upd .runner .k{color:var(--acc)}.upd .stage .k{color:var(--warn)}.upd .update .x{font-weight:600}
.q{font-size:1.02rem;font-weight:600;margin:.4em 0 .2em}.blocks{color:var(--mut);font-size:13px;margin-bottom:6px}
.opts{display:grid;gap:6px;margin:8px 0}
.opt{display:flex;gap:10px;align-items:flex-start;text-align:left;width:100%;font:inherit;padding:8px 11px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer}
.opt:hover:not(:disabled){border-color:var(--acc)}.opt:disabled{cursor:default}.opt.rec{border-color:var(--ok);background:var(--okbg)}
.opt .k{font-weight:700;min-width:1.4em}.opt .l{font-weight:600}.opt .c{display:block;color:var(--mut);font-size:13px;font-weight:400}
.opt .tag{font-size:11px;color:var(--ok);font-weight:600;margin-left:6px}
.msg{max-width:88%;margin:5px 0;padding:6px 10px;border-radius:10px;white-space:pre-wrap;word-break:break-word;background:var(--chip)}
.msg.owner{margin-left:auto;background:var(--accbg)}.who{font-size:11px;color:var(--mut)}
textarea{width:100%;min-height:3.2em;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:8px;padding:7px;margin-top:6px;resize:vertical}
button.btn,a.btn{font:inherit;font-size:12.5px;padding:3px 10px;margin:6px 8px 0 0;border:1px solid var(--bd);border-radius:7px;background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none;display:inline-block}
a.btn.sm{margin:0;padding:0 8px}
button.btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
.err{color:var(--bad);font-size:13px;min-height:1em}
a{color:var(--acc)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--bd);vertical-align:top}
th{font-size:11.5px;color:var(--mut);font-weight:600}tr:last-child td{border-bottom:0}
.tablewrap{overflow-x:auto}
details>summary{cursor:pointer;color:var(--mut);font-size:12px;margin:4px 0}
.qa{margin:4px 0 4px 10px;font-size:12.5px}.qa .a{color:var(--ok)}
.empty{color:var(--mut);padding:10px 12px}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px}
.tab{font:inherit;font-size:13px;padding:3px 12px;border:1px solid var(--bd);border-radius:7px;background:var(--card);color:var(--fg);cursor:pointer}.tab.on{background:var(--acc);border-color:var(--acc);color:#fff}
iframe{width:100%;height:78vh;border:1px solid var(--bd);border-radius:8px;background:#fff}
@media (max-width:560px){main{padding:0 10px 40px}.msg{max-width:96%}}
</style></head><body>
<header><h1>Pipeline</h1><span class="counts" id="counts"></span><span class="pill" id="slack"></span><span class="pill" id="scan"></span></header>
<main>
<div id="threadsec" hidden><h2>Thread <a class="small" href="#" id="closethread">close</a></h2><div id="thread"></div></div>
<section id="opensec" hidden><h2>Open questions <span class="count" id="nneed">0</span></h2><div id="open"></div></section>
<div class="band">
<section><h2>Running dossiers <span class="count" id="nruns">0</span></h2><div class="sec" id="runs"></div></section>
<section><h2>This week's stub</h2><div id="week"></div><div id="otherqa"></div></section>
</div>
<section><h2>Stubs <span class="count" id="nstubs">0</span></h2><div class="sec tablewrap" id="stubs"></div></section>
<section><h2>Open work</h2><div class="tabs" id="tabs"></div><div id="frame"></div></section>
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


function keepOpen(oldN, freshN) {
  const open = new Set(Array.from(oldN.querySelectorAll('details[open][data-k]')).map(d => d.dataset.k));
  freshN.querySelectorAll('details[data-k]').forEach(d => { if (open.has(d.dataset.k)) d.open = true; });
}
// Keyed list with signature diffing: unchanged nodes keep their DOM (scroll, focus, drafts, open expanders).
function sync(root, items, make, emptyText) {
  const old = new Map(); for (const c of Array.from(root.children)) if (c.dataset.key) old.set(c.dataset.key, c);
  const want = [];
  for (const it of items) {
    const sig = JSON.stringify(it.data); const node = old.get(it.key);
    if (node && node.dataset.sig === sig) { want.push(node); continue; }
    if (node && node.querySelector('textarea') === document.activeElement && document.activeElement) { want.push(node); continue; }
    const fresh = make(it.data); fresh.dataset.key = it.key; fresh.dataset.sig = sig;
    if (node) { keepOpen(node, fresh); const a = node.querySelector('textarea'), b = fresh.querySelector('textarea'); if (a && b) b.value = a.value; const e = node.querySelector('.err'), f = fresh.querySelector('.err'); if (e && f) f.textContent = e.textContent; }
    want.push(fresh);
  }
  const empty = root.querySelector(':scope > .empty'); if (empty) empty.remove();
  want.forEach((n, i) => { if (root.children[i] !== n) root.insertBefore(n, root.children[i] || null); });
  while (root.children.length > want.length) root.lastChild.remove();
  if (!want.length && emptyText) root.appendChild(el('div', 'empty', emptyText));
}
// One block rebuilt only when its data changes; open expanders survive.
function patch(root, data, make) {
  const sig = JSON.stringify(data); if (root.dataset.sig === sig) return; root.dataset.sig = sig;
  const fresh = make(data); const old = document.createElement('div'); while (root.firstChild) old.appendChild(root.firstChild);
  keepOpen(old, fresh); root.replaceChildren(fresh);
}
async function post(id, body, card) {
  const err = card.querySelector('.err'); err.textContent = '';
  const r = await fetch('/api/questions/' + encodeURIComponent(id) + '/messages', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(body)});
  if (!r.ok) err.textContent = (await r.json().catch(() => ({}))).error || ('HTTP ' + r.status);
  refresh();
}
function buildQ(q) {
  const open = q.state === 'open';
  const c = el('div', 'need' + (open ? '' : ' thr')); if (open) c.id = q.id;
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


const statusChip = (st) => el('span', 'chip ' + st, st);
function link(text, href, cls) { const a = el('a', cls || '', text); a.href = href; return a; }
function viewBtn(repo, id) { const a = link('View dossier', '/dossier/' + encodeURIComponent(repo) + '/' + encodeURIComponent(id), 'btn sm'); a.target = '_blank'; a.rel = 'noopener'; return a; }
function qaBlock(list, key) {
  if (!list.length) return null;
  const d = el('details'); d.dataset.k = key; d.appendChild(el('summary', '', 'Q&A (' + list.length + ')'));
  for (const a of list) {
    const x = el('div', 'qa'); x.appendChild(el('div', '', 'Q: ' + a.text));
    const an = el('div', 'a', 'A: ' + (a.option ? a.option + ' · ' : '') + a.answer + ' · '); when(an, a.answeredAt || a.asked);
    an.appendChild(link(' thread', '#q=' + a.id)); x.appendChild(an); d.appendChild(x);
  }
  return d;
}
function updatesBlock(list, key) {
  const ul = el('ul', 'upd'); const mk = (u) => { const li = el('li', u.kind); const t = el('span', 't'); when(t, u.ts); li.appendChild(t); li.appendChild(el('span', 'k', u.kind)); li.appendChild(el('span', 'x', (u.stage && u.kind !== 'update' ? u.stage + ': ' : '') + u.text)); return li; };
  list.slice(0, 5).forEach(u => ul.appendChild(mk(u)));
  const wrap = el('div'); wrap.appendChild(ul);
  if (list.length > 5) { const d = el('details'); d.dataset.k = key; d.appendChild(el('summary', '', (list.length - 5) + ' older')); const u2 = el('ul', 'upd'); list.slice(5).forEach(u => u2.appendChild(mk(u))); d.appendChild(u2); wrap.appendChild(d); }
  return wrap;
}
function prPill(pr) {
  const w = el('span', 'pill'); w.appendChild(el('span', 'dot ' + (pr.red ? 'bad' : pr.pending ? 'pending' : pr.green ? 'ok' : '')));
  const a = link('PR #' + pr.number, pr.url); a.target = '_blank'; a.rel = 'noopener'; w.appendChild(a);
  w.appendChild(el('span', '', pr.green + '/' + pr.red + '/' + pr.pending)); w.title = 'checks: green / red / pending'; return w;
}
function stageText(r) { if (!r.phase) return ''; return r.phase === 'plan' ? 'Plan' : 'Phase ' + r.phase + (r.phaseName ? ' · ' + r.phaseName : ''); }
function buildRun(r) {
  const c = el('div', 'rowi'); const top = el('div', 'row');
  top.appendChild(el('strong', '', r.id)); top.appendChild(el('span', 'grow', r.title));
  top.appendChild(el('span', 'chip repo', r.repo)); top.appendChild(statusChip(r.status));
  if (r.runner) { const rc = el('span', 'chip ' + r.runner.state, 'runner ' + r.runner.state); top.appendChild(rc); }
  top.appendChild(viewBtn(r.repo, r.id)); c.appendChild(top);
  if (r.summary) c.appendChild(el('div', 'sum', r.summary));
  const m = el('div', 'meta'); const st = stageText(r) || (r.runner && r.runner.stage) || '';
  if (st) m.appendChild(el('span', 'chip', st));
  const ss = el('span', 'pill');
  if (r.session.state === 'active') { ss.appendChild(el('span', 'dot pulse')); ss.appendChild(el('span', '', 'active')); }
  else if (r.session.state === 'idle') { ss.appendChild(el('span', 'dot')); when(ss, r.session.lastTs, 'idle since '); }
  else ss.appendChild(el('span', '', 'no session'));
  m.appendChild(ss);
  if (r.session.context) m.appendChild(el('span', '', 'ctx ' + kfmt(r.session.context)));
  if (r.session.requests) m.appendChild(el('span', '', (r.session.requestsExact ? '' : '≥') + r.session.requests + ' req'));
  if (r.pr) m.appendChild(prPill(r.pr));
  for (const q of r.openQs) { const a = link('? ' + q.text.slice(0, 60), '#' + q.id, 'chip q'); a.title = q.text; m.appendChild(a); }
  c.appendChild(m);
  if (r.updates.length) c.appendChild(updatesBlock(r.updates, 'u'));
  else if (r.lastActivity) c.appendChild(el('div', 'small mut', r.lastActivity));
  const qa = qaBlock(r.answered, 'qa'); if (qa) c.appendChild(qa);
  return c;
}
const ciText = (pr) => pr ? pr.green + ' green / ' + pr.red + ' red / ' + pr.pending + ' pending' : '';
function buildWeek(rs) {
  const w = el('div', 'sec');
  for (const r of rs) {
    const s = r.stubs[0]; const h = el('div', 'rowi'); const t = el('div', 'row');
    t.appendChild(el('strong', 'mono', s.branch)); t.appendChild(el('span', 'chip repo', r.repo)); t.appendChild(statusChip(s.state)); h.appendChild(t);
    const m = el('div', 'meta');
    if (s.stage) m.appendChild(el('span', 'chip', s.stage));
    if (s.cut) m.appendChild(el('span', '', 'cut ' + s.cut));
    if (s.refAge) when(m, s.refAge, 'ref ');
    if (s.pr) { m.appendChild(prPill(s.pr)); }
    h.appendChild(m); w.appendChild(h);
    if (!s.dossiers.length) w.appendChild(el('div', 'empty', 'No dossiers on this stub yet.'));
    for (const d of s.dossiers) {
      const x = el('div', 'rowi'); const l = el('div', 'row');
      l.appendChild(el('strong', '', d.id)); l.appendChild(el('span', 'grow small', d.title)); l.appendChild(statusChip(d.status || '?'));
      if (d.via === 'open-pr') l.appendChild(el('span', 'chip', 'PR open')); l.appendChild(viewBtn(d.repo, d.id)); x.appendChild(l);
      const qa = qaBlock(d.answered, 'qa-' + d.id); if (qa) x.appendChild(qa); w.appendChild(x);
    }
  }
  return w;
}
function buildOtherQA(list) {
  const d = el('details'); d.dataset.k = 'oq'; d.appendChild(el('summary', '', 'Q&A of other dossiers (' + list.length + ')'));
  for (const r of list) { const x = el('div', 'rowi'); const l = el('div', 'row'); l.appendChild(el('strong', '', r.id)); l.appendChild(el('span', 'grow small', r.title)); l.appendChild(statusChip(r.status)); l.appendChild(viewBtn(r.repo, r.id)); x.appendChild(l); x.appendChild(qaBlock(r.answered, 'qa-' + r.id)); d.appendChild(x); }
  return d;
}
function buildStubsTable(rows) {
  const w = el('table'); const h = el('tr');
  for (const c of ['Repo', 'Branch', 'Cut', 'State', 'Stage', 'Dossiers', 'PR + CI']) h.appendChild(el('th', '', c)); w.appendChild(h);
  for (const r of rows) {
    const tr = el('tr'); const td = (v) => { const d = el('td'); if (v instanceof Node) d.appendChild(v); else d.textContent = v; tr.appendChild(d); };
    td(r.repo); td(el('span', 'mono', r.branch)); td(r.cut); td(statusChip(r.state)); td(r.stage); td(String(r.dossiers.length));
    if (r.pr) { const s = el('span'); const a = link('#' + r.pr.number, r.pr.url); a.target = '_blank'; a.rel = 'noopener'; s.appendChild(a); s.appendChild(el('span', 'mut', ' ' + r.pr.state.toLowerCase() + ' · ' + ciText(r.pr))); td(s); } else td('');
    w.appendChild(tr);
  }
  return w;
}
let tab = ''; try { tab = localStorage.getItem('ow') || ''; } catch (e) {}
function renderOpenWork(list) {
  if (!list.length) { $('tabs').replaceChildren(); $('frame').replaceChildren(el('div', 'empty', 'No open-work report yet.')); return; }
  if (!list.some(o => o.repo === tab)) tab = list[0].repo;
  patch($('tabs'), { list: list.map(o => o.repo), tab }, () => { const f = document.createDocumentFragment(); for (const o of list) { const b = el('button', 'tab' + (o.repo === tab ? ' on' : ''), o.repo); b.type = 'button'; b.onclick = () => { tab = o.repo; try { localStorage.setItem('ow', tab); } catch (e) {} renderOpenWork(list); }; f.appendChild(b); } const d = el('div', 'tabs'); d.style.margin = '0'; d.appendChild(f); return d; });
  const cur = list.find(o => o.repo === tab); const src = '/open-work/' + encodeURIComponent(cur.repo) + '?v=' + encodeURIComponent(cur.updated);
  let fr = $('frame').querySelector('iframe');
  if (!fr) { fr = el('iframe'); fr.setAttribute('sandbox', 'allow-scripts allow-popups'); fr.title = 'Open work'; $('frame').replaceChildren(fr); }
  if (fr.getAttribute('src') !== src) fr.setAttribute('src', src);
}
function hashQ() { const m = location.hash.match(/^#q=(.+)$/); return m ? decodeURIComponent(m[1]) : ''; }
$('closethread').onclick = (e) => { e.preventDefault(); history.replaceState(null, '', location.pathname); refresh(); };
window.addEventListener('hashchange', () => refresh());

async function refresh() {
  try {
    const [qr, rr, sr] = await Promise.all([fetch('/api/questions?state=all'), fetch('/api/runs'), fetch('/api/stubs')]);
    const all = await qr.json();
    const byDossier = (a, b) => (a.repo + a.dossier).localeCompare(b.repo + b.dossier) || a.ts.localeCompare(b.ts);
    const open = all.filter(q => q.state === 'open').sort(byDossier);
    sync($('open'), open.map(q => ({ key: q.id, data: q })), buildQ);
    $('opensec').hidden = !open.length; $('nneed').textContent = open.length;
    const tid = hashQ(), tq = tid && all.find(q => q.id === tid);
    $('threadsec').hidden = !tq; sync($('thread'), tq ? [{ key: tq.id, data: tq }] : [], buildQ);
    const v = rr.ok ? await rr.json() : { runs: [], slack: false, scannedAt: '' };
    const sv = sr.ok ? await sr.json() : { repos: [], openWork: [] };
    const lastAct = (r) => Date.parse(r.session.lastTs) || Date.parse(r.updated) || 0;
    const running = v.runs.filter(r => r.running).sort((a, b) => lastAct(b) - lastAct(a));
    const rows = running.map(r => ({ key: r.id + '@' + r.root, data: Object.assign({}, r, { openQs: open.filter(q => q.dossier === r.id).map(q => ({ id: q.id, text: q.text })) }) }));
    sync($('runs'), rows, buildRun, 'No running dossiers.'); $('nruns').textContent = running.length;
    const cur = sv.repos.filter(r => r.stubs.length);
    patch($('week'), cur, (rs) => rs.length ? buildWeek(rs) : el('div', 'empty', 'No stubs.'));
    const shown = new Set(); cur.forEach(r => r.stubs[0].dossiers.forEach(d => shown.add(r.repo + '/' + d.id)));
    const other = v.runs.filter(r => r.answered.length && !r.running && !shown.has(r.repo + '/' + r.id));
    patch($('otherqa'), other, (l) => l.length ? buildOtherQA(l) : el('span'));
    const all2 = []; sv.repos.forEach(r => r.stubs.forEach(s => all2.push(Object.assign({ repo: r.repo }, s))));
    $('nstubs').textContent = all2.length; patch($('stubs'), all2, (l) => l.length ? buildStubsTable(l) : el('div', 'empty', 'No stubs.'));
    renderOpenWork(sv.openWork || []);
    const newest = cur.map(r => Object.assign({ repo: r.repo }, r.stubs[0])).sort((a, b) => b.cut.localeCompare(a.cut))[0];
    $('counts').replaceChildren(el('span', '', ''), ); const cn = $('counts');
    const b = (n) => { const e = el('b', '', String(n)); return e; };
    cn.appendChild(b(open.length)); cn.appendChild(document.createTextNode(' open questions · ')); cn.appendChild(b(running.length)); cn.appendChild(document.createTextNode(' running · stub '));
    cn.appendChild(document.createTextNode(newest ? newest.state + ' (' + newest.repo + ')' : 'none'));
    $('slack').replaceChildren(el('span', 'dot ' + (v.slack ? 'ok' : '')), el('span', '', v.slack ? 'Slack connected' : 'Slack off'));
    const sc = $('scan'); sc.replaceChildren(); if (v.scannedAt) when(sc, v.scannedAt, 'scanned ');
    tick();
  } catch (e) { /* server restarting; try again */ }
}
refresh(); setInterval(refresh, 5000); setInterval(tick, 15000);
</script></body></html>`
