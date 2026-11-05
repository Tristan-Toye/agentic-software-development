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
.df-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-top:1px solid var(--bd);min-width:0}.df-row:first-of-type{border-top:0}.df-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.df-loc{font-size:11px;color:var(--mut);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.btn.sm{padding:2px 8px;font-size:12px}.btn.ghost{background:transparent;border-color:var(--bd);color:var(--mut)}.btn.ghost:hover{color:var(--fg)}.tx.md p{margin:.2em 0}.tx.md ul,.tx.md ol{margin:.2em 0 .2em 1.2em}.tx.md table{border-collapse:collapse;margin:.3em 0}.tx.md th,.tx.md td{border:1px solid var(--bd);padding:2px 8px;text-align:left}.b{font-weight:700}.ctl-msg.ok{color:var(--ok)}
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
.sec{margin:18px 0}.sechd{display:flex;align-items:center;gap:10px;margin:0 0 8px}.sechd h2{margin:0}.sechd .grow{flex:1}
.agrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}.acard{border:1px solid var(--bd);border-radius:12px;background:var(--card);padding:12px 14px;display:flex;flex-direction:column;gap:8px}.acard.next{border-color:var(--acc)}
.ahd{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.aname{font-weight:700;font-size:1rem;margin-right:4px}.meter .mhd{display:flex;justify-content:space-between;font-size:12.5px}.meter .ml{color:var(--mut)}.meter .mv{font-weight:600}
.mbar{height:7px;border-radius:4px;background:var(--chip);overflow:hidden;margin:3px 0}.mfill{display:block;height:100%;background:var(--acc)}.mfill.ok{background:var(--ok)}.mfill.warn{background:var(--warn)}.mfill.bad{background:var(--bad)}
.dfplan{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}.nowline{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0 0;color:var(--fg)}.nowline .nowtext{display:inline-flex;gap:6px;align-items:center;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}a.srow .nowline{margin:2px 0 0;color:var(--mut)}.nowline .chip{font-size:11px}.rsub{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}a.srow .sname>span:first-child{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip.fixing{display:inline-flex;align-items:center;gap:5px;background:var(--accbg);color:var(--acc);font-weight:600}a.srow:has(.chip.fixing){box-shadow:inset 3px 0 0 var(--acc)}
.dfp{border-left:3px solid var(--acc)}.dfp-bar{display:flex;height:9px;border-radius:5px;overflow:hidden;background:var(--chip);margin:6px 0 4px}.dfp-bar .seg{display:block;height:100%}
.seg.ok,.sw.ok{background:var(--ok)}.seg.acc,.sw.acc{background:var(--acc)}.seg.warn,.sw.warn{background:var(--warn)}.seg.mut,.sw.mut{background:var(--mut)}.seg.mut2,.sw.mut2{background:var(--bd)}
.dfp-leg{display:flex;gap:12px;flex-wrap:wrap;color:var(--mut)}.dfp-leg .sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.dfp-rows{margin-top:8px;border:1px solid var(--bd);border-radius:10px;overflow:hidden}.dfp-row{display:grid;grid-template-columns:max-content minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 12px;border-top:1px solid var(--bd);font-size:13px}.dfp-row:first-child{border-top:0}.dfp-t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.panel.blocked{border-left:3px solid var(--bad)}.blk{border-top:1px solid var(--bd);padding:10px 0 4px;display:flex;flex-direction:column;gap:6px}.blk-cmd{margin:0;padding:8px 10px;border-radius:8px;background:var(--chip);font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto}
.blk-rule{display:flex;gap:6px;flex-wrap:wrap}.blk-rule input{flex:1;min-width:220px;font-size:12px;padding:6px 8px;border:1px solid var(--bd);border-radius:8px;background:var(--bg);color:var(--fg)}
.psum{border-left:3px solid var(--acc)}.psum h3{font-size:.95rem;margin:12px 0 4px}.psum .md p:first-child{margin-top:0}
.result .res-hd{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--mut);margin-bottom:4px}.result .res-hd .kind{font-weight:600;color:var(--fg)}
.chat{margin:10px 0 0;padding:8px 12px;border-radius:10px;background:var(--chip);overflow-wrap:anywhere}.chat.owner{margin-left:12%;background:var(--accbg)}.chat.agent,.chat.runner{margin-right:6%}.chat.runner{color:var(--mut)}.chat .md p:first-child{margin-top:0}.chat .md p:last-child{margin-bottom:0}
.cbx{width:18px;height:18px;padding:0;border:1px solid var(--bd);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;line-height:16px;cursor:pointer}.cbx.on{background:var(--acc);border-color:var(--acc);color:#fff}.dfbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 12px;border:1px solid var(--bd);border-radius:12px;background:var(--card);position:sticky;top:8px;z-index:2;box-shadow:var(--shadow)}.dfbar .grow{flex:1}.dfnote{margin:8px 2px 12px}.dfcards{display:flex;flex-direction:column;gap:10px}.dfc{border:1px solid var(--bd);border-radius:12px;background:var(--card);padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0}.dfc.pick{cursor:pointer}.dfc.pick:hover{border-color:var(--acc)}.dfc.on{border-color:var(--acc);background:var(--accbg)}.dfc.closed{opacity:.75}.dfc .cbx{pointer-events:none;flex:none}.dfc-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.dfc-h .grow{flex:1}.dfc-t{font-weight:600;line-height:1.35;overflow-wrap:anywhere}.dfc-loc{font-size:12px;color:var(--mut);overflow-wrap:anywhere;white-space:normal}.dfc-f{display:flex;gap:6px;flex-wrap:wrap}.dfact{font-size:12px;border:1px solid var(--bd);border-radius:999px;padding:1px 9px}.dfact .l{color:var(--mut);margin-right:5px}.dfact.bad{border-color:var(--bad);color:var(--bad)}.dfact.mut{color:var(--mut)}.dfc-why{font-size:12.5px;color:var(--mut);line-height:1.4}.dfc-a{display:flex;justify-content:flex-end}
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
.shead{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}.shead .chips{display:flex;gap:6px;flex-wrap:wrap}.sgrps{display:flex;flex-direction:column;gap:12px;margin-top:8px}.sgrp-h{font-size:12px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.03em;margin:0 0 5px;cursor:default}details.sgrp>summary.sgrp-h{cursor:pointer}.srows{border:1px solid var(--bd);border-radius:10px;overflow:hidden}a.srow{display:grid;grid-template-columns:max-content minmax(0,1fr) auto;gap:12px;align-items:center;padding:8px 12px;border-top:1px solid var(--bd);color:var(--fg);text-decoration:none;font-size:13px}a.srow:first-child{border-top:0}a.srow:hover{background:var(--accbg)}a.srow.done{color:var(--mut)}a.srow .sid{font-weight:700}a.srow .sname{color:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}a.srow.done .sname{color:var(--mut)}.sright{display:flex;gap:6px;align-items:center;justify-content:flex-end;flex-wrap:wrap}.sright>.chip{font-size:11px}@media (max-width:640px){a.srow{grid-template-columns:max-content minmax(0,1fr)}.sright{grid-column:1/-1;justify-content:flex-start}}.scol.full{margin:22px 0}.scol.full>h2{margin:0 0 10px}.scol.full .scard{padding:14px 18px}.scol.full .scard .title{font-size:1rem}
ul.tree.wide{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));column-gap:18px;border-left:0;padding-left:0}ul.tree.wide>li{padding-left:0}ul.tree.wide>li::before{display:none}
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
const S = { q: [], runs: [], accounts: [], usageBusy: false, slack: false, scannedAt: '', stubs: { repos: [], openWork: [] }, loaded: false, detail: null, live: false, changedAt: 0, showAll: {}, ctl: {}, qerr: {}, tab: {}, frameH: {}, dsel: {}, chat: {} };
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
/* The /work-on phase; a run that has not reached its first checkpoint yet is "starting", a dossier nobody runs shows none. */
const CMD_DOING = { 'fix-deferred': 'Fixing deferred issues' };
function stageText(r) {
  const live = r.runner && ['running', 'waiting', 'paused'].includes(r.runner.state);
  if (live && r.runner.command && CMD_DOING[r.runner.command]) return CMD_DOING[r.runner.command];
  if (!r.phase || r.phase === 'plan') return live ? 'Starting' : '';
  return 'Phase ' + r.phase + (r.phaseName ? ' · ' + r.phaseName : '');
}
/* The run's state in plain words, no "runner" jargon. */
const RUN_STATE = { running: 'in progress', waiting: 'waiting for you', paused: 'paused · usage limit', stopped: 'stopped', failed: 'failed', done: 'done' };
const runChip = (st) => chip(RUN_STATE[st] || st, st);
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
  const who = l.accepted ? 'agent · took your words as the answer' : l.from === 'owner' && l.kind === 'followup' ? 'you' : l.from + ' ' + l.kind;
  return h('div', { class: 'msg ' + l.from }, [h('div', { class: 'who' }, [who + (l.option ? ' ' + l.option : '') + (l.via ? ' via ' + l.via : '') + ' · ', when(l.ts)]), h('div', {}, [l.text])]);
}
function qCard(q, opts) {
  const open = q.state === 'open'; const repo = qRepo(q);
  const kids = [
    h('div', { class: 'top' }, [chip(rlabel(repo), 'neutral'), h('a', { class: 'chip', href: hrefItem(repo, q.dossier) }, [q.dossier]), h('span', { class: 'grow' }), h('span', { class: 'mono' }, [q.id + ' · ']), opts.queue ? when(q.ts, 'waiting ') : when(q.ts)]),
    h('div', { class: 'q' }, [q.text]), q.blocks ? h('div', { class: 'blocks' }, ['Blocks: ' + q.blocks]) : null, optButtons(q)];
  if (opts.thread) kids.push(h('div', {}, q.lines.slice(1).map(msgBlock)));
  if (open) kids.push(h('textarea', { placeholder: 'Write your answer or a question for the agent', 'data-qid': q.id }),
    h('div', { class: 'ctl' }, [h('button', { class: 'btn primary', type: 'button', 'data-act': 'reply', 'data-qid': q.id }, ['Send']),
      h('span', { class: 'small mut' }, ['The question stays open: the agent answers you, or confirms what it will do with your words and closes it. The buttons answer outright.'])]));
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
  if (r.runner && r.runnerLog) kids.push(h('button', { class: 'btn sm ghost', type: 'button', 'data-act': 'copy', 'data-text': r.runnerLog, 'data-label': 'Copy log path', title: r.runnerLog }, ['Copy log path']));
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
    // The badge: the dossiers in flight, each counted once: in progress, or on the oldest open stub (merged into it or parked under it).
    const rs = S.runs.filter(r => r.repo === repo); const prog = rs.filter(inProgress).map(r => r.id);
    const os = openStubs(repo); const cur = os.length ? os[os.length - 1] : null;
    const onStub = cur ? Array.from(new Set(cur.dossiers.map(d => d.id).concat(cur.members.map(m => m.id)))) : [];
    const n = new Set(prog.concat(onStub)).size;
    const nq = byRepo[repo] || 0; const cs = [];
    if (cur) cs.push(chip('stub ' + cur.state, cur.state)); if (nq) cs.push(chip(nq + ' open question' + (nq > 1 ? 's' : ''), 'open'));
    const meta = prog.length + ' in progress' + (cur ? ' · ' + onStub.length + ' on ' + cur.branch : ' · no open stub');
    cards.push(h('a', { class: 'card', href: hrefRepo(repo), 'data-k': 'home:' + repo }, [
      h('span', { class: 'title' }, [rlabel(repo)]), h('span', { class: 'badge ' + (n ? 'info' : ''), title: 'Dossiers in progress or on the oldest open stub, each counted once' }, [String(n)]),
      h('span', { class: 'meta' }, [meta]), h('span', { class: 'chips' }, cs)]));
  });
  return h('div', {}, [h('h1', {}, ['Pipeline']), h('p', { class: 'lead' }, ['Pick a repo to see what is running, what is next and what is done.']),
    accountsView(), runningView(), h('div', { class: 'grid' }, cards),
    section('Open questions', open.length, queueView(''), 'queue')]);
}
/* The Claude logins: which one the next stage starts on, the 5-hour and weekly windows, and the runs on each. */
const fmtReset = (iso) => { const t = Date.parse(iso); if (!t) return ''; const d = new Date(t); const day = d.toDateString() === new Date().toDateString() ? '' : d.toLocaleDateString(undefined, { weekday: 'short' }) + ' ';
  const mins = Math.max(0, Math.round((t - Date.now()) / 60000)); const left = mins < 60 ? mins + ' min' : Math.floor(mins / 60) + 'h' + String(mins % 60).padStart(2, '0');
  return 'resets ' + day + d.toTimeString().slice(0, 5) + ' (in ' + left + ')'; };
function meter(label, w) {
  const pct = w && typeof w.usedPct === 'number' ? Math.max(0, Math.min(100, w.usedPct)) : null;
  const tone = pct === null ? '' : pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : 'ok';
  return h('div', { class: 'meter' }, [h('div', { class: 'mhd' }, [h('span', { class: 'ml' }, [label]), h('span', { class: 'mv' }, [pct === null ? (w && w.status === 'reset' ? 'reset — not read since' : '—') : Math.round(pct) + '%'])]),
    h('div', { class: 'mbar' }, [h('span', { class: 'mfill ' + tone, style: 'width:' + (pct || 0) + '%' })]), h('div', { class: 'small mut' }, [w && w.resetsAt ? fmtReset(w.resetsAt) : ''])]);
}
function accountsView() {
  const as = S.accounts || []; if (!as.length) return null;
  const cards = as.map(a => h('div', { class: 'acard' + (a.next ? ' next' : ''), 'data-k': 'acct:' + a.name }, [
    h('div', { class: 'ahd' }, [h('span', { class: 'aname' }, [a.name]), a.next ? chip('next runs use this', 'done') : null, a.limitedUntil ? chip('blocked ' + fmtReset(a.limitedUntil).replace('resets ', 'until '), 'blocked') : null,
      a.runs.length ? chip(a.runs.length + ' running', 'neutral') : null]),
    h('div', { class: 'small mut' }, [[a.email, a.org].filter(Boolean).join(' · ')]),
    meter('5-hour', a.fiveHour), meter('Weekly', a.week),
    h('div', { class: 'small mut' }, [a.sampledAt ? when(a.sampledAt, 'read ') : 'never read'])]));
  return h('section', { class: 'sec', 'data-k': 'sec:acct' }, [h('div', { class: 'sechd' }, [h('h2', {}, ['Accounts']), h('span', { class: 'grow' }),
    h('button', { class: 'btn sm', type: 'button', 'data-act': 'usage-refresh', disabled: S.usageBusy ? true : null, title: 'One tiny haiku call per account reads its current windows' }, [S.usageBusy ? 'Checking…' : '↻ Check usage'])]),
    h('div', { class: 'agrid' }, cards)]);
}
/* Every run consuming tokens right now, across repos: its state, step, account, agents at work, context and last activity. */
function runningView() {
  const live = S.runs.filter(r => r.runner && ['running', 'waiting', 'paused'].includes(r.runner.state));
  const agents = live.reduce((n, r) => n + (r.agents ? r.agents.running : 0), 0);
  const working = live.filter(r => r.runner.state === 'running').length;
  const rows = live.sort((a, b) => (a.repo + a.id).localeCompare(b.repo + b.id)).map(r => {
    const last = r.updates && r.updates[0]; const nq = openQs(r).length;
    const right = [runChip(r.runner.state)];
    if (r.runner.account) right.push(chip('account ' + r.runner.account, 'neutral'));
    if (r.agents && r.agents.running) right.push(h('span', { class: 'chip fixing' }, [h('span', { class: 'dot info live' }), r.agents.running + ' agent' + (r.agents.running === 1 ? '' : 's')]));
    if (r.session && r.session.context && r.runner.state === 'running') right.push(chip('context ' + kfmt(r.session.context), 'neutral'));
    if (nq) right.push(h('span', { class: 'qb' }, ['? ' + nq]));
    if (r.blocked) right.push(chip('⛔ ' + r.blocked + ' blocked', 'blocked'));
    return h('a', { class: 'srow', href: hrefItem(r.repo, r.id), 'data-k': 'run:' + r.repo + '/' + r.id }, [h('span', { class: 'sid mono' }, [r.id]),
      h('span', { class: 'sname' }, [h('span', {}, [r.title]), h('span', { class: 'small mut rsub' }, [rlabel(r.repo) + ' · ' + (stageText(r) || 'starting') + (last ? ' · ' : ''), last ? when(last.ts, 'last update ') : null]), nowLine(r)]),
      h('span', { class: 'sright' }, right)]);
  });
  const head = live.length ? live.length + ' run' + (live.length === 1 ? '' : 's') + ' · ' + working + ' working' + (agents ? ' · ' + agents + ' sub-agent' + (agents === 1 ? '' : 's') : '') : 'Nothing is running';
  return h('section', { class: 'sec', 'data-k': 'sec:live' }, [h('div', { class: 'sechd' }, [h('h2', {}, ['Running now']), h('span', { class: 'small mut' }, [head])]),
    rows.length ? h('div', { class: 'srows' }, rows) : null]);
}
function questionsView() {
  const hq = hashQ(); const tq = hq && S.q.find(q => q.id === hq && q.state === 'answered');
  const kids = [h('h1', {}, ['Open questions']), h('p', { class: 'lead' }, ['Every question waiting for you, across repos, oldest first. Answer here or in Slack; the first answer is final.'])];
  if (tq) kids.push(section('Thread', null, qCard(tq, { thread: true, hit: hq }), 'thread'));
  kids.push(queueView(hq));
  return h('div', {}, kids);
}
/* What a live run's orchestrator is on right now (its transcript): the call in flight, else the background work it waits for; and how long it has been quiet. */
function nowDoing(r) {
  const live = r.runner ? ['running', 'waiting'].includes(r.runner.state) : (r.session && r.session.state === 'active');
  const a = r.session && r.session.activity; if (!live || !a) return null;
  const since = (ts) => ts && Date.parse(ts) ? ' · for ' + dur(Date.now() - Date.parse(ts)) : '';
  let text = '', tone = 'neutral';
  if (a.doing) text = 'Now: ' + a.doing + since(a.doingSince);
  else if (a.background.length) { const first = a.background.slice().sort((x, y) => (x.since || '').localeCompare(y.since || ''))[0]; text = 'Waiting on: ' + a.background.map(b => b.label).join(', ') + since(first.since); }
  else if (r.runner && r.runner.state === 'waiting') text = '';
  else text = 'Thinking';
  const quietMs = a.lastTs && Date.parse(a.lastTs) ? Date.now() - Date.parse(a.lastTs) : 0;
  const quiet = r.runner && r.runner.state === 'running' && quietMs >= 20 * 60000 ? 'quiet for ' + dur(quietMs) : '';
  if (quiet && !a.doing && !a.background.length) tone = 'warn';
  if (!text && !quiet) return null;
  return { text, quiet, tone, title: a.lastTs ? 'Last transcript activity ' + new Date(a.lastTs).toLocaleString() : '' };
}
function nowLine(r) {
  const n = nowDoing(r); if (!n) return null;
  return h('div', { class: 'nowline small', title: n.title }, [n.text ? h('span', { class: 'nowtext' }, [h('span', { class: 'dot info live' }), n.text]) : null,
    n.quiet ? chip(n.quiet + (n.tone === 'warn' ? ' ⚠' : ''), n.tone) : null]);
}
function latestDoing(r) { const u = r.updates.find(x => !x.auto && x.event !== 'owner-message') || r.updates[0]; return u ? u.text : (r.lastActivity || ''); }
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
/* One dossier on a stub card: id, title, and where it stands, one clean row that scales to many. */
function stubRow(repo, id, o) {
  const r = S.runs.find(x => x.repo === repo && x.id === id); const nq = r ? openQs(r) : [];
  const title = (r && r.title) || o.title || '';
  const right = [];
  const fixing = r && r.runner && r.runner.command === 'fix-deferred' && ['running', 'waiting', 'paused'].includes(r.runner.state);
  if (fixing) right.push(h('span', { class: 'chip fixing', title: 'A stacked PR with fixes for the deferred issues is being built' }, [h('span', { class: 'dot info live' }), r.runner.state === 'waiting' ? 'fixing deferred · waiting for you' : r.runner.state === 'paused' ? 'fixing deferred · paused' : 'fixing deferred']));
  if (o.merged) right.push(chip('merged', 'done'));
  else if (r && (r.status === 'done' || r.status === 'pr')) right.push(chip('PR open · ready to merge', 'done'));
  else { const st = r ? stageText(r) : ''; if (st) right.push(chip(st, 'neutral')); if (isLive(r)) right.push(h('span', { class: 'dot info live', title: 'running' })); }
  if (o.noPr) right.push(chip('no PR yet', 'neutral'));
  if (r && r.deferredOpen) right.push(h('span', { class: 'chip', title: r.deferredOpen + ' deferred item(s) still to tackle' }, [r.deferredOpen + ' deferred']));
  if (nq.length) right.push(h('span', { class: 'qb', title: nq.length + ' open question(s)' }, ['? ' + nq.length]));
  return h('a', { class: 'srow' + (o.merged ? ' done' : ''), href: hrefItem(repo, id), 'data-k': 'srow:' + repo + '/' + id, title: id + (title ? ' · ' + title : '') },
    [h('span', { class: 'sid mono' }, [id]), h('span', { class: 'sname' }, [title || '—']), h('span', { class: 'sright' }, right)]);
}
function stubGroup(key, label, rows, fold) {
  if (!rows.length) return null;
  const body = h('div', { class: 'srows' }, rows);
  const head = label + ' · ' + rows.length;
  if (fold) return h('details', { class: 'sgrp', 'data-k': key, open: rows.length <= 6 ? true : null }, [h('summary', { class: 'sgrp-h' }, [head]), body]);
  return h('div', { class: 'sgrp', 'data-k': key }, [h('div', { class: 'sgrp-h' }, [head]), body]);
}
function stubTree(repo, s) {
  const cs = [chip(s.state)]; if (s.stage) cs.push(chip(s.stage, 'neutral')); if (s.pr) cs.push(prChip(s.pr));
  const runOf = (id) => S.runs.find(x => x.repo === repo && x.id === id);
  const finished = (id) => { const r = runOf(id); return !!r && (r.status === 'done' || r.status === 'pr'); };
  const mem = s.members || [];
  const busy = mem.filter(m => !finished(m.id)).map(m => stubRow(repo, m.id, { noPr: m.noPr }));
  const ready = mem.filter(m => finished(m.id)).map(m => stubRow(repo, m.id, {}));
  const merged = s.dossiers.map(d => stubRow(repo, d.id, { merged: true, title: d.title }));
  const k = 'sg:' + repo + '/' + s.branch + ':';
  const groups = [stubGroup(k + 'busy', 'In progress', busy), stubGroup(k + 'ready', 'PR open · waiting for your merge', ready), stubGroup(k + 'merged', 'Merged into the stub', merged, true)].filter(Boolean);
  return h('div', { class: 'scard', 'data-k': 'ostub:' + repo + '/' + s.branch }, [
    h('div', { class: 'shead' }, [h('a', { class: 'title mono', href: hrefStub(repo, s.branch) }, [s.branch]), h('span', { class: 'meta' }, ['cut ' + s.cut]), h('span', { class: 'chips' }, cs)]),
    groups.length ? h('div', { class: 'sgrps' }, groups) : h('div', { class: 'meta' }, ['Nothing in progress or merged yet.'])]);
}
function sideColumn(repo) {
  const rr = S.stubs.repos.find(x => x.repo === repo); const stubs = rr ? rr.stubs : []; const os = openStubs(repo);
  const placed = {}; stubs.forEach(s => (s.members || []).forEach(m => { placed[m.id] = 1; }));
  const loose = S.runs.filter(r => r.repo === repo && r.running && !placed[r.id]);
  const merged = stubs.filter(s => s.state === 'merged');
  const kids = [];
  if (loose.length) kids.push(h('div', { class: 'scol-sec', 'data-k': 'loose:' + repo }, [h('h3', {}, ['In progress']), h('ul', { class: 'tree' }, loose.map(r => subItem(repo, r.id, false, false)))]));
  kids.push(h('h2', {}, ['Stubs']));
  kids.push.apply(kids, os.length ? os.map(s => stubTree(repo, s)) : [h('div', { class: 'empty' }, ['No open stub for this repo.'])]);
  // Dossiers on an open stub card show their deferred count there; this lists the rest (their stub merged).
  const onCard = {}; os.forEach(s => { (s.members || []).forEach(m => { onCard[m.id] = 1; }); s.dossiers.forEach(d => { onCard[d.id] = 1; }); });
  const dfd = S.runs.filter(r => r.repo === repo && r.deferredOpen > 0 && !onCard[r.id]).sort((a, b) => b.deferredOpen - a.deferredOpen);
  if (dfd.length) kids.push(h('div', { class: 'scol-sec', 'data-k': 'dfd:' + repo }, [h('h3', {}, ['Deferred on merged work · ' + dfd.reduce((n, r) => n + r.deferredOpen, 0) + ' open']),
    h('ul', { class: 'tree' }, dfd.map(r => h('li', {}, [h('a', { class: 'sub-item', href: hrefItem(repo, r.id) }, [h('span', { class: 'sid mono' }, [r.id]), h('span', { class: 'sname', title: r.title }, [r.title]), chip(r.deferredOpen + ' open', 'ready')])])))]));
  if (merged.length) kids.push(h('details', { class: 'mstubs', 'data-k': 'mstubs:' + repo }, [h('summary', {}, ['Merged stubs (' + merged.length + ')']),
    h('ul', { class: 'tree' }, merged.map(s => h('li', {}, [h('a', { class: 'sub-item', href: hrefStub(repo, s.branch) }, [h('span', { class: 'sid mono' }, [s.branch]), h('span', { class: 'sname', title: s.branch }, [s.dossiers.length + ' dossier(s)'])])])))]));
  return h('section', { class: 'scol full', 'data-k': 'sec:stubs' }, kids);
}
function repoView(repo) {
  const rs = S.runs.filter(r => r.repo === repo); const prog = rs.filter(inProgress); const os = openStubs(repo);
  const ow = (S.stubs.openWork || []).find(o => o.repo === repo); const oq = S.q.filter(q => q.state === 'open' && qRepo(q) === repo).sort((a, b) => a.ts.localeCompare(b.ts));
  const kids = [h('h1', {}, [rlabel(repo)]), h('p', { class: 'lead' }, [prog.length + ' running · ' + oq.length + ' open question' + (oq.length === 1 ? '' : 's') + ' · ' + os.length + ' open stub' + (os.length === 1 ? '' : 's')])];
  if (oq.length) kids.push(section('Waiting on you', oq.length, h('div', {}, oq.map(q => qCard(q, { thread: true }))), 'oq'));
  const frame = ow ? h('iframe', { class: 'report', 'data-repo': repo, src: '/open-work/' + enc(repo) + '?theme=dark&v=' + enc(ow.updated), title: 'Open work', scrolling: 'no', style: S.frameH[repo] ? 'height:' + S.frameH[repo] + 'px' : null }) : h('div', { class: 'empty' }, ['The open-work report is not generated yet.']);
  // The stubs (the current one and the next) first, then Open work, each full width, one under the other.
  kids.push(sideColumn(repo));
  kids.push(h('section', { class: 'main-col', 'data-k': 'sec:report' }, [h('h2', {}, ['Open work']), frame]));
  return h('div', {}, kids);
}
function header(r, mode) {
  const nq = openQs(r); const cs = [chip(r.status)];
  if (r.runner && r.runner.state !== 'done') cs.push(runChip(r.runner.state));
  const st = stageText(r); if (st) cs.push(chip(st, 'neutral'));
  if (r.session && r.session.context) cs.push(h('span', { title: 'Tokens in the orchestrator\'s context right now. It compacts at 200k, so this falls back after each compaction and each new stage.' }, [chip('context ' + kfmt(r.session.context) + ' / 200k', 'neutral')]));
  if (r.pr) cs.push(prChip(r.pr)); if (r.jira && r.jira !== 'null') cs.push(chip(r.jira, 'neutral'));
  nq.forEach(q => cs.push(h('a', { class: 'chip warn', href: '/questions#q=' + enc(q.id) }, ['? open question'])));
  const acts = [];
  if (mode === 'run') acts.push(h('a', { class: 'btn', href: hrefItem(r.repo, r.id) + '/dossier' }, ['View dossier']));
  else if (r.updates.length || inProgress(r)) acts.push(h('a', { class: 'btn', href: hrefItem(r.repo, r.id) }, ['Back to run']));
  return h('div', { class: 'panel' }, [h('h1', { style: 'margin-top:0' }, [r.id + ' · ' + r.title]), r.summary && mode === 'run' ? h('p', { class: 'sum' }, [r.summary]) : null,
    h('div', { class: 'chips' }, cs), nowLine(r), h('div', { class: 'row' }, controls(r, true).concat(acts))]);
}
const ICON = { step: '→', blocked: '⛔', start: '▶', stage: '✔', question: '?', answered: '✔', 'suite-green': '●', 'suite-red': '✖', gate: '▣', pr: '⇄', workflow: '⚙', failed: '✖', waiting: '⏳', done: '★', stopped: '■', note: '•', spawn: '⎇', reply: '↩', routine: '·', info: '·', entry: '§' };
const KLABEL = { step: 'next step', blocked: 'blocked by the safety check', start: 'run started', stage: 'stage done', question: 'question asked', answered: 'answered', 'suite-green': 'suite green', 'suite-red': 'suite red', gate: 'gate', pr: 'pull request', workflow: 'workflow', spawn: 'agent', failed: 'failed', waiting: 'waiting', done: 'done', stopped: 'stopped', note: 'update', reply: 'reply', routine: 'routine', info: 'runner' };
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
    return h('li', { class: cls, 'data-k': dk }, [qCard(q, { thread: q.state !== 'open' ? false : true })]);
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
  const body = e.html ? h('div', { class: 'tx md', 'data-raw': '1', 'data-html': e.html }, []) : h('div', { class: 'tx' }, [e.text]);
  return h('li', { class: cls, 'data-k': dk }, [h('div', { class: 'hd' }, [icon(k), h('span', { class: 'kind' }, [KLABEL[k] || e.type]), when(e.ts)]), body]);
}
function itemLi(it, gk) {
  if (it.kind !== 'more') return tlItem(it);
  const n = it.entries.length;
  return h('li', { class: 'more', 'data-k': 'more:' + gk + it.entries[0].entry.ts + n }, [h('details', {}, [h('summary', {}, ['+' + n + ' routine update' + (n > 1 ? 's' : '') + ' · ', when(it.entries[0].entry.ts)]),
    h('ol', {}, it.entries.map(x => h('li', { 'data-k': ekey(x.entry) }, [h('span', { class: 'mut' }, [(when(x.entry.ts) || h('span')), ' ']), x.entry.text.split('\n')[0]])))])]);
}
function phaseGroup(g, key) {
  const c = g.counts; const bits = []; if (c.questions) bits.push(c.questions + ' question' + (c.questions > 1 ? 's' : '')); bits.push(c.updates + ' update' + (c.updates === 1 ? '' : 's'));
  const head = h('summary', {}, [h('span', { class: 'pl' }, [g.label]), g.current ? chip('current', 'info') : (g.durationMs !== null ? h('span', { class: 'chip' }, [dur(g.durationMs)]) : null), h('span', { class: 'pc' }, [bits.join(' · ')])]);
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
  const head = [h('span', {}, [n.phase || 'Starting — no phase reached yet'])];
  if (n.since && Date.parse(n.since)) head.push(h('span', { class: 'chip' }, ['in this phase for ' + dur(Date.now() - Date.parse(n.since))]));
  if (runner) head.push(runChip(runner.state));
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
  const bv = blockedView(r, d); if (bv) kids.push(bv);
  const res = resultView(r, d); if (res) kids.push(res);
  const lm = liveMessageView(r); if (lm) kids.push(lm);
  const dp = deferredProgress(r, d); if (dp) kids.push(dp);
  /* Every dossier has the same tabs; a finished or not-started one opens on the
     dossier itself, a run in progress on its timeline. Empty tabs are left out. */
  const key = repo + '/' + id;
  const hasTl = d && d.entries && d.entries.length > 0, hasLog = d && d.log && d.log.length > 0, nDf = r.deferred ? r.deferred.items.length : 0;
  const list = [];
  if (mode !== 'run') list.push(['doc', 'Dossier']);
  // One timeline tab per run: Implement dossier (the /work-on phases), then each later run (Fix deferred issues, …) with its own steps.
  const secs = (d && d.sections) || [];
  if (mode === 'run' || hasTl) list.push(['timeline', d && d.planning ? 'Planning' : 'Implement dossier']);
  secs.filter(x => x.cmd).forEach(x => list.push(['tl:' + x.cmd, x.label]));
  if (mode === 'run' || hasLog) list.push(['log', 'Build log' + (hasLog ? ' (' + d.log.length + ')' : '')]);
  if (nDf) list.push(['deferred', 'Deferred (' + r.deferredOpen + ' open / ' + nDf + ')']);
  // A finished dossier opens on its dossier, but while a later run (fixing deferred issues) works, on its timeline.
  const busy = r.runner && ['running', 'waiting', 'paused'].includes(r.runner.state);
  const liveTab = d && d.activeSection ? 'tl:' + d.activeSection : 'timeline';
  let tab = S.tab[key] || (busy && list.some(t => t[0] === liveTab) ? liveTab : busy && list.some(t => t[0] === 'timeline') ? 'timeline' : list[0][0]); if (!list.some(t => t[0] === tab)) tab = list[0][0];
  if (list.length > 1) kids.push(tabs(key, list));
  if (!d) kids.push(h('div', { class: 'empty' }, ['Loading…']));
  else if (tab === 'doc') {
    const sv = summaryView(r, d); if (sv) kids.push(sv);
    const html = d.html || '';
    kids.push(h('div', { class: 'panel md', 'data-raw': '1', 'data-sig': html.length + ':' + html.slice(0, 40) + html.slice(-40), 'data-html': html }, []));
    if (r.answered.length) kids.push(section('Answered questions', r.answered.length, h('div', {}, r.answered.map(a => h('div', { class: 'panel qa', 'data-k': 'qa:' + a.id }, [
      h('div', {}, ['Q: ' + a.text]), h('div', { class: 'a' }, ['A: ' + (a.option ? a.option + ' · ' : '') + a.answer + ' · ', when(a.answeredAt || a.asked)]), h('a', { class: 'small', href: '/questions#q=' + enc(a.id) }, ['thread'])]))), 'qa'));
  }
  else if (tab === 'log') kids.push(hasLog ? h('ol', { class: 'tl', 'data-k': 'log' }, d.log.slice().reverse().map((l, i) => h('li', { class: 'log', 'data-k': 'l:' + i + l.slice(0, 30) }, [h('div', { class: 'tx' }, [l])]))) : h('div', { class: 'empty' }, ['The dossier has no build log yet.']));
  else if (tab === 'deferred') kids.push(deferredView(r));
  else {
    const sec = tab.indexOf('tl:') === 0 ? secs.find(x => 'tl:' + x.cmd === tab) : null;
    const live = (d.activeSection || '') === (sec ? sec.cmd : '');
    if (mode === 'run' && live) kids.push(nowView(d, r.runner, true));
    kids.push(groupsView(sec ? sec.groups : (d.groups || []), key + (sec ? ':' + sec.cmd : ''), 'No updates yet — the agent posts here as the run progresses'));
  }
  return h('div', {}, kids);
}
/* The deferred ledger: open items with a copyable /plan seed, fixed ones with their
   commit, the ones routed to a new dossier; "Fix all deferred" sits in the header. */
function dfState(r, i) {
  if (i.state === 'fixed') return r.deferred.pr ? h('a', { href: r.deferred.pr, target: '_blank', rel: 'noopener', title: i.note ? 'commit ' + i.note : '' }, [chip('fixed · stacked PR ' + prNum(r.deferred.pr), 'done')]) : chip('fixed' + (i.note ? ' · ' + i.note.slice(0, 8) : ''), 'done');
  // Ruled to its own dossier: /fix-deferred only routes it; the dossier exists once /plan <ID> D-n has run and logged
  // DEFERRED-DOSSIER, and then the chip links to it. Until then, say that, and hand over the exact command.
  if (i.state === 'dossier') {
    if (i.dossier) return h('a', { href: hrefItem(r.repo, i.dossier) }, [chip('→ dossier ' + i.dossier, 'planned')]);
    const cmd = '/plan ' + r.id + ' ' + i.id;
    if (i.planning && ['running', 'waiting', 'paused'].includes(i.planning)) return h('span', { title: 'A /plan run is writing this dossier' + (i.planning === 'waiting' ? '; it waits for your answer' : '') }, [chip(i.planning === 'waiting' ? 'being planned · ? waiting for you' : 'being planned', i.planning === 'waiting' ? 'warn' : 'pending')]);
    const st = S.ctl[r.repo + '/' + r.id + ':plan:' + i.id] || {};
    return h('span', { class: 'dfplan', title: 'You ruled this item to its own dossier. No dossier is written yet' + (i.planning === 'failed' || i.planning === 'stopped' ? ' (its /plan run ' + i.planning + ')' : '') + '. Plan it starts ' + cmd + ' in the background; its questions come to you here.' }, [chip('own dossier · not planned yet', 'planned'),
      h('button', { class: 'btn sm', type: 'button', 'data-act': 'plan-item', 'data-repo': r.repo, 'data-id': r.id, 'data-item': i.id, disabled: st.busy ? true : null }, [st.busy ? 'Starting…' : 'Plan it']),
      h('button', { class: 'btn sm', type: 'button', 'data-act': 'copy', 'data-text': cmd }, ['Copy ' + cmd]), st.err ? h('span', { class: 'err' }, [st.err]) : null]);
  }
  if (i.state === 'deprecated') return h('span', { title: i.note || '' }, [chip('dropped', 'neutral')]);
  if (i.state === 'open' && i.keptOpen) return h('span', { title: i.ruling || '' }, [chip('left open · your ruling', 'neutral')]);
  if (i.state === 'open' && i.question && S.q.some(q => q.id === i.question && q.state === 'open')) return h('a', { href: '/questions#q=' + enc(i.question) }, [chip('? waiting for you', 'warn')]);
  if (i.state === 'fixing') return h('span', { title: i.note || '' }, [chip(fixLive(r) ? 'being fixed' : 'fix not finished', fixLive(r) ? 'pending' : 'blocked')]);
  return chip('not tackled', 'ready');
}
/* Fixing the deferred issues: the stacked PR, where each item stands (being fixed, fixed, its own dossier, dropped,
   left open), and the questions it waits on; live while /fix-deferred runs, the final state once it is done. */
const fixLive = (r) => !!(r.runner && r.runner.command === 'fix-deferred' && ['running', 'waiting', 'paused'].includes(r.runner.state));
const prNum = (u) => { const m = String(u || '').match(/(\d+)\/?$/); return m ? '#' + m[1] : 'PR'; };
function deferredProgress(r, d) {
  const df = r.deferred; if (!df || !df.items.length) return null;
  const live = fixLive(r); const ran = live || !!df.pr || !!df.branch || df.items.some(i => i.state === 'fixing');
  if (!ran) return null;
  const n = (s) => df.items.filter(i => i.state === s).length;
  const parts = [['fixed', n('fixed'), 'ok'], ['being fixed', n('fixing'), 'acc'], ['to its own dossier', n('dossier'), 'warn'], ['dropped', n('deprecated'), 'mut'], ['left open', n('open'), 'mut2']];
  const total = df.items.length; const nq = openQs(r).length;
  const head = [h('span', { class: 'kind' }, [live ? 'Fixing deferred issues' : 'Deferred issues · after the fix run']), live ? h('span', { class: 'dot info live' }) : null];
  if (live) head.push(runChip(r.runner.state)); else if (r.runner && r.runner.command === 'fix-deferred' && r.runner.state !== 'done') head.push(runChip(r.runner.state));
  if (df.pr) head.push(h('a', { class: 'chip act', href: df.pr, target: '_blank', rel: 'noopener' }, ['stacked PR ' + prNum(df.pr)]));
  else if (df.branch) head.push(chip('branch ' + df.branch, 'neutral'));
  if (nq) head.push(h('a', { class: 'chip warn', href: '/questions' }, ['? ' + nq + ' waiting for you']));
  const bar = h('div', { class: 'dfp-bar' }, parts.filter(p => p[1]).map(p => h('span', { class: 'seg ' + p[2], style: 'flex:' + p[1], title: p[1] + ' ' + p[0] }, [])));
  const legend = h('div', { class: 'dfp-leg small' }, parts.filter(p => p[1]).map(p => h('span', {}, [h('span', { class: 'sw ' + p[2] }), p[1] + ' ' + p[0]])));
  const rows = df.items.map(i => h('div', { class: 'dfp-row', 'data-k': 'dfp:' + i.id }, [h('span', { class: 'mono b' }, [i.id]), h('span', { class: 'dfp-t', title: [i.title, i.note].filter(Boolean).join(' — ') }, [i.title]), dfState(r, i)]));
  const last = live && r.updates && r.updates.find(u => !u.auto && u.event !== 'owner-message');
  return h('section', { class: 'panel dfp', 'data-k': 'dfp' }, [h('div', { class: 'res-hd' }, head), bar, legend,
    last ? h('div', { class: 'small mut' }, ['Latest: ' + last.text.split('\n')[0].slice(0, 220) + ' · ', when(last.ts)]) : null,
    h('div', { class: 'dfp-rows' }, rows),
    !live && n('fixing') ? h('div', { class: 'small mut' }, [n('fixing') + ' item(s) were being fixed when the run ended; the build log says where they stand.']) : null]);
}
/* Tool calls the auto-mode safety check refused, not ruled on yet: allow once, always allow in this repo (a narrow
   permission rule in its .claude/settings.local.json), or deny. The ruling reaches the agent as the answer to its open question. */
function blockedView(r, d) {
  const bs = d && d.blocks; if (!bs || !bs.length) return null;
  const key = r.repo + '/' + r.id; const st = S.chat['blk:' + key] = S.chat['blk:' + key] || {};
  const cards = bs.map(b => {
    const rk = 'rule:' + b.id; if (st[rk] === undefined) st[rk] = b.tool + '(' + b.target + ')';
    const busy = st.busy === b.id;
    return h('div', { class: 'blk', 'data-k': 'blk:' + b.id }, [
      h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['⛔ ' + (b.tool || 'tool') + (b.agent ? ' · a sub-agent' : '')]), when(b.ts)]),
      h('pre', { class: 'blk-cmd' }, [b.target]),
      b.reason ? h('div', { class: 'small' }, [h('span', { class: 'mut' }, ['Why it was blocked: ']), b.reason]) : null,
      h('div', { class: 'ctl' }, [
        h('button', { class: 'btn', type: 'button', 'data-act': 'blk', 'data-do': 'once', 'data-repo': r.repo, 'data-id': r.id, 'data-b': b.id, disabled: busy || !d.blockQuestion ? true : null, title: d.blockQuestion ? 'Answers the agent\'s open question with an approval naming this exact command' : 'The agent has not asked yet' }, ['Allow once']),
        h('button', { class: 'btn', type: 'button', 'data-act': 'blk', 'data-do': 'deny', 'data-repo': r.repo, 'data-id': r.id, 'data-b': b.id, disabled: busy || !d.blockQuestion ? true : null }, ['Deny'])]),
      h('div', { class: 'blk-rule' }, [h('input', { type: 'text', class: 'mono', 'data-rule': b.id, value: st[rk], 'aria-label': 'Permission rule' }),
        h('button', { class: 'btn', type: 'button', 'data-act': 'blk', 'data-do': 'always', 'data-repo': r.repo, 'data-id': r.id, 'data-b': b.id, disabled: busy ? true : null }, ['Always allow in this repo'])]),
      h('div', { class: 'small mut' }, ['Always allow saves the rule to ' + r.repo + '\'s .claude/settings.local.json (permissions.allow) and restarts the stage when the agent gets your answer, so the rule applies at once (it redoes the work since the stage\'s last checkpoint). Widen it to a prefix, e.g. Bash(ssh vm-test *), to cover similar commands.'])]);
  });
  const kids = [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Blocked by the safety check · ' + bs.length]), d.blockQuestion ? h('span', { class: 'small mut' }, ['ruling goes to ' + d.blockQuestion]) : h('span', { class: 'small mut' }, ['the agent has not asked yet'])])].concat(cards);
  if (st.msg) kids.push(h('div', { class: 'ctl-msg ' + (st.ok ? 'ok' : 'bad') }, [st.msg]));
  return h('section', { class: 'panel blocked', 'data-k': 'blocked' }, kids);
}
/* What the dossier changed, in product terms (/product-summary, written after Phase 9), above the dossier itself. */
function summaryView(r, d) {
  const key = r.repo + '/' + r.id; const st = S.chat['sum:' + key] = S.chat['sum:' + key] || {};
  if (d.summary) return h('section', { class: 'panel psum', 'data-k': 'psum' }, [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Product summary']), when(d.summary.ts)]), mdBlock(d.summary.html, 'psum:' + d.summary.ts)]);
  const built = ['done', 'pr', 'review'].indexOf(r.status) >= 0 && !(r.runner && ['running', 'waiting', 'paused'].includes(r.runner.state));
  if (!built) return null;
  const busy = d.summaryBusy || st.busy;
  return h('section', { class: 'panel psum empty-sum', 'data-k': 'psum' }, [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Product summary'])]),
    h('div', { class: 'ctl' }, [h('button', { class: 'btn', type: 'button', 'data-act': 'summary', 'data-repo': r.repo, 'data-id': r.id, disabled: busy ? true : null }, [busy ? 'Writing the summary…' : 'Write product summary']),
      h('span', { class: 'small mut' }, ['This build finished before summaries were written; a short extra run writes it now.'])]),
    st.err ? h('div', { class: 'err' }, [st.err]) : null]);
}
/* The finished run's last reply, as a terminal would show it, then the owner's chat with that session. */
const mdBlock = (html, k) => h('div', { class: 'md', 'data-raw': '1', 'data-k': k, 'data-sig': html.length + ':' + html.slice(0, 40), 'data-html': html }, []);
function resultView(r, d) {
  const c = d && d.conv; if (!c || !c.result) return null;
  const key = r.repo + '/' + r.id; const st = S.chat[key] = S.chat[key] || {};
  const who = { owner: 'You', agent: 'Agent', runner: 'Runner' };
  const kids = [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, [c.result.command === 'fix-deferred' ? 'Fix-deferred run · final reply' : 'Final reply']), when(c.result.ts)]), mdBlock(c.result.html, 'res:' + c.result.ts)];
  c.chat.forEach((l, i) => kids.push(h('div', { class: 'chat ' + l.from, 'data-k': 'ch:' + i + ':' + l.ts }, [h('div', { class: 'who' }, [who[l.from] + ' · ', when(l.ts)]), mdBlock(l.html, 'chm:' + i)])));
  if (c.busy) kids.push(h('div', { class: 'chat agent wait', 'data-k': 'ch:busy' }, [h('span', { class: 'dot info live' }), ' The agent is answering…']));
  if (c.canChat) {
    kids.push(h('textarea', { 'data-chat': key, placeholder: 'Ask the agent a question, or tell it what to do next', disabled: c.busy || st.busy ? true : null, 'data-k': 'ch:ta' }));
    kids.push(h('div', { class: 'ctl', 'data-k': 'ch:ctl' }, [h('button', { class: 'btn primary', type: 'button', 'data-act': 'chat', 'data-repo': r.repo, 'data-id': r.id, disabled: c.busy || st.busy ? true : null }, [st.busy ? 'Sending…' : 'Send']),
      h('span', { class: 'small mut' }, ['Resumes the session that wrote this reply.'])]));
  }
  if (st.err) kids.push(h('div', { class: 'err', 'data-k': 'ch:err' }, [st.err]));
  return h('section', { class: 'panel result', 'data-k': 'result' }, kids);
}
/* A message to a live run: the runner hands it to the running agent at once (mid-turn, or by waking it while it waits
   on a question, which stays open). For a correction to an answer already given, or a change of plan. */
function liveMessageView(r) {
  if (!r.runner || !['running', 'waiting', 'paused'].includes(r.runner.state)) return null;
  const key = r.repo + '/' + r.id; const st = S.chat[key] = S.chat[key] || {};
  const sent = (r.updates || []).filter(u => u.event === 'owner-message').slice(0, 3);
  const delivered = (r.updates || []).find(u => u.kind === 'runner' && /^delivered \d+ message/.test(u.text));
  const kids = [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Message the running agent'])])];
  sent.slice().reverse().forEach((u, i) => kids.push(h('div', { class: 'chat owner', 'data-k': 'lm:' + u.ts + i }, [h('div', { class: 'who' }, ['You · ', when(u.ts),
    delivered && delivered.ts >= u.ts ? ' · delivered' : ' · waiting for the agent']), h('div', { class: 'tx' }, [u.text])])));
  kids.push(h('textarea', { 'data-chat': key, placeholder: 'Correct an earlier answer, or tell it what to do next. Not an answer to an open question.', disabled: st.busy ? true : null, 'data-k': 'lm:ta' }));
  kids.push(h('div', { class: 'ctl', 'data-k': 'lm:ctl' }, [h('button', { class: 'btn primary', type: 'button', 'data-act': 'chat', 'data-repo': r.repo, 'data-id': r.id, disabled: st.busy ? true : null }, [st.busy ? 'Sending…' : 'Send']),
    h('span', { class: 'small mut' }, [r.runner.state === 'paused' ? 'The run is paused for the usage limit; the agent gets it when it resumes.' : 'The agent gets it now and confirms in a note what it will do.'])]));
  if (st.err) kids.push(h('div', { class: 'err', 'data-k': 'lm:err' }, [st.err]));
  return h('section', { class: 'panel result', 'data-k': 'livemsg' }, kids);
}
/* The deferred ledger as a table: tick items (or all) and "Fix selected" runs /fix-deferred on just those, in one
   stacked PR through the implementer and review flow; an item that looks big enough for a dossier is put to the owner first. */
// Only an item still open can be picked for a fix; one ruled to its own dossier is seeded with /plan instead.
const dfPickable = (i) => i.state === 'open';
const cbx = (on, attrs) => h('button', Object.assign({ class: 'cbx' + (on ? ' on' : ''), type: 'button', role: 'checkbox', 'aria-checked': on ? 'true' : 'false' }, attrs), [on ? '✓' : '']);
function deferredView(r) {
  const df = r.deferred; const key = r.repo + '/' + r.id;
  const sel = S.dsel[key] = S.dsel[key] || {}; const pick = df.items.filter(dfPickable);
  Object.keys(sel).forEach(k => { if (!pick.some(i => i.id === k)) delete sel[k]; });
  const n = pick.filter(i => sel[i.id]).length; const all = pick.length > 0 && n === pick.length;
  const fix = r.controls && r.controls['fix-deferred']; const st = S.ctl[key] || {};
  const fact = (label, v, bad) => v ? h('span', { class: 'dfact' + (bad ? ' bad' : '') }, [h('span', { class: 'l' }, [label]), v]) : null;
  const cards = df.items.map(i => {
    const can = dfPickable(i); const on = !!sel[i.id];
    const riskWord = (i.risk || '').split(':')[0].trim(); const riskWhy = (i.risk || '').split(':').slice(1).join(':').trim();
    return h('div', { class: 'dfc' + (on ? ' on' : '') + (can ? ' pick' : ' closed'), 'data-k': 'df:' + i.id, 'data-act': can ? 'dsel' : null, 'data-key': key, 'data-id': i.id, role: can ? 'checkbox' : null, 'aria-checked': can ? (on ? 'true' : 'false') : null, tabindex: can ? '0' : null }, [
      h('div', { class: 'dfc-h' }, [can ? h('span', { class: 'cbx' + (on ? ' on' : '') }, [on ? '✓' : '']) : null, h('span', { class: 'mono b' }, [i.id]), chip(i.kind, 'neutral'), h('span', { class: 'grow' }), dfState(r, i)]),
      h('div', { class: 'dfc-t' }, [i.title]),
      i.loc ? h('code', { class: 'dfc-loc' }, [i.loc]) : null,
      h('div', { class: 'dfc-f' }, [fact('Risk', riskWord, /^(high|critical)/i.test(riskWord)), fact('Contract', i.contract, i.contract === 'external'), fact('Effort', i.effort, i.effort === 'L'),
        i.inReport === false ? h('span', { class: 'dfact mut' }, ['not product code']) : null]),
      riskWhy || i.why ? h('div', { class: 'dfc-why' }, [[riskWhy, i.why].filter(Boolean).join(' · ')]) : null,
      can ? h('div', { class: 'dfc-a' }, [h('button', { class: 'btn sm ghost', type: 'button', 'data-act': 'copy', 'data-text': '/plan ' + r.id + ' ' + i.id, title: 'Copy the /plan seed for this item' }, ['Copy /plan seed'])]) : null]);
  });
  const links = [];
  if (df.report) links.push(h('a', { class: 'small', href: '/deferred/' + enc(r.repo) + '/' + enc(r.id), target: '_blank', rel: 'noopener' }, ['full report']));
  if (df.pr) links.push(h('a', { class: 'small', href: df.pr, target: '_blank', rel: 'noopener' }, ['stacked PR']));
  const bar = [];
  if (pick.length) {
    const confirming = st.confirm === 'fix-sel'; const busy = st.busy === 'fix-sel';
    const why = !fix || !fix.show ? (r.runner && ['running', 'waiting', 'paused'].includes(r.runner.state) ? 'the run is still going' : 'available once the build is finished') : '';
    bar.push(h('button', { class: 'btn sm', type: 'button', 'data-act': 'dsel-all', 'data-key': key }, [all ? 'Clear selection' : 'Select all ' + pick.length]));
    bar.push(h('span', { class: 'small mut' }, [n + ' of ' + pick.length + ' selected']));
    bar.push(h('span', { class: 'grow' }));
    if (why) bar.push(chip(why, 'blocked'));
    if (confirming) bar.push(h('button', { class: 'btn act', type: 'button', 'data-act': 'ctl-cancel', 'data-repo': r.repo, 'data-id': r.id }, ['Cancel']));
    bar.push(h('button', { class: 'btn act' + (confirming ? ' confirm' : ' go'), type: 'button', 'data-act': 'fix-sel', 'data-repo': r.repo, 'data-id': r.id, disabled: !n || !!why || !!st.busy ? true : null },
      [busy ? 'Starting the fix…' : confirming ? 'Confirm: fix ' + n + ' item' + (n === 1 ? '' : 's') : 'Fix selected (' + n + ')']));
  }
  const kids = [];
  if (bar.length) kids.push(h('div', { class: 'dfbar', 'data-k': 'dfbar' }, bar));
  if (pick.length) kids.push(h('div', { class: 'small mut dfnote', 'data-k': 'dfnote' }, ['Click a card to select it. Selected items are fixed in one stacked PR, test first, through the implementer and review flow, without asking. You are asked only about an item the agent would rather not fix there: one that deserves its own dossier, no longer applies, or is not worth it.']));
  if (st.msg && st.last === 'fix-sel') kids.push(h('div', { class: 'ctl-msg ' + (st.ok ? 'ok' : 'bad'), 'data-k': 'dfmsg' }, [st.msg]));
  kids.push(h('div', { class: 'dfcards', 'data-k': 'dfcards' }, cards));
  if (links.length) kids.push(h('div', { class: 'ctl', 'data-k': 'dflinks' }, links));
  return h('div', { class: 'dfwrap', 'data-k': 'deferred' }, kids);
}
function stubView(repo, branch) {
  const rr = S.stubs.repos.find(x => x.repo === repo); const s = rr && rr.stubs.find(x => x.branch === branch);
  if (!s) return h('div', {}, [h('h1', {}, [branch]), h('div', { class: 'empty' }, [S.loaded ? 'No such stub.' : 'Loading…'])]);
  const d = S.detail && S.detail.url === detailUrl() ? S.detail.data : null;
  const cs = [chip(s.state), chip('cut ' + s.cut, 'neutral')]; if (s.stage) cs.push(chip(s.stage, 'neutral')); if (s.pr) cs.push(prChip(s.pr));
  const impl = s.dossiers.length ? h('div', { class: 'mini' }, s.dossiers.map(x => h('div', { class: 'card', 'data-k': 'i:' + x.id }, [
    h('a', { class: 'stretch title', href: hrefItem(x.repo || repo, x.id) }, [h('span', { class: 'id' }, [x.id + ' ']), x.title]), h('div', { class: 'chips' }, [chip(x.status || '?')])]))) : h('div', { class: 'empty' }, ['No dossier is implemented on this stub yet.']);
  const key = repo + '/' + branch; const tab = S.tab[key] || 'timeline'; const body = [];
  body.push(tabs(key, [['timeline', 'Timeline'], ['record', 'Record']]));
  if (!d || !d.groups) body.push(h('div', { class: 'empty' }, ['Loading…']));
  else if (tab === 'record') body.push(d.record.length ? h('ol', { class: 'tl', 'data-k': 'rec' }, d.record.slice().reverse().map(tlEntry)) : h('div', { class: 'empty' }, [d.hasRecord ? 'The record file has no sections.' : 'No record file for this stub.']));
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
  if (R.kind === 'item') { const r = findRun(R.repo, R.id); if (!r) return null; return '/api/timeline/' + enc(R.repo) + '/' + enc(R.id); }
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
  if (all || w.has('runs') || w.has('scan')) jobs.push(fetchJ('/api/runs').then(d => { if (d && d.pageId) { if (S.pageId && S.pageId !== d.pageId) { location.reload(); return; } S.pageId = d.pageId; } if (d && d.runs) { S.runs = d.runs; S.slack = d.slack; S.scannedAt = d.scannedAt; S.accounts = d.accounts || []; S.usageBusy = !!d.usageBusy; } }));
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
async function fixSelected(btn) {
  const repo = btn.dataset.repo, id = btn.dataset.id, key = repo + '/' + id; const st = S.ctl[key] = S.ctl[key] || {};
  if (st.confirm !== 'fix-sel') { st.confirm = 'fix-sel'; clearTimeout(st.t); st.t = setTimeout(() => { if (st.confirm === 'fix-sel') { st.confirm = null; render(); } }, 8000); render(); return; }
  clearTimeout(st.t); st.confirm = null; st.busy = 'fix-sel'; st.msg = ''; st.last = 'fix-sel'; render();
  const items = Object.keys(S.dsel[key] || {});
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/fix-deferred', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ items }) });
    const j = await r.json().catch(() => ({})); st.msg = j.output || j.error || ('HTTP ' + r.status); st.ok = r.ok && j.ok !== false;
    if (st.ok) S.dsel[key] = {};
  } catch (e) { st.msg = 'request failed'; st.ok = false; }
  st.busy = null; await load(['runs']);
}
async function ruleBlock(btn) {
  const repo = btn.dataset.repo, id = btn.dataset.id, bid = btn.dataset.b, act = btn.dataset.do, key = repo + '/' + id;
  const st = S.chat['blk:' + key] = S.chat['blk:' + key] || {};
  const inp = document.querySelector('input[data-rule="' + bid.replace(/"/g, '') + '"]'); if (inp) st['rule:' + bid] = inp.value;
  if (st.confirm !== act + bid) { st.confirm = act + bid; btn.textContent = 'Confirm: ' + btn.textContent; setTimeout(() => { if (st.confirm === act + bid) { st.confirm = null; render(); } }, 8000); return; }
  st.confirm = null; st.busy = bid; st.msg = ''; render();
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/blocks/' + enc(bid), { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ action: act, rule: st['rule:' + bid] }) });
    const j = await r.json().catch(() => ({})); st.ok = r.ok && j.ok !== false; st.msg = j.output || j.error || ('HTTP ' + r.status);
  } catch (e) { st.ok = false; st.msg = 'request failed'; }
  st.busy = null; await load(['runs', 'questions']);
}
async function writeSummary(btn) {
  const repo = btn.dataset.repo, id = btn.dataset.id, st = S.chat['sum:' + repo + '/' + id] = S.chat['sum:' + repo + '/' + id] || {};
  st.busy = true; st.err = ''; render();
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/summary', { method: 'POST' });
    const j = await r.json().catch(() => ({})); if (!r.ok) st.err = j.error || ('HTTP ' + r.status);
  } catch (e) { st.err = 'request failed'; }
  st.busy = false; await load(['runs']);
}
async function planItem(btn) {
  const repo = btn.dataset.repo, id = btn.dataset.id, item = btn.dataset.item, k = repo + '/' + id + ':plan:' + item;
  const st = S.ctl[k] = S.ctl[k] || {}; if (st.busy) return; st.busy = true; st.err = ''; render();
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/plan-item', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ item }) });
    const j = await r.json().catch(() => ({})); if (!(r.ok && j.ok !== false)) st.err = j.error || j.output || ('HTTP ' + r.status);
  } catch (e) { st.err = 'request failed'; }
  st.busy = false; await load(['runs']);
}
async function sendChat(btn) {
  const repo = btn.dataset.repo, id = btn.dataset.id, key = repo + '/' + id; const st = S.chat[key] = S.chat[key] || {};
  const ta = document.querySelector('textarea[data-chat="' + key.replace(/"/g, '') + '"]'); const text = ta ? ta.value.trim() : '';
  if (!text || st.busy) return;
  st.busy = true; st.err = ''; render();
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/chat', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok !== false) { if (ta) ta.value = ''; } else st.err = j.error || j.output || ('HTTP ' + r.status);
  } catch (e) { st.err = 'request failed'; }
  st.busy = false; await load(['runs']);
}
document.addEventListener('click', (e) => {
  let t = e.target.closest ? e.target.closest('[data-act]') : null;
  const inLink = e.target.closest ? e.target.closest('a[href]') : null;
  if (t && inLink && t.contains(inLink) && t !== inLink) t = null;   // a link inside a clickable card stays a link
  if (t) {
    e.preventDefault(); const act = t.dataset.act;
    if (act === 'opt') post(t.dataset.qid, { kind: 'answer', option: t.dataset.opt });
    else if (act === 'reply') {
      // Same rule as Slack (replyKind): typed text is a follow-up; the agent answers it or accepts it as the answer.
      const ta = t.closest('.need').querySelector('textarea'); const v = ta ? ta.value.trim() : '';
      if (v) { ta.value = ''; post(t.dataset.qid, { kind: 'followup', text: v }); }
    } else if (act === 'ctl') control(t);
    else if (act === 'copy') { const lab = t.dataset.label || t.textContent; t.dataset.label = lab; try { navigator.clipboard.writeText(t.dataset.text); t.textContent = 'Copied'; setTimeout(() => { t.textContent = lab; }, 1500); } catch (_) {} }
    else if (act === 'ctl-cancel') { const s = S.ctl[t.dataset.repo + '/' + t.dataset.id]; if (s) s.confirm = null; render(); }
    else if (act === 'tab') { S.tab[t.dataset.key] = t.dataset.tab; render(); }
    else if (act === 'dsel') { const m = S.dsel[t.dataset.key] = S.dsel[t.dataset.key] || {}; if (m[t.dataset.id]) delete m[t.dataset.id]; else m[t.dataset.id] = true; render(); }
    else if (act === 'dsel-all') { const r0 = S.runs.find(x => x.repo + '/' + x.id === t.dataset.key); if (r0) { const pk = r0.deferred.items.filter(dfPickable); const m = S.dsel[t.dataset.key] = S.dsel[t.dataset.key] || {}; const all = pk.every(i => m[i.id]); pk.forEach(i => { if (all) delete m[i.id]; else m[i.id] = true; }); render(); } }
    else if (act === 'fix-sel') fixSelected(t);
    else if (act === 'chat') sendChat(t);
    else if (act === 'plan-item') planItem(t);
    else if (act === 'summary') writeSummary(t);
    else if (act === 'blk') ruleBlock(t);
    else if (act === 'usage-refresh') { S.usageBusy = true; render(); fetch('/api/accounts/refresh', { method: 'POST' }).catch(() => {}).finally(() => load(['runs'])); }
    else if (act === 'toggle-done') { S.showAll[t.dataset.repo] = !S.showAll[t.dataset.repo]; render(); }
    return;
  }
  const a = e.target.closest ? e.target.closest('a[href]') : null;
  if (!a || a.target === '_blank' || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
  const h2 = a.getAttribute('href');
  if (h2 && (h2 === '/' || h2.indexOf('/questions') === 0 || h2.indexOf('/repo/') === 0)) { e.preventDefault(); navigate(h2); }
});
document.addEventListener('keydown', (e) => { if ((e.key === ' ' || e.key === 'Enter') && e.target.classList && e.target.classList.contains('dfc') && e.target.dataset.act === 'dsel') { e.preventDefault(); e.target.click(); } });
window.addEventListener('popstate', onRoute);
window.addEventListener('hashchange', () => { if (R.kind === 'questions') { scrollTarget = hashQ(); render(); } else onRoute(); });
setInterval(() => { if (!S.live) load(null); }, 60000);   // fallback while the event stream is down
setInterval(() => { if (!document.hidden) render(); }, 30000);
onRoute(); connect();
</script></body></html>`
