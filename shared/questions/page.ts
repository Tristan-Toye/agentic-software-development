// The owner dashboard: one inline HTML + JS + CSS page, no external resources. All dynamic text goes in via textContent
// or createTextNode; the only innerHTML is the server's escaped markdown render of a dossier. Contract: references/owner-questions.md (Dashboard).
// The script avoids backticks and dollar-brace so it can live in this raw template.
export const PAGE = String.raw`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline · Owner questions</title>
<style>
:root{color-scheme:light dark;--bg:#f5f6f8;--fg:#1b1d21;--mut:#6b7078;--card:#fff;--bd:#e3e6eb;--acc:#2f5fe3;--accbg:#e9effd;--shadow:0 1px 2px rgba(20,24,32,.05);--lift:0 10px 28px rgba(20,24,32,.12);
--ok:#15803d;--okbg:#e2f4e8;--warn:#a85a08;--warnbg:#fcefd8;--bad:#c62828;--badbg:#fde7e7;--chip:#eceef2}
:root{color-scheme:dark;--bg:#101215;--fg:#e6e8eb;--mut:#8f949c;--card:#1a1d22;--bd:#2c3037;--acc:#7fa8ff;--accbg:#1b2640;--shadow:0 1px 2px rgba(0,0,0,.4);--lift:0 10px 28px rgba(0,0,0,.55);
--ok:#56d364;--okbg:#16301e;--warn:#e3a54a;--warnbg:#33280f;--bad:#ff7b72;--badbg:#3a1a1a;--chip:#272b32}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--acc)}
header{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--bd);padding:10px 20px;display:flex;gap:12px 18px;align-items:center;flex-wrap:wrap}
header .brand{font-weight:700;font-size:1rem;color:var(--fg);text-decoration:none}
.crumbs{flex:1;min-width:0;color:var(--mut);font-size:13px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.crumbs a{color:var(--mut)}.crumbs a:hover{color:var(--acc)}.crumbs .cur{color:var(--fg);font-weight:600}
.pill{font-size:12px;color:var(--mut);display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mut);flex:none}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}.dot.info{background:var(--acc)}
main{max-width:1380px;margin:0 auto;padding:8px 20px 64px}
h1{font-size:1.45rem;margin:20px 0 4px}h2{font-size:.78rem;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);margin:30px 0 12px;display:flex;align-items:center;gap:8px}
h2 .n{background:var(--chip);border-radius:10px;padding:0 8px;font-size:12px;letter-spacing:0;color:var(--fg)}
.lead{color:var(--mut);margin:0 0 6px;max-width:70ch}
.mut{color:var(--mut)}.small{font-size:12px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{position:relative;display:flex;flex-direction:column;gap:7px;padding:16px 16px 14px;border:1px solid var(--bd);border-radius:12px;background:var(--card);box-shadow:var(--shadow);color:var(--fg);text-decoration:none;min-width:0;transition:box-shadow .15s,transform .15s,border-color .15s}
.card:hover,.card:focus-within{box-shadow:var(--lift);transform:translateY(-2px);border-color:var(--acc)}
a.card:focus-visible,.card a.stretch:focus-visible{outline:none}
.card:has(a.stretch:focus-visible),a.card:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
@media (prefers-reduced-motion:reduce){.card{transition:none}.card:hover,.card:focus-within{transform:none}}
.card .title{font-weight:650;font-size:1rem;line-height:1.3;padding-right:34px;color:var(--fg);text-decoration:none;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.card .title .id{color:var(--mut);font-weight:600}
a.stretch::after{content:"";position:absolute;inset:0;border-radius:12px}
.card .act{position:relative;z-index:2}
.card .meta{color:var(--mut);font-size:13px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.card .chips,.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.card.dim{opacity:.72}
.badge{position:absolute;top:12px;right:12px;min-width:26px;height:26px;padding:0 8px;border-radius:13px;font-size:13px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;background:var(--chip);color:var(--fg);z-index:2;text-decoration:none}
.badge.warn{background:var(--warnbg);color:var(--warn)}.badge.info{background:var(--accbg);color:var(--acc)}.badge.ok{background:var(--okbg);color:var(--ok)}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;padding:1px 9px;border-radius:10px;background:var(--chip);color:var(--fg);white-space:nowrap;line-height:1.6;text-decoration:none}
.chip.info{background:var(--accbg);color:var(--acc)}.chip.warn{background:var(--warnbg);color:var(--warn)}.chip.ok{background:var(--okbg);color:var(--ok)}.chip.bad{background:var(--badbg);color:var(--bad)}
.chip .dot{width:6px;height:6px;background:currentColor}
.btn{font:inherit;font-size:12.5px;padding:3px 11px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none;display:inline-block}
.btn:hover:not(:disabled){border-color:var(--acc)}.btn:focus-visible,.opt:focus-visible,textarea:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.btn:disabled{opacity:.55;cursor:not-allowed}.btn.primary,.btn.confirm{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.confirm.stop{background:var(--bad);border-color:var(--bad)}
.ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:2px}
.ctl-msg{font-size:12px;color:var(--mut);overflow-wrap:anywhere}.ctl-msg.bad{color:var(--bad)}
.df-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-top:1px solid var(--bd);min-width:0}.df-row:first-of-type{border-top:0}.df-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.df-loc{font-size:11px;color:var(--mut);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.btn.sm{padding:2px 8px;font-size:12px}.b{font-weight:700}.ctl-msg.ok{color:var(--ok)}
.empty{color:var(--mut);padding:14px 4px}
.panel{border:1px solid var(--bd);border-radius:12px;background:var(--card);padding:16px 18px;box-shadow:var(--shadow);margin:14px 0}
.panel .row{display:flex;gap:10px 14px;flex-wrap:wrap;align-items:center;margin:6px 0}
.sum{color:var(--mut);max-width:80ch}
.md{max-width:86ch}.md h1,.md h2,.md h3{border-bottom:1px solid var(--bd);padding-bottom:3px;text-transform:none;letter-spacing:0;color:var(--fg);font-size:1.1rem;margin:22px 0 8px;display:block}
.md h1{font-size:1.35rem}.md pre{background:var(--chip);padding:10px;border-radius:8px;overflow-x:auto;font-size:12.5px}.md code{background:var(--chip);padding:0 4px;border-radius:4px}
.mini{display:flex;gap:10px;flex-wrap:wrap}.mini .card{padding:8px 12px;gap:2px;min-width:170px}
/* timeline: newest on top */
ol.tl{list-style:none;margin:0;padding:0 0 0 22px;border-left:2px solid var(--bd)}
ol.tl>li{position:relative;margin:0 0 14px;padding:0}
ol.tl>li::before{content:"";position:absolute;left:-29px;top:6px;width:10px;height:10px;border-radius:50%;background:var(--bd);border:2px solid var(--bg)}
ol.tl>li.update::before,ol.tl>li.entry::before{background:var(--acc)}ol.tl>li.stage::before{background:var(--warn)}ol.tl>li.question::before{background:var(--warn)}ol.tl>li.answer::before{background:var(--ok)}
ol.tl>li.auto{font-size:12px;opacity:.85}ol.tl>li.auto::before{width:8px;height:8px;left:-28px;top:7px}
ol.tl>li.boundary{margin:20px 0 14px}ol.tl>li.boundary::before{display:none}
.bound{display:flex;align-items:center;gap:10px;margin-left:-22px;color:var(--mut);font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase}
.bound::before,.bound::after{content:"";height:1px;background:var(--bd);flex:1}.bound::before{flex:0 0 14px}
.tl .hd{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;font-size:12px;color:var(--mut)}.tl .kind{text-transform:uppercase;font-size:10.5px;letter-spacing:.05em;font-weight:700}
.tl .tx{white-space:pre-wrap;overflow-wrap:anywhere}.tl li.update .tx{font-weight:600}.tl li.auto .tx{font-weight:400}.tl li.log .tx{color:var(--mut)}
.tl details>summary{cursor:pointer;font-weight:600}.tl details .tx{margin-top:4px;color:var(--mut)}
/* questions */
.need{border:1px solid var(--bd);border-left:3px solid var(--warn);border-radius:12px;padding:14px 16px;margin:10px 0;background:var(--card);box-shadow:var(--shadow);scroll-margin-top:70px}
.need.thr{border-left-color:var(--acc)}.need.hit{outline:2px solid var(--acc)}
.need .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--mut)}.grow{flex:1}
.need .q{font-size:1.02rem;font-weight:600;margin:.4em 0 .2em;overflow-wrap:anywhere}.blocks{color:var(--mut);font-size:13px;margin-bottom:6px}
.opts{display:grid;gap:6px;margin:8px 0}
.opt{display:flex;gap:10px;align-items:flex-start;text-align:left;width:100%;font:inherit;padding:9px 12px;border:1px solid var(--bd);border-radius:10px;background:var(--card);color:var(--fg);cursor:pointer}
.opt:hover:not(:disabled){border-color:var(--acc)}.opt:disabled{cursor:default}.opt.rec{border-color:var(--ok);background:var(--okbg)}
.opt .k{font-weight:700;min-width:1.4em}.opt .l{font-weight:600}.opt .c{display:block;color:var(--mut);font-size:13px;font-weight:400}.opt .tag{font-size:11px;color:var(--ok);font-weight:600;margin-left:6px}
.msg{max-width:88%;margin:5px 0;padding:6px 10px;border-radius:10px;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--chip)}
.msg.owner{margin-left:auto;background:var(--accbg)}.who{font-size:11px;color:var(--mut)}
textarea{width:100%;min-height:3.4em;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:10px;padding:8px;margin:6px 0;resize:vertical}
.err{color:var(--bad);font-size:13px;min-height:1em}
details>summary{cursor:pointer;color:var(--mut);font-size:12.5px;margin:4px 0}
.qa{margin:6px 0;font-size:13px}.qa .a{color:var(--ok)}
iframe.report{display:block;width:100%;height:900px;border:0;border-radius:12px;background:transparent}
.btn.lg{font-size:14px;font-weight:600;padding:7px 18px;border-radius:10px}.btn.lg.go{background:var(--acc);border-color:var(--acc);color:#fff}.btn.lg.go:disabled{background:var(--chip);border-color:var(--bd);color:var(--mut)}
.tabs{display:flex;gap:4px;margin:16px 0 8px;border-bottom:1px solid var(--bd)}.tab{font:inherit;font-size:13px;padding:6px 14px;border:0;border-bottom:2px solid transparent;background:none;color:var(--mut);cursor:pointer}.tab.on{color:var(--fg);border-bottom-color:var(--acc);font-weight:600}
.strip{display:flex;gap:12px;flex-wrap:wrap;align-items:stretch}.strip .card{flex:1 1 280px;max-width:420px;padding:12px 14px;gap:5px}.strip .card .title{font-size:.95rem}
.sub{margin:18px 0 6px;font-size:.78rem;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);font-weight:700}
.now{border:1px solid var(--bd);border-left:4px solid var(--acc);border-radius:12px;background:var(--card);padding:14px 18px;margin:14px 0;box-shadow:var(--shadow)}
.now .lbl{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);font-weight:700}
.now .ph1{font-size:1.15rem;font-weight:700;margin:2px 0 6px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.stepper{display:flex;gap:4px;flex-wrap:wrap;margin:8px 0}.stepper .st{min-width:26px;height:24px;border-radius:12px;background:var(--chip);color:var(--mut);font-size:12px;display:inline-flex;align-items:center;justify-content:center;padding:0 7px}
.stepper .st.done{background:var(--okbg);color:var(--ok)}.stepper .st.cur{background:var(--acc);color:#fff;font-weight:700;padding:0 12px}
.doing{display:flex;gap:8px;align-items:baseline;margin-top:6px}.doing .tx{font-weight:600;overflow-wrap:anywhere}
.waitbox{border:2px solid var(--warn);background:var(--warnbg);border-radius:12px;padding:10px 14px 4px;margin:0 0 12px}
.waitbox h3{margin:0 0 4px;font-size:1.05rem;color:var(--warn)}.waitbox .need{background:var(--card)}
.nowait{color:var(--mut);font-size:12.5px;margin-top:8px}
details.ph{margin:10px 0;border:1px solid var(--bd);border-radius:12px;background:var(--card);padding:0 14px}
details.ph>summary{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;padding:10px 0;margin:0;color:var(--fg);font-size:14px}
details.ph.cur{border-color:var(--acc)}details.ph .pl{font-weight:700}details.ph .ps{color:var(--mut);flex:1 1 220px;min-width:0;overflow-wrap:anywhere}details.ph .pc{color:var(--mut);font-size:12px;margin-left:auto}
details.ph>ol.tl{margin:2px 0 14px 6px}
ol.tl>li.ev .ic{display:inline-block;min-width:1.3em;text-align:center;font-weight:700}
ol.tl>li.key .tx{font-weight:600}
ol.tl>li.k-stage::before,ol.tl>li.k-done::before,ol.tl>li.k-answered::before,ol.tl>li.k-suite-green::before{background:var(--ok)}
ol.tl>li.k-suite-red::before,ol.tl>li.k-failed::before,ol.tl>li.k-stopped::before{background:var(--bad)}
ol.tl>li.k-question::before,ol.tl>li.k-waiting::before,ol.tl>li.k-gate::before{background:var(--warn)}
ol.tl>li.k-pr::before,ol.tl>li.k-workflow::before,ol.tl>li.k-start::before{background:var(--acc)}
li.k-stage .ic,li.k-done .ic,li.k-answered .ic,li.k-suite-green .ic{color:var(--ok)}li.k-suite-red .ic,li.k-failed .ic,li.k-stopped .ic{color:var(--bad)}li.k-question .ic,li.k-waiting .ic,li.k-gate .ic{color:var(--warn)}li.k-pr .ic,li.k-workflow .ic,li.k-start .ic{color:var(--acc)}
ol.tl>li.rt,ol.tl>li.more{font-size:12px;color:var(--mut)}ol.tl>li.rt .tx{font-weight:400}ol.tl>li.more::before{width:7px;height:7px;left:-27px;top:8px}
li.more ol{list-style:none;margin:4px 0 0;padding-left:8px}li.more ol li{margin:2px 0}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:20px;align-items:start}.cols>.scol{position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto;order:2}.cols>.main-col{order:1;min-width:0}
.scol h3{margin:14px 0 8px;font-size:.78rem;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);font-weight:700}.scol>h3:first-child{margin-top:0}
.scard{border:1px solid var(--bd);border-radius:12px;background:var(--card);box-shadow:var(--shadow);padding:12px 14px;margin:0 0 10px;display:flex;flex-direction:column;gap:5px;min-width:0}.scard .title{font-weight:600;color:var(--fg);text-decoration:none;overflow-wrap:anywhere;font-size:.92rem}.scard .title:hover{color:var(--acc)}
ul.tree{list-style:none;margin:6px 0 0;padding:0 0 0 10px;border-left:2px solid var(--bd)}ul.tree>li{position:relative;margin:2px 0;padding-left:10px}ul.tree>li::before{content:"";position:absolute;left:-12px;top:50%;width:10px;border-top:2px solid var(--bd)}
a.sub-item{display:flex;gap:6px;align-items:center;flex-wrap:nowrap;white-space:nowrap;overflow:hidden;padding:3px 6px;border-radius:8px;color:var(--fg);text-decoration:none;font-size:12.5px;min-width:0}a.sub-item:hover{background:var(--accbg)}a.sub-item.done{color:var(--mut)}
.sid{font-weight:600;white-space:nowrap}.sname{color:var(--mut);flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}a.sub-item>.chip,a.sub-item>.qb,a.sub-item>.dot{flex:none}a.sub-item>.chip{font-size:10.5px;padding:0 7px}.qb{background:var(--warnbg);color:var(--warn);border-radius:9px;padding:0 7px;font-size:11px;font-weight:700}
.dot.live{animation:pulse 1.4s ease-in-out infinite}@keyframes pulse{50%{opacity:.35}}@media (prefers-reduced-motion:reduce){.dot.live{animation:none}}
details.mstubs{margin-top:10px}details.mstubs>summary{font-size:.78rem;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);font-weight:700}
@media (max-width:1000px){.cols{grid-template-columns:minmax(0,1fr)}.cols>.scol{position:static;max-height:none;overflow:visible;order:1}.cols>.main-col{order:2}}
@media (max-width:560px){header{padding:8px 16px}main{padding:4px 16px 56px}.msg{max-width:96%}.grid{grid-template-columns:1fr}}
</style></head><body>
<header><a class="brand" href="/">Pipeline</a><nav class="crumbs" id="crumbs" aria-label="Breadcrumb"></nav><span class="pill" id="slack"></span><span class="pill" id="upd"></span></header>
<main id="view"></main>
<script>
const enc = encodeURIComponent;
function h(tag, a, kids) {
  const e = document.createElement(tag);
  if (a) for (const k in a) { const v = a[k]; if (v === null || v === undefined || v === false) continue; if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else e.setAttribute(k, v === true ? '' : String(v)); }
  (function add(list) { list.forEach(c => { if (c === null || c === undefined || c === false) return; if (Array.isArray(c)) return add(c); e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); }); })(kids || []);
  if (a && a['data-raw']) e.innerHTML = a['data-html'] || '';
  return e;
}
function ago(ts) {
  const t = Date.parse(ts); if (!t) return '';
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 45) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}
function when(ts, prefix) { if (!ts || !Date.parse(ts)) return null; return h('time', { datetime: ts, title: new Date(ts).toLocaleString(), class: 'when' }, [(prefix || '') + ago(ts)]); }
const kfmt = (n) => n >= 1000 ? Math.round(n / 1000) + 'k' : String(n);
const TONE = { planned: 'neutral', ready: 'info', building: 'info', review: 'warn', pr: 'warn', done: 'ok', dropped: 'neutral', running: 'info', waiting: 'warn', failed: 'bad', stopped: 'bad', gap: 'warn', 'contract-change': 'warn', unknown: 'neutral',
  accepting: 'info', integrating: 'warn', merged: 'ok', blocked: 'bad', green: 'ok', red: 'bad', pending: 'warn', open: 'warn' };
function chip(text, kind, extra) { return h('span', { class: 'chip ' + (TONE[kind || text] || '') + (extra ? ' ' + extra : '') }, [text]); }
function prChip(pr) {
  const tone = pr.red ? 'red' : pr.pending ? 'pending' : pr.green ? 'green' : 'neutral';
  const a = h('a', { class: 'chip act ' + (TONE[tone] || ''), href: pr.url, target: '_blank', rel: 'noopener', title: 'checks: ' + pr.green + ' green, ' + pr.red + ' red, ' + pr.pending + ' pending' }, [h('span', { class: 'dot' }), 'PR #' + pr.number + ' · ' + pr.green + '/' + pr.red + '/' + pr.pending]);
  return a;
}

// ---- state ----
const S = { q: [], runs: [], slack: false, scannedAt: '', stubs: { repos: [], openWork: [] }, loaded: false, detail: null, live: false, changedAt: 0, showAll: {}, ctl: {}, qerr: {}, tab: {}, frameH: {} };
let R = { kind: 'home' };
let routeSig = '';
let scrollTarget = '';

function parseRoute() {
  const seg = location.pathname.split('/').filter(Boolean).map(decodeURIComponent);
  if (!seg.length) return { kind: 'home' };
  if (seg[0] === 'questions') return { kind: 'questions' };
  if (seg[0] === 'repo' && seg[1]) {
    const repo = seg[1];
    if (seg.length === 2) return { kind: 'repo', repo };
    if (seg[2] === 'stub' && seg[3]) return { kind: 'stub', repo, branch: seg.slice(3).join('/') };
    if (seg[2] === 'graph') return { kind: 'repo', repo };
    return { kind: 'item', repo, id: seg[2], forceDossier: seg[3] === 'dossier' };
  }
  return { kind: 'home' };
}
function hashQ() { const m = location.hash.match(/^#(?:q=)?(.+)$/); return m ? decodeURIComponent(m[1]) : ''; }
const hrefRepo = (repo) => '/repo/' + enc(repo);
const hrefItem = (repo, id) => '/repo/' + enc(repo) + '/' + enc(id);
const hrefStub = (repo, b) => '/repo/' + enc(repo) + '/stub/' + b.split('/').map(enc).join('/');

// ---- classification ----
const inProgress = (r) => r.running || ['building', 'review', 'pr'].indexOf(r.status) >= 0;
const isDone = (r) => r.status === 'done' || r.status === 'dropped';
const notStarted = (r) => !inProgress(r) && !isDone(r);
const findRun = (repo, id) => S.runs.find(r => r.repo === repo && r.id === id);
const repoNames = () => { const s = new Set(); S.runs.forEach(r => s.add(r.repo)); S.stubs.repos.forEach(r => s.add(r.repo)); return Array.from(s).sort(); };
const openQs = (r) => S.q.filter(q => q.state === 'open' && q.dossier === r.id && qRepo(q) === r.repo);
const rlabel = (repo) => { const r = S.runs.find(x => x.repo === repo); if (r && r.repoLabel) return r.repoLabel; const t = S.stubs.repos.find(x => x.repo === repo); return (t && t.label) || repo; };
const allOpen = () => S.q.filter(q => q.state === 'open').sort((a, b) => (a.repo + a.dossier).localeCompare(b.repo + b.dossier) || a.ts.localeCompare(b.ts));
function stageText(r) { if (!r.phase) return r.runner && r.runner.stage ? 'Stage ' + r.runner.stage : ''; return r.phase === 'plan' ? 'Plan' : 'Phase ' + r.phase + (r.phaseName ? ' · ' + r.phaseName : ''); }
function itemMode(run) { if (R.forceDossier) return 'dossier'; return run.view === 'run' ? 'run' : 'dossier'; }
function newestStub(repo) { const r = S.stubs.repos.find(x => x.repo === repo); return r && r.stubs.length ? r.stubs[0] : null; }
const doneKey = (r) => (r.updated || '') + r.id;

// ---- questions ----
function qRepo(q) { const c = S.runs.filter(x => x.id === q.dossier); const r = c.find(x => x.repo === q.repo) || c.find(x => q.root && (q.root === x.worktree || q.root.indexOf(x.worktree + '/') === 0 || q.root === x.main || q.root.indexOf(x.main + '/') === 0)) || (c.length === 1 ? c[0] : null); return r ? r.repo : q.repo; }
function optButtons(q) {
  const open = q.state === 'open';
  return h('div', { class: 'opts' }, q.options.map(o => {
    const rec = o.key === q.recommendation;
    return h('button', { class: 'opt' + (rec ? ' rec' : ''), type: 'button', 'data-act': 'opt', 'data-qid': q.id, 'data-opt': o.key, disabled: !open }, [
      h('span', { class: 'k' }, [o.key]),
      h('span', {}, [h('span', { class: 'l' }, [o.label, rec ? h('span', { class: 'tag' }, ['recommended']) : null]), o.consequence ? h('span', { class: 'c' }, [o.consequence]) : null])]);
  }));
}
function msgBlock(l) {
  return h('div', { class: 'msg ' + l.from }, [h('div', { class: 'who' }, [l.from + ' ' + l.kind + (l.option ? ' ' + l.option : '') + (l.via ? ' via ' + l.via : '') + ' · ', when(l.ts)]), h('div', {}, [l.text])]);
}
function qCard(q, opts) {
  const open = q.state === 'open'; const repo = qRepo(q);
  const kids = [
    h('div', { class: 'top' }, [chip(rlabel(repo), 'neutral'), h('a', { class: 'chip', href: hrefItem(repo, q.dossier) }, [q.dossier]), h('span', { class: 'grow' }), h('span', { class: 'mono' }, [q.id + ' · ']), opts.queue ? when(q.ts, 'waiting ') : when(q.ts)]),
    h('div', { class: 'q' }, [q.text]), q.blocks ? h('div', { class: 'blocks' }, ['Blocks: ' + q.blocks]) : null, optButtons(q)];
  if (opts.thread) kids.push(h('div', {}, q.lines.slice(1).map(msgBlock)));
  if (open) kids.push(h('textarea', { placeholder: 'Ask a follow-up, or write a free-text answer', 'data-qid': q.id }),
    h('div', { class: 'ctl' }, [h('button', { class: 'btn', type: 'button', 'data-act': 'followup', 'data-qid': q.id }, ['Ask follow-up']), h('button', { class: 'btn primary', type: 'button', 'data-act': 'answer', 'data-qid': q.id }, ['Answer'])]));
  kids.push(h('div', { class: 'err' }, [S.qerr[q.id] || '']));
  return h('div', { class: 'need' + (open ? '' : ' thr') + (opts.hit === q.id ? ' hit' : ''), id: 'q-' + q.id, 'data-k': 'q:' + q.id + (opts.thread ? ':t' : ':x') }, kids);
}

// ---- controls ----
function controls(r, big) {
  const key = r.repo + '/' + r.id; const st = S.ctl[key] || {}; const kids = [];
  const NAME = { start: 'Start', resume: 'Resume', stop: 'Stop', 'fix-deferred': 'Fix all deferred' };
  ['start', 'resume', 'stop', 'fix-deferred'].forEach(a => {
    const c = r.controls && r.controls[a]; if (!c || !c.show) return;
    const confirming = st.confirm === a; const busy = st.busy === a;
    const label = busy ? { start: 'Starting…', resume: 'Resuming…', stop: 'Stopping…', 'fix-deferred': 'Starting the fix…' }[a] : confirming ? 'Confirm: ' + NAME[a].toLowerCase() : NAME[a];
    kids.push(h('button', { class: 'btn act' + (big ? ' lg' : '') + (big && a !== 'stop' && !confirming ? ' go' : '') + (confirming ? ' confirm ' + a : ''), type: 'button', 'data-act': 'ctl', 'data-ctl': a, 'data-repo': r.repo, 'data-id': r.id, disabled: !c.ok || !!st.busy }, [label]));
    if (confirming) kids.push(h('button', { class: 'btn act' + (big ? ' lg' : ''), type: 'button', 'data-act': 'ctl-cancel', 'data-repo': r.repo, 'data-id': r.id }, ['Cancel']));
    if (!c.ok && c.reason) kids.push(chip(c.reason, 'blocked'));
  });
  if (r.runner) kids.push(h('a', { class: 'small act', href: '/api/runs/' + enc(r.repo) + '/' + enc(r.id) + '/log', target: '_blank', rel: 'noopener' }, ['runner log']));
  const out = [];
  if (kids.length) out.push(h('div', { class: 'ctl' }, kids));
  if (st.msg) out.push(h('div', { class: 'ctl-msg ' + (st.ok ? 'ok' : 'bad') }, [st.msg]));
  return out;
}

// ---- cards ----
function card(href, title, parts) {
  return h('a', { class: 'card', href }, [h('span', { class: 'title' }, [title])].concat(parts));
}
function section(title, n, content, key) {
  return h('section', { 'data-k': 'sec:' + key }, [h('h2', {}, [title, n === null ? null : h('span', { class: 'n' }, [String(n)])]), content]);
}
const grid = (cards, empty) => cards.length ? h('div', { class: 'grid' }, cards) : h('div', { class: 'empty' }, [empty]);

// ---- views ----
function queueOpen() { return S.q.filter(q => q.state === 'open').sort((a, b) => a.ts.localeCompare(b.ts) || a.id.localeCompare(b.id)); }
function queueView(hit) {
  const open = queueOpen();
  return open.length ? h('div', { class: 'queue', 'data-k': 'queue' }, open.map(q => qCard(q, { queue: true, thread: !!hit, hit }))) : h('div', { class: 'empty', 'data-k': 'noq' }, ['No open questions.']);
}
function homeView() {
  const open = allOpen(); const cards = [];
  const byRepo = {}; open.forEach(q => { const r = qRepo(q); byRepo[r] = (byRepo[r] || 0) + 1; });
  repoNames().forEach(repo => {
    const rs = S.runs.filter(r => r.repo === repo); const np = rs.filter(inProgress).length, ns = rs.filter(notStarted).length, nd = rs.filter(isDone).length;
    const nq = byRepo[repo] || 0; const st = newestStub(repo); const cs = [];
    if (st) cs.push(chip('stub ' + st.state, st.state)); if (nq) cs.push(chip(nq + ' open question' + (nq > 1 ? 's' : ''), 'open'));
    cards.push(h('a', { class: 'card', href: hrefRepo(repo), 'data-k': 'home:' + repo }, [
      h('span', { class: 'title' }, [rlabel(repo)]), h('span', { class: 'badge ' + (np ? 'info' : '') }, [String(np)]),
      h('span', { class: 'meta' }, [np + ' in progress · ' + ns + ' not started · ' + nd + ' done']), h('span', { class: 'chips' }, cs)]));
  });
  return h('div', {}, [h('h1', {}, ['Pipeline']), h('p', { class: 'lead' }, ['Pick a repo to see what is running, what is next and what is done.']), h('div', { class: 'grid' }, cards),
    section('Open questions', open.length, queueView(''), 'queue')]);
}
function questionsView() {
  const hq = hashQ(); const tq = hq && S.q.find(q => q.id === hq && q.state === 'answered');
  const kids = [h('h1', {}, ['Open questions']), h('p', { class: 'lead' }, ['Every question waiting for you, across repos, oldest first. Answer here or in Slack; the first answer is final.'])];
  if (tq) kids.push(section('Thread', null, qCard(tq, { thread: true, hit: hq }), 'thread'));
  kids.push(queueView(hq));
  return h('div', {}, kids);
}
function latestDoing(r) { const u = r.updates.find(x => !x.auto) || r.updates[0]; return u ? u.text : (r.lastActivity || ''); }
function stageOf9(r) { return /^\d+$/.test(r.phase || '') ? 'Stage ' + r.phase + '/9' + (r.phaseName ? ' · ' + r.phaseName : '') : stageText(r); }
function openStubs(repo) { const rr = S.stubs.repos.find(x => x.repo === repo); if (!rr) return []; return (rr.open || []).map(b => rr.stubs.find(s => s.branch === b)).filter(Boolean); }
function fitFrame(f) {
  try {
    const d = f.contentDocument; if (!d || !d.documentElement) return;
    const set = () => { const hh = Math.ceil(d.documentElement.getBoundingClientRect().height); const repo = f.getAttribute('data-repo'); if (hh > 50 && Math.abs((S.frameH[repo] || 0) - hh) > 2) { S.frameH[repo] = hh; f.style.height = hh + 'px'; } };
    set(); if (f._ro) f._ro.disconnect(); if (window.ResizeObserver) { f._ro = new ResizeObserver(set); f._ro.observe(d.documentElement); }
  } catch (e) { /* cross-origin or gone */ }
}
document.addEventListener('load', (e) => { const f = e.target; if (f && f.tagName === 'IFRAME' && f.classList.contains('report')) fitFrame(f); }, true);
const isLive = (r) => !!r && ((r.runner && (r.runner.state === 'running' || r.runner.state === 'waiting')) || (r.session && r.session.state === 'active'));
function subItem(repo, id, noPr, done, ttl) {
  const r = S.runs.find(x => x.repo === repo && x.id === id); const nq = r ? openQs(r) : [];
  const title = (r && r.title) || ttl || '';
  const st = done ? 'done' : r ? stageText(r) : '';
  const kids = [h('span', { class: 'sid mono', style: 'font-weight:700' }, [id]), h('span', { class: 'sname', title: id + (title ? ' · ' + title : '') }, [title])];
  if (st) kids.push(chip(st, done ? 'done' : 'neutral'));
  if (!done && isLive(r)) kids.push(h('span', { class: 'dot info live', title: 'running' }));
  if (noPr) kids.push(chip('no PR yet', 'neutral'));
  if (nq.length) kids.push(h('span', { class: 'qb', title: nq.length + ' open question(s)' }, ['? ' + nq.length]));
  return h('li', { 'data-k': 'sub:' + repo + '/' + id }, [h('a', { class: 'sub-item' + (done ? ' done' : ''), href: hrefItem(repo, id) }, kids)]);
}
function stubTree(repo, s) {
  const cs = [chip(s.state)]; if (s.stage) cs.push(chip(s.stage, 'neutral')); if (s.pr) cs.push(prChip(s.pr));
  const mem = (s.members || []).map(m => subItem(repo, m.id, m.noPr, false));
  const done = s.dossiers.map(d => subItem(repo, d.id, false, true, d.title));
  return h('div', { class: 'scard', 'data-k': 'ostub:' + repo + '/' + s.branch }, [
    h('a', { class: 'title mono', href: hrefStub(repo, s.branch) }, [s.branch]),
    h('div', { class: 'meta' }, ['cut ' + s.cut]), h('div', { class: 'chips' }, cs),
    mem.length || done.length ? h('ul', { class: 'tree' }, mem.concat(done)) : h('div', { class: 'meta' }, ['Nothing in progress or merged yet.'])]);
}
function sideColumn(repo) {
  const rr = S.stubs.repos.find(x => x.repo === repo); const stubs = rr ? rr.stubs : []; const os = openStubs(repo);
  const placed = {}; stubs.forEach(s => (s.members || []).forEach(m => { placed[m.id] = 1; }));
  const loose = S.runs.filter(r => r.repo === repo && r.running && !placed[r.id]);
  const merged = stubs.filter(s => s.state === 'merged');
  const kids = [];
  if (loose.length) kids.push(h('div', { class: 'scol-sec', 'data-k': 'loose:' + repo }, [h('h3', {}, ['In progress']), h('ul', { class: 'tree' }, loose.map(r => subItem(repo, r.id, false, false)))]));
  kids.push(h('h3', {}, ['Open stubs']));
  kids.push.apply(kids, os.length ? os.map(s => stubTree(repo, s)) : [h('div', { class: 'empty' }, ['No open stub for this repo.'])]);
  const dfd = S.runs.filter(r => r.repo === repo && r.deferredOpen > 0).sort((a, b) => b.deferredOpen - a.deferredOpen);
  if (dfd.length) kids.push(h('div', { class: 'scol-sec', 'data-k': 'dfd:' + repo }, [h('h3', {}, ['Deferred · ' + dfd.reduce((n, r) => n + r.deferredOpen, 0) + ' open']),
    h('ul', { class: 'tree' }, dfd.map(r => h('li', {}, [h('a', { class: 'sub-item', href: hrefItem(repo, r.id) }, [h('span', { class: 'sid mono' }, [r.id]), h('span', { class: 'sname', title: r.title }, [r.title]), chip(r.deferredOpen + ' open', 'ready')])])))]));
  if (merged.length) kids.push(h('details', { class: 'mstubs', 'data-k': 'mstubs:' + repo }, [h('summary', {}, ['Merged stubs (' + merged.length + ')']),
    h('ul', { class: 'tree' }, merged.map(s => h('li', {}, [h('a', { class: 'sub-item', href: hrefStub(repo, s.branch) }, [h('span', { class: 'sid mono' }, [s.branch]), h('span', { class: 'sname', title: s.branch }, [s.dossiers.length + ' dossier(s)'])])])))]));
  return h('aside', { class: 'scol' }, kids);
}
function repoView(repo) {
  const rs = S.runs.filter(r => r.repo === repo); const prog = rs.filter(inProgress); const os = openStubs(repo);
  const ow = (S.stubs.openWork || []).find(o => o.repo === repo); const oq = S.q.filter(q => q.state === 'open' && qRepo(q) === repo).sort((a, b) => a.ts.localeCompare(b.ts));
  const kids = [h('h1', {}, [rlabel(repo)]), h('p', { class: 'lead' }, [prog.length + ' running · ' + oq.length + ' open question' + (oq.length === 1 ? '' : 's') + ' · ' + os.length + ' open stub' + (os.length === 1 ? '' : 's')])];
  if (oq.length) kids.push(section('Waiting on you', oq.length, h('div', {}, oq.map(q => qCard(q, { thread: true }))), 'oq'));
  const frame = ow ? h('iframe', { class: 'report', 'data-repo': repo, src: '/open-work/' + enc(repo) + '?theme=dark&v=' + enc(ow.updated), title: 'Open work', scrolling: 'no', style: S.frameH[repo] ? 'height:' + S.frameH[repo] + 'px' : null }) : h('div', { class: 'empty' }, ['The open-work report is not generated yet.']);
  kids.push(h('div', { class: 'cols', 'data-k': 'cols' }, [h('section', { class: 'main-col', 'data-k': 'sec:report' }, [h('h2', {}, ['Open work']), frame]), sideColumn(repo)]));
  return h('div', {}, kids);
}
function header(r, mode) {
  const nq = openQs(r); const cs = [chip(r.status)];
  if (r.runner) cs.push(chip('runner ' + r.runner.state, r.runner.state));
  const st = stageText(r); if (st) cs.push(chip(st, 'neutral'));
  if (r.session && r.session.context) cs.push(chip('ctx ' + kfmt(r.session.context), 'neutral'));
  if (r.pr) cs.push(prChip(r.pr)); if (r.jira && r.jira !== 'null') cs.push(chip(r.jira, 'neutral'));
  nq.forEach(q => cs.push(h('a', { class: 'chip warn', href: '/questions#q=' + enc(q.id) }, ['? open question'])));
  const acts = [];
  if (mode === 'run') acts.push(h('a', { class: 'btn', href: hrefItem(r.repo, r.id) + '/dossier' }, ['View dossier']));
  else if (r.updates.length || inProgress(r)) acts.push(h('a', { class: 'btn', href: hrefItem(r.repo, r.id) }, ['Back to run']));
  return h('div', { class: 'panel' }, [h('h1', { style: 'margin-top:0' }, [r.id + ' · ' + r.title]), r.summary && mode === 'run' ? h('p', { class: 'sum' }, [r.summary]) : null,
    h('div', { class: 'chips' }, cs), h('div', { class: 'row' }, controls(r, true).concat(acts))]);
}
const ICON = { start: '▶', stage: '✔', question: '?', answered: '✔', 'suite-green': '●', 'suite-red': '✖', gate: '▣', pr: '⇄', workflow: '⚙', failed: '✖', waiting: '⏳', done: '★', stopped: '■', note: '•', spawn: '⎇', reply: '↩', routine: '·', info: '·', entry: '§' };
const KLABEL = { start: 'run started', stage: 'stage done', question: 'question asked', answered: 'answered', 'suite-green': 'suite green', 'suite-red': 'suite red', gate: 'gate', pr: 'pull request', workflow: 'workflow', spawn: 'agent', failed: 'failed', waiting: 'waiting', done: 'done', stopped: 'stopped', note: 'update', reply: 'reply', routine: 'routine', info: 'runner' };
function dur(ms) { const m = Math.round(ms / 60000); if (m < 1) return '<1 min'; if (m < 60) return m + ' min'; const hh = Math.floor(m / 60); if (hh < 24) return hh + ' h ' + (m % 60 ? (m % 60) + ' min' : ''); return Math.floor(hh / 24) + ' d ' + (hh % 24) + ' h'; }
const icon = (k) => h('span', { class: 'ic' }, [ICON[k] || '•']);
function tlEntry(e) {
  const li = tlItem({ kind: e.type === 'answer' ? 'answered' : e.type === 'question' ? 'question' : e.type === 'message' ? 'reply' : 'info', key: false, entry: e }); return li;
}
const ekey = (e) => 'e:' + e.type + '|' + e.ts + '|' + (e.qid || '') + '|' + (e.label || '') + '|' + e.text.slice(0, 40);
function tlItem(it) {
  const e = it.entry; const k = it.kind;
  const cls = e.type + (e.auto ? ' auto' : '') + ' ev k-' + k + (it.key ? ' key' : '') + (it.kind === 'routine' || it.kind === 'info' ? ' rt' : '');
  const dk = ekey(e);
  if (e.type === 'boundary') return h('li', { class: 'boundary', 'data-k': 'b:' + e.ts + e.label }, [h('div', { class: 'bound' }, [e.label]), e.text ? h('div', { class: 'tx mut' }, [e.text]) : null]);
  if (e.type === 'question') {
    const q = S.q.find(x => x.id === e.qid) || e.question;
    if (q.state === 'open') return h('li', { class: cls, 'data-k': dk }, [h('div', { class: 'hd' }, [icon(k), h('span', { class: 'kind' }, ['question asked · waiting for you']), when(e.ts)]), h('div', { class: 'tx' }, [e.text])]);
    return h('li', { class: cls, 'data-k': dk }, [qCard(q, { thread: false })]);
  }
  if (e.type === 'message' || e.type === 'answer') {
    const who = e.type === 'answer' ? 'Answer' + (e.option ? ' ' + e.option : '') : e.from + ' ' + e.kind;
    return h('li', { class: cls, 'data-k': dk }, [h('div', { class: 'hd' }, [icon(k), h('span', { class: 'kind' }, [who + (e.via ? ' via ' + e.via : '')]), when(e.ts)]), h('div', { class: 'tx' }, [e.text])]);
  }
  if (e.type === 'entry') {
    const head = h('div', { class: 'hd' }, [icon('entry'), when(e.ts) || (e.ts ? h('span', {}, [e.ts]) : null)]);
    if (!e.text) return h('li', { class: cls, 'data-k': dk }, [head, h('div', { class: 'tx' }, [e.label])]);
    return h('li', { class: cls, 'data-k': dk }, [head, h('details', { 'data-k': 'd:' + e.ts + e.label }, [h('summary', {}, [e.label]), h('div', { class: 'tx' }, [e.text])])]);
  }
  if (e.event === 'spawn') return h('li', { class: cls, 'data-k': dk }, [h('div', { class: 'hd' }, [icon(k), h('span', { class: 'kind' }, ['agent']), chip(e.status || 'unknown'), e.durationMs !== undefined ? h('span', { class: 'chip' }, [dur(e.durationMs)]) : null, when(e.ts)]), h('div', { class: 'tx' }, [e.text.split('\n')[0]])]);
  return h('li', { class: cls, 'data-k': dk }, [h('div', { class: 'hd' }, [icon(k), h('span', { class: 'kind' }, [KLABEL[k] || e.type]), e.stage && e.type === 'stage' ? h('span', {}, [e.stage]) : null, when(e.ts)]), h('div', { class: 'tx' }, [e.text])]);
}
function itemLi(it, gk) {
  if (it.kind !== 'more') return tlItem(it);
  const n = it.entries.length;
  return h('li', { class: 'more', 'data-k': 'more:' + gk + it.entries[0].entry.ts + n }, [h('details', {}, [h('summary', {}, ['+' + n + ' routine update' + (n > 1 ? 's' : '') + ' · ', when(it.entries[0].entry.ts)]),
    h('ol', {}, it.entries.map(x => h('li', { 'data-k': ekey(x.entry) }, [h('span', { class: 'mut' }, [(when(x.entry.ts) || h('span')), ' ']), x.entry.text.split('\n')[0]])))])]);
}
function phaseGroup(g, key) {
  const c = g.counts; const bits = []; if (c.questions) bits.push(c.questions + ' question' + (c.questions > 1 ? 's' : '')); bits.push(c.updates + ' update' + (c.updates === 1 ? '' : 's'));
  const head = h('summary', {}, [h('span', { class: 'pl' }, [g.label]), g.current ? chip('current', 'info') : (g.durationMs !== null ? h('span', { class: 'chip' }, [dur(g.durationMs)]) : null), g.summary ? h('span', { class: 'ps' }, [g.summary]) : null, h('span', { class: 'pc' }, [bits.join(' · ')])]);
  const gk = key + g.label;
  return h('details', { class: 'ph' + (g.current ? ' cur' : ''), 'data-k': 'ph:' + gk, 'data-cur': g.current ? '1' : '', open: g.current }, [head, h('ol', { class: 'tl' }, g.items.map(it => itemLi(it, gk)))]);
}
function groupsView(gs, key, emptyText) {
  if (!gs || !gs.length || gs.every(g => !g.items.length && !g.label)) return h('div', { class: 'empty', 'data-k': 'empty' }, [emptyText]);
  return h('div', {}, gs.map(g => phaseGroup(g, key)));
}
function stepper(n, names) {
  const out = []; for (let i = 0; i <= 9; i++) out.push(h('span', { class: 'st' + (n === i ? ' cur' : n !== null && i < n ? ' done' : ''), title: 'Phase ' + i + (names && names[String(i)] ? ' — ' + names[String(i)] : '') }, [n === i ? 'Phase ' + i : String(i)]));
  return h('div', { class: 'stepper', 'aria-label': 'Phase ' + (n === null ? '?' : n) + ' of 9' }, out);
}
function nowView(d, runner, withStepper) {
  const n = d.now; const kids = [];
  const waiting = n.waiting.map(id => S.q.find(q => q.id === id)).filter(q => q && q.state === 'open');
  if (waiting.length) kids.push(h('div', { class: 'waitbox' }, [h('h3', {}, ['Waiting on you · ' + waiting.length + ' question' + (waiting.length > 1 ? 's' : '')])].concat(waiting.map(q => qCard(q, { thread: true })))));
  kids.push(h('div', { class: 'lbl' }, ['Now']));
  const head = [h('span', {}, [n.phase || 'No phase recorded yet'])];
  if (n.since && Date.parse(n.since)) head.push(h('span', { class: 'chip' }, ['in this phase for ' + dur(Date.now() - Date.parse(n.since))]));
  if (runner) head.push(chip('runner ' + runner.state, runner.state));
  kids.push(h('div', { class: 'ph1' }, head));
  if (withStepper) kids.push(stepper(n.step, d.phaseNames));
  if (n.doing) kids.push(h('div', { class: 'doing' }, [icon(n.doing.kind), h('div', {}, [h('div', { class: 'tx' }, [n.doing.text.split('\n')[0]]), h('div', { class: 'small mut' }, [when(n.doing.ts)])])]));
  else kids.push(h('div', { class: 'small mut' }, ['Nothing posted yet.']));
  if (!waiting.length) kids.push(h('div', { class: 'nowait' }, ['Nothing is waiting on you.']));
  return h('div', { class: 'now', 'data-k': 'now' }, kids);
}
function tabs(key, list) {
  const cur = S.tab[key] || list[0][0];
  return h('div', { class: 'tabs', role: 'tablist', 'data-k': 'tabs' }, list.map(t => h('button', { class: 'tab' + (cur === t[0] ? ' on' : ''), type: 'button', role: 'tab', 'data-act': 'tab', 'data-key': key, 'data-tab': t[0] }, [t[1]])));
}
function itemView(repo, id) {
  const r = findRun(repo, id);
  if (!r) return h('div', {}, [h('h1', {}, [id]), h('div', { class: 'empty' }, [S.loaded ? 'No such dossier in ' + repo + '.' : 'Loading…'])]);
  const mode = itemMode(r); const d = S.detail && S.detail.url === detailUrl() ? S.detail.data : null;
  const kids = [header(r, mode)];
  if (mode === 'run') {
    const key = repo + '/' + id; const tab = S.tab[key] || 'timeline';
    kids.push(tabs(key, [['timeline', d && d.planning ? 'Planning' : 'Timeline'], ['activity', 'Activity'], ['log', 'Build log' + (d && d.log ? ' (' + d.log.length + ')' : '')]]));
    if (!d || !d.groups) kids.push(h('div', { class: 'empty' }, ['Loading…']));
    else if (tab === 'log') kids.push(d.log.length ? h('ol', { class: 'tl', 'data-k': 'log' }, d.log.slice().reverse().map((l, i) => h('li', { class: 'log', 'data-k': 'l:' + i + l.slice(0, 30) }, [h('div', { class: 'tx' }, [l])]))) : h('div', { class: 'empty' }, ['The dossier has no build log yet.']));
    else if (tab === 'activity') kids.push(groupsView(d.activity, key + '#a', 'No automatic activity yet'));
    else { kids.push(nowView(d, r.runner, true)); kids.push(groupsView(d.groups, key, 'No updates yet — the agent posts here as the run progresses')); }
  } else {
    const html = d && d.html ? d.html : '';
    kids.push(h('div', { class: 'panel md', 'data-raw': '1', 'data-sig': html.length + ':' + html.slice(0, 40) + html.slice(-40), 'data-html': html }, []));
    if (r.answered.length) kids.push(section('Answered questions', r.answered.length, h('div', {}, r.answered.map(a => h('div', { class: 'panel qa', 'data-k': 'qa:' + a.id }, [
      h('div', {}, ['Q: ' + a.text]), h('div', { class: 'a' }, ['A: ' + (a.option ? a.option + ' · ' : '') + a.answer + ' · ', when(a.answeredAt || a.asked)]), h('a', { class: 'small', href: '/questions#q=' + enc(a.id) }, ['thread'])]))), 'qa'));
  }
  if (r.deferred && r.deferred.items.length) kids.push(deferredView(r));
  return h('div', {}, kids);
}
/* The deferred ledger: open items with a copyable /plan seed, fixed ones with their
   commit, the ones routed to a new dossier; "Fix all deferred" sits in the header. */
function deferredView(r) {
  const df = r.deferred; const open = df.items.filter(i => i.state === 'open').length;
  const rows = df.items.map(i => h('div', { class: 'df-row', 'data-k': 'df:' + i.id }, [
    h('span', { class: 'mono b' }, [i.id]), chip(i.kind, 'neutral'),
    h('span', { class: 'df-t', title: [i.title, i.loc, i.risk, i.why].filter(Boolean).join(' — ') }, [i.title]),
    i.loc ? h('code', { class: 'df-loc' }, [i.loc]) : null,
    i.state === 'fixed' ? chip('fixed' + (i.note ? ' ' + i.note.slice(0, 8) : ''), 'done')
      : i.state === 'dossier' ? chip('→ new dossier', 'planned') : chip('open', 'ready'),
    i.state !== 'fixed' ? h('button', { class: 'btn sm', type: 'button', 'data-act': 'copy', 'data-text': '/plan ' + r.id + ' ' + i.id, title: 'Copy the /plan seed for this item' }, ['Plan']) : null,
  ]));
  const links = [];
  if (df.report) links.push(h('a', { class: 'small', href: '/deferred/' + enc(r.repo) + '/' + enc(r.id), target: '_blank', rel: 'noopener' }, ['full report']));
  if (df.pr) links.push(h('a', { class: 'small', href: df.pr, target: '_blank', rel: 'noopener' }, ['stacked PR']));
  return section('Deferred issues', open + ' open of ' + df.items.length, h('div', { class: 'panel' }, [links.length ? h('div', { class: 'ctl' }, links) : null, ...rows]), 'deferred');
}
function stubView(repo, branch) {
  const rr = S.stubs.repos.find(x => x.repo === repo); const s = rr && rr.stubs.find(x => x.branch === branch);
  if (!s) return h('div', {}, [h('h1', {}, [branch]), h('div', { class: 'empty' }, [S.loaded ? 'No such stub.' : 'Loading…'])]);
  const d = S.detail && S.detail.url === detailUrl() ? S.detail.data : null;
  const cs = [chip(s.state), chip('cut ' + s.cut, 'neutral')]; if (s.stage) cs.push(chip(s.stage, 'neutral')); if (s.pr) cs.push(prChip(s.pr));
  const impl = s.dossiers.length ? h('div', { class: 'mini' }, s.dossiers.map(x => h('div', { class: 'card', 'data-k': 'i:' + x.id }, [
    h('a', { class: 'stretch title', href: hrefItem(x.repo || repo, x.id) }, [h('span', { class: 'id' }, [x.id + ' ']), x.title]), h('div', { class: 'chips' }, [chip(x.status || '?')])]))) : h('div', { class: 'empty' }, ['No dossier is implemented on this stub yet.']);
  const key = repo + '/' + branch; const tab = S.tab[key] || 'timeline'; const body = [];
  body.push(tabs(key, [['timeline', 'Timeline'], ['activity', 'Activity'], ['record', 'Record']]));
  if (!d || !d.groups) body.push(h('div', { class: 'empty' }, ['Loading…']));
  else if (tab === 'record') body.push(d.record.length ? h('ol', { class: 'tl', 'data-k': 'rec' }, d.record.slice().reverse().map(tlEntry)) : h('div', { class: 'empty' }, [d.hasRecord ? 'The record file has no sections.' : 'No record file for this stub.']));
  else if (tab === 'activity') body.push(groupsView(d.activity, key + '#a', 'No automatic activity yet'));
  else { body.push(nowView(d, null, false)); body.push(groupsView(d.groups, key, 'No updates yet — the agent posts here as the stub progresses')); }
  return h('div', {}, [h('div', { class: 'panel' }, [h('h1', { class: 'mono', style: 'margin-top:0' }, [branch]), h('div', { class: 'chips' }, cs), h('div', { class: 'row small mut' }, [s.refAge ? when(s.refAge, 'ref updated ') : null]),
    h('h2', {}, ['Implemented dossiers', h('span', { class: 'n' }, [String(s.dossiers.length)])]), impl])].concat(body));
}

// ---- header, crumbs ----
function crumbs() {
  const out = []; const add = (t, href) => { if (out.length) out.push(h('span', {}, ['›'])); out.push(href ? h('a', { href }, [t]) : h('span', { class: 'cur' }, [t])); };
  add('Home', R.kind === 'home' ? null : '/');
  if (R.kind === 'questions') add('Open questions');
  if (R.repo) add(rlabel(R.repo), R.kind === 'repo' ? null : hrefRepo(R.repo));
  if (R.kind === 'item') add(R.id, R.forceDossier ? hrefItem(R.repo, R.id) : null);
  if (R.kind === 'item' && R.forceDossier) add('Dossier');
  if (R.kind === 'stub') add(R.branch);
  return out;
}
function detailUrl() {
  if (R.kind === 'stub') return '/api/stub?repo=' + enc(R.repo) + '&branch=' + enc(R.branch);
  if (R.kind === 'item') { const r = findRun(R.repo, R.id); if (!r) return null; return (itemMode(r) === 'run' ? '/api/timeline/' : '/api/dossier/') + enc(R.repo) + '/' + enc(R.id); }
  return null;
}
function build() {
  if (!S.loaded) return h('div', { class: 'empty' }, ['Loading…']);
  if (R.kind === 'questions') return questionsView(); if (R.kind === 'repo') return repoView(R.repo);
  if (R.kind === 'item') return itemView(R.repo, R.id); if (R.kind === 'stub') return stubView(R.repo, R.branch);
  return homeView();
}

// ---- keep scroll, expanders and typed text: patch the live DOM instead of replacing it ----
function sameNode(a, b) { return a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName); }
function syncAttrs(o, n) {
  Array.from(o.attributes).forEach(a => { if (!n.hasAttribute(a.name) && !(o.tagName === 'DETAILS' && a.name === 'open')) o.removeAttribute(a.name); });
  Array.from(n.attributes).forEach(a => { if (o.tagName === 'DETAILS' && a.name === 'open') return; if (o.getAttribute(a.name) !== a.value) o.setAttribute(a.name, a.value); });
}
function patchNode(o, n) {
  if (o.nodeType !== 1) { if (o.nodeValue !== n.nodeValue) o.nodeValue = n.nodeValue; return; }
  if (o.tagName === 'TEXTAREA') { syncAttrs(o, n); return; }
  if (o.tagName === 'DETAILS' && o.getAttribute('data-cur') === '1' && n.getAttribute('data-cur') !== '1') o.removeAttribute('open');
  const changed = o.getAttribute('data-html') !== n.getAttribute('data-html');
  syncAttrs(o, n);
  if (o.hasAttribute('data-raw')) { if (changed) o.innerHTML = n.getAttribute('data-html') || ''; return; }
  morph(o, n);
}
function morph(op, np) {
  const olds = Array.from(op.childNodes), keyed = new Map(), used = new Set(), res = [];
  olds.forEach(x => { if (x.nodeType === 1 && x.dataset.k) keyed.set(x.dataset.k, x); });
  let idx = 0;
  Array.from(np.childNodes).forEach(nn => {
    let on = null; const k = nn.nodeType === 1 && nn.dataset.k;
    if (k) on = keyed.get(k) || null;
    else { while (idx < olds.length && (used.has(olds[idx]) || (olds[idx].nodeType === 1 && olds[idx].dataset.k))) idx++; if (idx < olds.length) on = olds[idx++]; }
    if (on && !used.has(on) && sameNode(on, nn)) { used.add(on); patchNode(on, nn); res.push(on); } else res.push(nn);
  });
  olds.forEach(x => { if (!used.has(x)) x.remove(); });
  res.forEach((x, i) => { if (op.childNodes[i] !== x) op.insertBefore(x, op.childNodes[i] || null); });
}

function render() {
  const sig = R.kind + '|' + (R.repo || '') + '|' + (R.id || '') + '|' + (R.branch || '') + '|' + (R.forceDossier ? 'd' : '');
  const view = document.getElementById('view'); const fresh = document.createElement('div'); fresh.appendChild(build());
  if (sig !== routeSig) { routeSig = sig; view.replaceChildren(); window.scrollTo(0, 0); }
  morph(view, fresh);
  const cr = document.createElement('div'); crumbs().forEach(c => cr.appendChild(c)); morph(document.getElementById('crumbs'), cr);
  const sl = document.createElement('div'); sl.appendChild(h('span', { class: 'dot ' + (S.slack ? 'ok' : '') })); sl.appendChild(h('span', {}, [S.slack ? 'Slack connected' : 'Slack off'])); morph(document.getElementById('slack'), sl);
  const up = document.createElement('div'); up.appendChild(h('span', { class: 'dot ' + (S.live ? 'ok' : 'warn'), title: S.live ? 'live updates' : 'polling every 60 s' }));
  const t = new Date(S.changedAt || Date.now()).toISOString(); up.appendChild(when(t, 'updated ') || h('span')); morph(document.getElementById('upd'), up);
  document.title = (R.kind === 'item' ? R.id + ' · ' : R.kind === 'repo' ? R.repo + ' · ' : '') + 'Pipeline · Owner questions';
  if (scrollTarget) { const e = document.getElementById('q-' + scrollTarget); if (e) { e.scrollIntoView({ block: 'start' }); scrollTarget = ''; } }
}

// ---- data ----
async function fetchJ(u) { try { const r = await fetch(u); return r.ok ? await r.json() : null; } catch (e) { return null; } }
async function loadDetail() {
  const u = detailUrl(); if (!u) { S.detail = null; return; }
  const d = await fetchJ(u); if (d && detailUrl() === u) S.detail = { url: u, data: d };
}
async function load(what) {
  const all = !what; const w = new Set(what || []); const jobs = [];
  if (all || w.has('questions')) jobs.push(fetchJ('/api/questions?state=all').then(d => { if (Array.isArray(d)) S.q = d; }));
  if (all || w.has('runs') || w.has('scan')) jobs.push(fetchJ('/api/runs').then(d => { if (d && d.runs) { S.runs = d.runs; S.slack = d.slack; S.scannedAt = d.scannedAt; } }));
  if (all || w.has('stubs') || w.has('scan')) jobs.push(fetchJ('/api/stubs').then(d => { if (d && d.repos) S.stubs = d; }));
  await Promise.all(jobs);
  await loadDetail();
  S.loaded = true; S.changedAt = Date.now(); render();
}
let sse = null;
function connect() {
  if (!window.EventSource) return;
  try { sse = new EventSource('/api/events'); } catch (e) { return; }
  sse.onopen = () => { S.live = true; render(); };
  sse.addEventListener('changed', (ev) => { let w = []; try { w = JSON.parse(ev.data).what || []; } catch (e) {} load(w.length ? w : null); });
  sse.onerror = () => { S.live = false; render(); };
}
function navigate(href, replace) {
  const u = new URL(href, location.href);
  if (replace) history.replaceState(null, '', u.pathname + u.search + u.hash); else history.pushState(null, '', u.pathname + u.search + u.hash);
  onRoute();
}
function onRoute() {
  if (location.pathname === '/' && hashQ()) { history.replaceState(null, '', '/questions#q=' + enc(hashQ())); }
  if (/^\/repo\/[^/]+\/graph\/?$/.test(location.pathname)) history.replaceState(null, '', location.pathname.replace(/\/graph\/?$/, ''));
  R = parseRoute(); scrollTarget = R.kind === 'questions' ? hashQ() : ''; S.detail = null;
  render(); load(null);
}

// ---- actions (delegated, so patched nodes keep working) ----
async function post(id, body) {
  S.qerr[id] = '';
  try {
    const r = await fetch('/api/questions/' + enc(id) + '/messages', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) S.qerr[id] = (await r.json().catch(() => ({}))).error || ('HTTP ' + r.status);
  } catch (e) { S.qerr[id] = 'request failed'; }
  await load(['questions']);
}
async function control(btn) {
  const a = btn.dataset.ctl, repo = btn.dataset.repo, id = btn.dataset.id, key = repo + '/' + id; const st = S.ctl[key] = S.ctl[key] || {};
  if (st.confirm !== a) { st.confirm = a; clearTimeout(st.t); st.t = setTimeout(() => { if (st.confirm === a) { st.confirm = null; render(); } }, 8000); render(); return; }
  clearTimeout(st.t); st.confirm = null; st.busy = a; st.msg = ''; render();
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/' + a, { method: 'POST' });
    const j = await r.json().catch(() => ({})); st.msg = j.output || j.error || ('HTTP ' + r.status); st.ok = r.ok && j.ok !== false;
  } catch (e) { st.msg = 'request failed'; st.ok = false; }
  st.busy = null; await load(['runs']);
}
document.addEventListener('click', (e) => {
  const t = e.target.closest ? e.target.closest('[data-act]') : null;
  if (t) {
    e.preventDefault(); const act = t.dataset.act;
    if (act === 'opt') post(t.dataset.qid, { kind: 'answer', option: t.dataset.opt });
    else if (act === 'followup' || act === 'answer') {
      const ta = t.closest('.need').querySelector('textarea'); if (ta && ta.value.trim()) { const v = ta.value; ta.value = ''; post(t.dataset.qid, { kind: act, text: v }); }
    } else if (act === 'ctl') control(t);
    else if (act === 'copy') { try { navigator.clipboard.writeText(t.dataset.text); t.textContent = 'Copied'; setTimeout(() => { t.textContent = 'Plan'; }, 1500); } catch (_) {} }
    else if (act === 'ctl-cancel') { const s = S.ctl[t.dataset.repo + '/' + t.dataset.id]; if (s) s.confirm = null; render(); }
    else if (act === 'tab') { S.tab[t.dataset.key] = t.dataset.tab; render(); }
    else if (act === 'toggle-done') { S.showAll[t.dataset.repo] = !S.showAll[t.dataset.repo]; render(); }
    return;
  }
  const a = e.target.closest ? e.target.closest('a[href]') : null;
  if (!a || a.target === '_blank' || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
  const h2 = a.getAttribute('href');
  if (h2 && (h2 === '/' || h2.indexOf('/questions') === 0 || h2.indexOf('/repo/') === 0)) { e.preventDefault(); navigate(h2); }
});
window.addEventListener('popstate', onRoute);
window.addEventListener('hashchange', () => { if (R.kind === 'questions') { scrollTarget = hashQ(); render(); } else onRoute(); });
setInterval(() => { if (!S.live) load(null); }, 60000);   // fallback while the event stream is down
setInterval(() => { if (!document.hidden) render(); }, 30000);
onRoute(); connect();
</script></body></html>`
