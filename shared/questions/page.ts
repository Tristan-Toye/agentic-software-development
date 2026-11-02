// The owner page: inline HTML + JS, no external scripts. All dynamic text goes in via textContent.
export const PAGE = String.raw`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Owner questions</title>
<style>
:root{color-scheme:light dark;--bg:#fafafa;--fg:#1c1c1e;--mut:#666;--card:#fff;--bd:#d8d8dc;--acc:#0a5fd0;--ok:#1a7f37}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#e8e8ea;--mut:#9a9aa0;--card:#202023;--bd:#38383d;--acc:#6aa9ff;--ok:#56d364}}
body{font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:820px;margin:0 auto;padding:16px}
h1{font-size:1.2rem}h2{font-size:1rem;color:var(--mut);margin:1.6em 0 .4em}
.card{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;margin:10px 0}
.card.done{opacity:.85}.id{font:12px ui-monospace,monospace;color:var(--mut)}
.q{font-weight:600;margin:.3em 0}.opt{margin:.2em 0}.rec{color:var(--ok)}
.msg{margin:.4em 0;padding:.3em .6em;border-left:3px solid var(--bd);white-space:pre-wrap}
.msg.owner{border-color:var(--acc)}.who{font-size:12px;color:var(--mut)}
button{font:inherit;padding:4px 10px;margin:3px 6px 3px 0;border:1px solid var(--bd);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
button:hover{border-color:var(--acc)}textarea{width:100%;box-sizing:border-box;min-height:3.2em;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:6px;padding:6px;margin-top:6px}
.err{color:#c62828}.empty{color:var(--mut)}
</style></head><body>
<h1>Owner questions</h1><div id="open"></div><h2>Answered</h2><div id="done"></div>
<script>
const cards = new Map();
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
async function post(id, body, card) {
  const err = card.querySelector('.err'); err.textContent = '';
  const r = await fetch('/api/questions/' + encodeURIComponent(id) + '/messages', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(body)});
  if (!r.ok) { err.textContent = (await r.json().catch(() => ({}))).error || ('HTTP ' + r.status); }
  refresh();
}
function build(q) {
  const c = el('div', 'card' + (q.state === 'answered' ? ' done' : '')); c.id = q.id;
  c.appendChild(el('div', 'id', q.id));
  c.appendChild(el('div', 'q', q.text));
  if (q.blocks) c.appendChild(el('div', '', 'Blocks: ' + q.blocks));
  for (const o of q.options) {
    c.appendChild(el('div', 'opt' + (o.key === q.recommendation ? ' rec' : ''), o.key + '. ' + o.label + (o.consequence ? ' - ' + o.consequence : '') + (o.key === q.recommendation ? ' (recommended)' : '')));
  }
  for (const l of q.lines.slice(1)) {
    const m = el('div', 'msg ' + l.from);
    m.appendChild(el('div', 'who', l.from + ' ' + l.kind + (l.option ? ' ' + l.option : '') + (l.via ? ' via ' + l.via : '') + ' - ' + l.ts));
    m.appendChild(el('div', '', l.text)); c.appendChild(m);
  }
  if (q.state === 'open') {
    const row = el('div'); 
    for (const o of q.options) { const b = el('button', '', o.key + ': ' + o.label); b.onclick = () => post(q.id, {kind:'answer', option:o.key, text:o.label}, c); row.appendChild(b); }
    c.appendChild(row);
    const t = el('textarea'); t.placeholder = 'Text'; c.appendChild(t);
    const f = el('button', '', 'Ask follow-up'); f.onclick = () => { if (t.value.trim()) { post(q.id, {kind:'followup', text:t.value}, c); t.value = ''; } };
    const a = el('button', '', 'Answer'); a.onclick = () => { if (t.value.trim()) { post(q.id, {kind:'answer', text:t.value}, c); t.value = ''; } };
    c.appendChild(f); c.appendChild(a);
  }
  c.appendChild(el('div', 'err'));
  return c;
}
function group(root, list, answered) {
  root.replaceChildren();
  if (!list.length) root.appendChild(el('div', 'empty', answered ? 'None yet.' : 'No open questions.'));
  let last = null;
  for (const q of list) {
    const key = q.repo + ' / ' + q.dossier;
    if (key !== last) { root.appendChild(el('h2', '', key)); last = key; }
    const sig = JSON.stringify(q); let e = cards.get(q.id);
    if (!e || e.sig !== sig) {
      const drafts = e ? e.node.querySelector('textarea') : null;
      const node = build(q); if (drafts && node.querySelector('textarea')) node.querySelector('textarea').value = drafts.value;
      e = {sig, node}; cards.set(q.id, e);
    }
    root.appendChild(e.node);
  }
}
async function refresh() {
  try {
    const r = await fetch('/api/questions?state=all'); const all = await r.json();
    const byDossier = (a, b) => (a.repo + a.dossier).localeCompare(b.repo + b.dossier) || a.ts.localeCompare(b.ts);
    group(document.getElementById('open'), all.filter(q => q.state === 'open').sort(byDossier), false);
    group(document.getElementById('done'), all.filter(q => q.state === 'answered').sort(byDossier).reverse(), true);
  } catch (e) { /* server restarting; try again */ }
}
refresh(); setInterval(refresh, 3000);
</script></body></html>`
