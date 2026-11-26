// The owner dashboard: one inline HTML + JS + CSS page, no external resources. All dynamic text goes in via textContent
// or createTextNode; the only innerHTML is the server's escaped markdown render of a dossier. Contract: references/owner-questions.md (Dashboard).
// The script avoids backticks and dollar-brace so it can live in this raw template.
import { vrWalkOrder } from "./vrwalk.ts"
import { vrDecisionStats } from "./vrdecision.ts"
import { VR_TEXT, vrFitLabel } from "./vrtext.ts"
import { packetBody, packetModel } from "./packet.ts"
import { paneDefault, paneRead, paneSlug, paneWrite, splitSections } from "./panes.ts"
import { fbCounts, fbEmpty, fbIsViewed, fbNote, fbPayload, fbPending, fbRead, fbSetNote, fbSetViewed, fbSettle, fbWrite } from "./feedback.ts"

export const PAGE = String.raw`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline · Owner questions</title>
<style>
:root{color-scheme:dark;--bg:#0e0f10;--fg:#e9e9e7;--mut:#8d8f94;--card:#151617;--raised:#1b1c1e;--bd:rgba(233,233,231,.09);--bd2:rgba(233,233,231,.2);--acc:#7da2f0;--accbg:rgba(125,162,240,.12);--shadow:none;--lift:0 8px 24px rgba(0,0,0,.5);
--ok:#5fcf80;--okbg:rgba(95,207,128,.12);--warn:#e0a84e;--warnbg:rgba(224,168,78,.13);--bad:#f0807a;--badbg:rgba(240,128,122,.13);--que:#b49af0;--quebg:rgba(180,154,240,.14);--chip:rgba(233,233,231,.07)}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 ui-sans-serif,-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif;font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased;background:var(--bg);color:var(--fg)}
a{color:var(--acc)}
header{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--bd);padding:10px 20px;display:flex;gap:12px 18px;align-items:center;flex-wrap:wrap}
header .brand{font-weight:600;font-size:1rem;color:var(--fg);text-decoration:none}
.crumbs{flex:1;min-width:0;color:var(--mut);font-size:13px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.crumbs a{color:var(--mut)}.crumbs a:hover{color:var(--acc)}.crumbs .cur{color:var(--fg);font-weight:600}
.pill{font-size:12px;color:var(--mut);display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mut);flex:none}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}.dot.info{background:var(--acc)}
main{max-width:1440px;margin:0 auto;padding:8px 24px 64px}
h1{font-size:1.5rem;font-weight:600;letter-spacing:-.01em;margin:24px 0 4px}h2{font-size:.8125rem;font-weight:500;letter-spacing:0;text-transform:none;color:var(--mut);margin:32px 0 12px;display:flex;align-items:center;gap:8px}
h2 .n{background:none;border-radius:0;padding:0;font-size:12px;letter-spacing:0;color:var(--mut)}
.lead{color:var(--mut);margin:0 0 6px;max-width:70ch}
.diskbar{border:1px solid var(--bad,#c0392b);border-left-width:4px;border-radius:10px;padding:10px 14px;margin:10px 0;background:var(--card)}
.mut{color:var(--mut)}.small{font-size:12px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{position:relative;display:flex;flex-direction:column;gap:7px;padding:16px 16px 14px;border:1px solid var(--bd);border-radius:12px;background:var(--card);box-shadow:var(--shadow);color:var(--fg);text-decoration:none;min-width:0;transition:border-color .12s,background-color .12s}
.card:hover,.card:focus-within{border-color:var(--bd2)}
a.card:focus-visible,.card a.stretch:focus-visible{outline:none}
.card:has(a.stretch:focus-visible),a.card:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
@media (prefers-reduced-motion:reduce){.card{transition:none}.card:hover,.card:focus-within{transform:none}}
.card .title{font-weight:600;font-size:1rem;line-height:1.3;padding-right:34px;color:var(--fg);text-decoration:none;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
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
.btn:disabled{opacity:.55;cursor:not-allowed}.btn.primary,.btn.confirm{background:var(--acc);border-color:var(--acc);color:#0e0f10}
.btn.confirm.stop{background:var(--bad);border-color:var(--bad)}
.ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:2px}
.ctl-msg{font-size:12px;color:var(--mut);overflow-wrap:anywhere}.ctl-msg.bad{color:var(--bad)}
.df-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-top:1px solid var(--bd);min-width:0}.df-row:first-of-type{border-top:0}.df-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.df-loc{font-size:11px;color:var(--mut);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.btn.sm{padding:2px 8px;font-size:12px}.btn.ghost{background:transparent;border-color:var(--bd);color:var(--mut)}.btn.ghost:hover{color:var(--fg)}.tx.md p{margin:.2em 0}.tx.md ul,.tx.md ol{margin:.2em 0 .2em 1.2em}.tx.md table{border-collapse:collapse;margin:.3em 0}.tx.md th,.tx.md td{border:1px solid var(--bd);padding:2px 8px;text-align:left}.b{font-weight:700}.ctl-msg.ok{color:var(--ok)}
.empty{color:var(--mut);padding:14px 4px}
.panel{border:1px solid var(--bd);border-radius:12px;background:var(--card);padding:16px 18px;box-shadow:var(--shadow);margin:14px 0}
.panel .row{display:flex;gap:10px 14px;flex-wrap:wrap;align-items:center;margin:6px 0}
.sum{color:var(--mut);max-width:80ch}
.md{max-width:86ch}.md h1,.md h2,.md h3{border-bottom:1px solid var(--bd);padding-bottom:3px;text-transform:none;letter-spacing:0;color:var(--fg);font-size:1.1rem;margin:22px 0 8px;display:block}
.md h1{font-size:1.35rem}.md table{border-collapse:collapse;margin:.5em 0;font-size:13px;display:block;max-width:100%;overflow-x:auto}.md th,.md td{border:1px solid var(--bd);padding:4px 10px;text-align:left;vertical-align:top}.md th{background:var(--chip);font-weight:600}.md a{color:var(--acc)}.md ul ul,.md ul ol,.md ol ul{margin:.15em 0}.md pre{background:var(--chip);padding:10px;border-radius:8px;overflow-x:auto;font-size:12.5px}.md code{background:var(--chip);padding:0 4px;border-radius:4px}
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
.need .q{font-size:1.02rem;font-weight:600;margin:.4em 0 .2em;overflow-wrap:anywhere}.q.md{font-weight:400;font-size:.95rem;line-height:1.45}.q.md p{margin:.45em 0}.q.md>p:first-child{margin-top:0}.q.md strong{font-weight:600}.q.md ul,.q.md ol{margin:.3em 0 .45em;padding-left:1.3em}.q.md li{margin:.15em 0}.q.md h4{margin:.6em 0 .2em;font-size:.95rem}.q.md hr{border:0;border-top:1px solid var(--bd);margin:.7em 0}.q.md .qh{font-weight:600;font-size:1.02rem;margin-bottom:.35em}.q.md .qdet{margin:.4em 0}.q.md .qdet summary{cursor:pointer;color:var(--mut);font-size:13px}.q.md code{font:.88em ui-monospace,monospace;background:var(--chip);padding:0 .25em;border-radius:4px}.blocks{color:var(--mut);font-size:13px;margin-bottom:6px}
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
.qchat{display:flex;flex-direction:column;gap:6px;margin:10px 0}.qchat .cm{max-width:88%;padding:7px 10px;border-radius:10px;border:1px solid var(--bd)}.qchat .cm.me{align-self:flex-end;background:var(--accbg)}.qchat .cm.them{align-self:flex-start}.qchat .cm .who{font-size:11px;color:var(--mut);margin-bottom:2px}.qchat .cm .tx{white-space:pre-wrap}.qchat .cm.wait{color:var(--mut);font-size:12px}.scard.clickable{cursor:pointer}.scard.clickable:hover{border-color:var(--acc)}.mergectl{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}.dfplan{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}.nowline{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0 0;color:var(--fg)}.nowline .nowtext{display:inline-flex;gap:6px;align-items:center;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.srow .nowline{margin:2px 0 0;color:var(--mut)}.nowline .chip{font-size:11px}.rsub{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.srow .sname>span:first-child{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip.fixing{display:inline-flex;align-items:center;gap:5px;background:var(--accbg);color:var(--acc);font-weight:600}.srow:has(.chip.fixing){box-shadow:inset 3px 0 0 var(--acc)}.chip.queued{display:inline-flex;align-items:center;gap:5px;background:var(--quebg);color:var(--que);font-weight:600}.dot.que{background:var(--que)}.srow:has(.chip.queued){box-shadow:inset 3px 0 0 var(--que)}
.dfp{border-left:3px solid var(--acc)}.dfp-bar{display:flex;height:9px;border-radius:5px;overflow:hidden;background:var(--chip);margin:6px 0 4px}.dfp-bar .seg{display:block;height:100%}
.seg.ok,.sw.ok{background:var(--ok)}.seg.acc,.sw.acc{background:var(--acc)}.seg.warn,.sw.warn{background:var(--warn)}.seg.mut,.sw.mut{background:var(--mut)}.seg.mut2,.sw.mut2{background:var(--bd)}
.dfp-leg{display:flex;gap:12px;flex-wrap:wrap;color:var(--mut)}.dfp-leg .sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.dfp-rows{margin-top:8px;border:1px solid var(--bd);border-radius:10px;overflow:hidden}.dfp-row{display:grid;grid-template-columns:max-content minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 12px;border-top:1px solid var(--bd);font-size:13px}.dfp-row:first-child{border-top:0}.dfp-t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.panel.blocked{border-left:3px solid var(--bad)}.blk{border-top:1px solid var(--bd);padding:10px 0 4px;display:flex;flex-direction:column;gap:6px}.blk-cmd{margin:0;padding:8px 10px;border-radius:8px;background:var(--chip);font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto}
.blk-rule{display:flex;gap:6px;flex-wrap:wrap}.blk-rule input{flex:1;min-width:220px;font-size:12px;padding:6px 8px;border:1px solid var(--bd);border-radius:8px;background:var(--bg);color:var(--fg)}
.psum{border-left:3px solid var(--acc)}.psum h3{font-size:.95rem;margin:12px 0 4px}.psum .md p:first-child{margin-top:0}
.res-hd{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px;color:var(--mut);margin-bottom:4px}.res-hd .kind{font-weight:600;color:var(--fg)}.dfp .res-hd .kind{font-size:14px}
.chat{margin:10px 0 0;padding:8px 12px;border-radius:10px;background:var(--chip);overflow-wrap:anywhere}.chat.owner{margin-left:12%;background:var(--accbg)}.chat.agent,.chat.runner{margin-right:6%}.chat.runner{color:var(--mut)}.chat .md p:first-child{margin-top:0}.chat .md p:last-child{margin-bottom:0}
.cbx{width:18px;height:18px;padding:0;border:1px solid var(--bd);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;line-height:16px;cursor:pointer}.cbx.on{background:var(--acc);border-color:var(--acc);color:#fff}.dfbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 12px;border:1px solid var(--bd);border-radius:12px;background:var(--card);position:sticky;top:8px;z-index:2;box-shadow:var(--shadow)}.dfbar .grow{flex:1}.dfnote{margin:8px 2px 12px}.dfcards{display:flex;flex-direction:column;gap:10px}.dfc{border:1px solid var(--bd);border-radius:12px;background:var(--card);padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0}.dfc.pick{cursor:pointer}.dfc.pick:hover{border-color:var(--acc)}.dfc.on{border-color:var(--acc);background:var(--accbg)}.dfc.closed{opacity:.75}.dfc .cbx{pointer-events:none;flex:none}.dfc-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.dfc-h .grow{flex:1}.dfc-t{font-weight:600;line-height:1.35;overflow-wrap:anywhere}.dfc-loc{font-size:12px;color:var(--mut);overflow-wrap:anywhere;white-space:normal}.dfc-f{display:flex;gap:6px;flex-wrap:wrap}.dfact{font-size:12px;border:1px solid var(--bd);border-radius:999px;padding:1px 9px}.dfact .l{color:var(--mut);margin-right:5px}.dfact.bad{border-color:var(--bad);color:var(--bad)}.dfact.mut{color:var(--mut)}.dfc-why{font-size:12.5px;color:var(--mut);line-height:1.4}.dfc-a{display:flex;justify-content:flex-end}
textarea{width:100%;min-height:3.4em;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:10px;padding:8px;margin:6px 0;resize:vertical}
.err{color:var(--bad);font-size:13px;min-height:1em}
details>summary{cursor:pointer;color:var(--mut);font-size:12.5px;margin:4px 0}
.qa{margin:6px 0;font-size:13px}.qa .a{color:var(--ok)}
iframe.report{display:block;width:100%;height:900px;border:0;border-radius:12px;background:transparent}
.btn.lg{font-size:14px;font-weight:600;padding:7px 18px;border-radius:10px}.btn.lg.go{background:var(--acc);border-color:var(--acc);color:#0e0f10}.btn.lg.go:disabled{background:var(--chip);border-color:var(--bd);color:var(--mut)}
.vrintent{max-width:860px;padding:12px 4px;line-height:1.55}.vrintent h3{margin:0 0 8px}.vrintent p,.vrintent ul{margin:0 0 10px}.tabs{display:flex;gap:4px;margin:16px 0 8px;border-bottom:1px solid var(--bd)}.tab{font:inherit;font-size:13px;padding:6px 14px;border:0;border-bottom:2px solid transparent;background:none;color:var(--mut);cursor:pointer}.tab.on{color:var(--fg);border-bottom-color:var(--acc);font-weight:600}
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
.shead{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}.shead .chips{display:flex;gap:6px;flex-wrap:wrap}.sgrps{display:flex;flex-direction:column;gap:12px;margin-top:8px}.sgrp-h{font-size:12px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.03em;margin:0 0 5px;cursor:default}details.sgrp>summary.sgrp-h{cursor:pointer}.srows{border:1px solid var(--bd);border-radius:10px;overflow:hidden}.srow{display:grid;grid-template-columns:max-content minmax(0,1fr) auto;gap:12px;align-items:center;padding:8px 12px;border-top:1px solid var(--bd);color:var(--fg);text-decoration:none;font-size:13px}.srow:first-child{border-top:0}.srow:hover{background:var(--accbg)}.srow.done{color:var(--mut)}.srow .sid{font-weight:700}.srow .sname{color:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.srow.done .sname{color:var(--mut)}.sright{display:flex;gap:6px;align-items:center;justify-content:flex-end;flex-wrap:wrap}.sright>.chip{font-size:11px}@media (max-width:640px){.srow{grid-template-columns:max-content minmax(0,1fr)}.sright{grid-column:1/-1;justify-content:flex-start}}.scol.full{margin:22px 0}.scol.full>h2{margin:0 0 10px}.scol.full .scard{padding:14px 18px}.scol.full .scard .title{font-size:1rem}
ul.tree.wide{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));column-gap:18px;border-left:0;padding-left:0}ul.tree.wide>li{padding-left:0}ul.tree.wide>li::before{display:none}
details.mstubs{margin-top:10px}details.mstubs>summary{font-size:.78rem;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);font-weight:700}
@media (max-width:1000px){.cols{grid-template-columns:minmax(0,1fr)}.cols>.scol{position:static;max-height:none;overflow:visible;order:1}.cols>.main-col{order:2}}
.dmwrap{display:grid;grid-template-columns:260px 1fr;gap:16px;align-items:start}.dmlist{display:flex;flex-direction:column;gap:6px;position:sticky;top:64px;max-height:calc(100vh - 90px);overflow:auto}.dmitem{display:block;padding:8px 10px;border:1px solid var(--bd);border-radius:10px;background:var(--card);color:var(--fg);text-decoration:none}.dmitem:hover{border-color:var(--acc)}.dmitem.cur{border-color:var(--acc);background:var(--accbg)}.dmt{font-weight:600;font-size:13px;display:flex;gap:6px;align-items:center;overflow:hidden;text-overflow:ellipsis}.dmthread{min-width:0}.dmthread .panel{margin-top:0}.chat.wait{color:var(--mut)}.dmhint{margin-left:8px}a.pill.dmlink{color:var(--acc);text-decoration:none;font-weight:600}
@media (max-width:760px){.dmwrap{grid-template-columns:1fr}.dmlist{position:static;max-height:none}}
@media (max-width:560px){header{padding:8px 16px}main{padding:4px 16px 56px}.msg{max-width:96%}.grid{grid-template-columns:1fr}}
.vr{padding:12px 14px}.vrbar{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}.vrh{font-size:1rem;margin:0 8px 0 0}.vrgrp{display:inline-flex;gap:4px}
.vrbar .btn[aria-pressed=true]{border-color:var(--acc);background:var(--accbg);font-weight:650}.vrbar .btn:disabled{opacity:.45;cursor:default}
.vrleg{display:flex;flex-wrap:wrap;gap:4px 14px;margin:8px 0}.vrlg{display:inline-flex;align-items:center;gap:4px}
.vrsw,.vrbadge{display:inline-block;min-width:16px;padding:0 4px;text-align:center;font-style:normal;font-weight:700;border-radius:4px;border:2px solid var(--mut);color:var(--mut);background:var(--chip);line-height:1.2}
.vrsw.added,.vrbadge.added{border-color:var(--ok);color:var(--ok);background:var(--okbg)}.vrsw.changed,.vrbadge.changed{border-color:var(--warn);color:var(--warn);background:var(--warnbg)}.vrsw.removed,.vrbadge.removed{border-color:var(--bad);color:var(--bad);background:var(--badbg);border-style:dashed}
.vrbody{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:12px;align-items:stretch}@media (max-width:760px){.vrbody{grid-template-columns:1fr}}
.vrsvg{width:100%;height:clamp(300px,58vh,560px);background:var(--bg);border:1px solid var(--bd);border-radius:10px;touch-action:none;cursor:grab;display:block}.vrsvg.drag{cursor:grabbing}
.vrsvg .vroff{display:none}.vrsvg g.vrn,.vrsvg g.vre,.vrsvg g.vrg{cursor:pointer;outline:none}.vrsvg g.sel>rect:not([stroke=transparent]),.vrsvg g.sel>.vrshp{stroke-width:4}.vrsvg g:focus-visible>rect,.vrsvg g:focus-visible>.vrshp{stroke-width:4}.vrshk{margin-left:8px}.vrshk b{font-weight:400}.vrsvg g.vre.sel .vrline{stroke-width:4}
.vrinsp{border:1px solid var(--bd);border-radius:10px;padding:10px 12px;overflow:auto;max-height:560px;background:var(--card)}.vrinsp h3{margin:0 0 6px;font-size:.95rem}.vrinsp p{margin:5px 0}.vrev{word-break:break-all;font-size:12px}
.vrnote{margin-top:6px}.vrmm{margin-top:8px;display:flex;gap:8px;align-items:flex-start}.vrta{flex:1;font:12px/1.4 ui-monospace,monospace;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:8px;padding:6px}
.vrparts{margin:8px 0;padding:6px 10px;border:1px solid var(--bd);border-radius:6px}.vrpart{padding:6px 0;border-top:1px solid var(--bd)}.vrpart.none{opacity:.85}.vrpcm{display:block;width:100%;margin:4px 0}.vrpv{margin:8px 0;padding:8px 10px;border:1px dashed var(--acc);background:var(--accbg);color:var(--fg);border-radius:8px;font-size:.88rem}.vrban{margin:8px 0;padding:8px 10px;border:1px solid var(--warn);background:var(--warnbg);color:var(--fg);border-radius:8px;font-size:.88rem}
.vrrev{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin-top:8px}.vrsum{font-weight:650}.vrprev{margin-top:8px}.vrprev textarea{width:100%;min-height:120px;font:12px/1.4 ui-monospace,monospace;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:8px;padding:6px}
.vrvd{margin-top:10px;padding-top:8px;border-top:1px solid var(--bd)}.vrvd .vrgrp{flex-wrap:wrap;margin:6px 0}.vrvd textarea{width:100%;min-height:60px;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:8px;padding:6px}
.vrvd .btn[aria-pressed=true]{border-color:var(--acc);background:var(--accbg);font-weight:650}.vrcode{font-size:11px;font-weight:700}
.vr [hidden]{display:none!important}.vrcv{position:relative;min-width:0;outline:none}
.vrhc{position:absolute;z-index:5;max-width:340px;padding:8px 10px;background:var(--card);border:1px solid var(--bd);border-radius:8px;box-shadow:var(--shadow);font-size:12px;line-height:1.4;pointer-events:none;overflow-wrap:anywhere}.vrhcm{margin:4px 0 0;padding-left:16px;font-family:var(--mono,monospace);font-size:11px}.vrhc b{display:block;font-size:13px;margin-bottom:2px}.vrhc .mut{color:var(--mut)}.vrhc p{margin:3px 0 0}
.vrov{position:absolute;right:8px;bottom:8px;padding:3px;background:var(--card);border:1px solid var(--bd);border-radius:6px;opacity:.94;cursor:crosshair;touch-action:none;box-shadow:var(--shadow)}.vrov svg{display:block}
.vrov rect{vector-effect:non-scaling-stroke;fill:none;stroke:var(--mut);stroke-width:1}.vrov .vrovn{fill:var(--chip)}.vrov .vrovn.added{stroke:var(--ok);fill:var(--okbg);stroke-width:2}.vrov .vrovn.changed{stroke:var(--warn);fill:var(--warnbg);stroke-dasharray:1.5 1.5;stroke-width:2}.vrov .vrovn.removed{stroke:var(--bad);fill:var(--badbg);stroke-dasharray:4 2;stroke-width:2}.vrov .vroff{display:none}
.vrov .vrovvp{fill:var(--accbg);fill-opacity:.35;stroke:var(--acc);stroke-width:2;stroke-dasharray:none}
.vrwalk{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:8px 0;padding:6px 10px;border:1px solid var(--acc);background:var(--accbg);border-radius:8px}.vrwl{font-weight:600;overflow-wrap:anywhere}
.vrhalo{pointer-events:none}.vrsvg.vrwk g.vrn:not(.walk),.vrsvg.vrwk g.vre:not(.walk),.vrsvg.vrwk g.vrg:not(.walk){opacity:.4}.vrsvg g.vre.walk .vrline{stroke-width:5;stroke:var(--acc)}.vrsvg .vrhd{cursor:pointer}
body.vrfs-on{overflow:hidden}body.vrfs-on .vr[data-vrkey]{position:fixed;inset:0;z-index:50;margin:0;border:0;border-radius:0;background:var(--bg);padding:10px 16px;display:flex;flex-direction:column;overflow:hidden}body.vrfs-on .vr[data-vrkey] .vrbody{flex:1;min-height:0;grid-template-rows:minmax(0,1fr)}body.vrfs-on .vr[data-vrkey] .vrcv{height:100%}body.vrfs-on .vr[data-vrkey] .vrsvg{height:100%}body.vrfs-on .vr[data-vrkey] .vrinsp{max-height:none}
.vrmaplist{margin:6px 0;padding:0;list-style:none}.vrmaplist li{display:flex;gap:8px;align-items:center;padding:4px 0;border-top:1px solid var(--bd)}.vrmaplist .grow{flex:1}.vrpre{white-space:pre-wrap;font:12px/1.4 ui-monospace,monospace;background:var(--bg);border:1px solid var(--bd);border-radius:8px;padding:8px;max-height:360px;overflow:auto}
/* ---- v2 refinement: quiet chrome, one accent, aligned run rows ---- */
header{padding:10px 24px}
.chip{font-size:11.5px;padding:0 8px;border-radius:6px;background:var(--chip);color:var(--mut)}
.chip.info{color:var(--acc)}.chip.ok{color:var(--ok)}.chip.warn{color:var(--warn)}.chip.bad{color:var(--bad)}
.badge{font-weight:600;border-radius:8px;color:var(--mut)}
.btn{border-radius:8px;padding:4px 12px;background:var(--raised);transition:border-color .12s,background-color .12s}
.btn:hover:not(:disabled){border-color:var(--bd2)}
.btn.lg{font-weight:500}
.opt{border-radius:10px;transition:border-color .12s,background-color .12s}.opt:hover{border-color:var(--bd2)}
.acard{border-radius:12px;padding:16px 18px;gap:10px;box-shadow:none}.acard.next{border-color:var(--bd2)}
.aname{font-weight:600}.meter .mv{font-weight:500}
.mbar{height:4px;border-radius:2px;margin:6px 0 4px}.mfill.ok{background:var(--fg);opacity:.7}
.srows{border-radius:12px;background:var(--card);display:grid;grid-template-columns:max-content minmax(0,1fr) auto}
.srow{display:grid;grid-template-columns:subgrid;grid-column:1/-1;gap:20px;padding:10px 16px;transition:background-color .12s}
.srow:hover{background:rgba(233,233,231,.04)}
.srow .sid{font-weight:500;color:var(--mut);max-width:11rem;overflow:hidden;text-overflow:ellipsis}
.srow .sname>span:first-child{color:var(--fg)}
.srow.done .sname>span:first-child{color:var(--mut)}
.sgh{grid-column:1/-1;display:flex;gap:8px;align-items:baseline;padding:10px 16px 6px;font-size:12px;color:var(--mut);border-top:1px solid var(--bd);background:rgba(233,233,231,.025)}
.sgh:first-child{border-top:0}.sgh b{font-weight:500;color:var(--fg)}
.sright{gap:14px;flex-wrap:nowrap}
.sright>.chip{background:none;padding:0;font-size:12px;color:var(--mut)}
.sright>.chip.info{color:var(--acc)}.sright>.chip.ok{color:var(--ok)}.sright>.chip.warn{color:var(--warn)}.sright>.chip.bad{color:var(--bad)}.sright>.chip.queued{color:var(--que)}
.sright>.chip:first-child:not(.fixing)::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.rmeta{font-size:11.5px;color:var(--mut);white-space:nowrap}
.srow:has(.chip.fixing),.srow:has(.chip.queued){box-shadow:none}
.nowline .chip{background:none;padding:0}
.sright>.chip.fixing{color:var(--acc)}
.empty{padding:16px 4px}
.skel{display:flex;flex-direction:column;gap:10px;margin-top:28px}.skel i{display:block;height:44px;border-radius:12px;background:var(--card);border:1px solid var(--bd)}.skel i:first-child{height:132px}
@media (max-width:640px){.srows{display:block}.srow{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:12px}.sright{flex-wrap:wrap}}
@media (prefers-reduced-motion:reduce){.btn,.opt,.srow,.card{transition:none}}
/* Dossier page: sticky status header, Needs you now strip, section rail, collapsible panes. Colours are the theme tokens only. */
:root{--hh:49px}
.dhead{position:sticky;top:var(--hh);z-index:15;margin:8px 0 12px;padding:10px 16px;display:flex;flex-direction:column;gap:4px}
.dhead .drow{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;min-width:0}
.dhead .dtitle{flex:1 1 280px;min-width:0;margin:0;font-size:1.1rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dhead .dctl{margin:0}.dhead .ctl{margin:0}.dhead .chips{margin:0}.dhead .nowline{margin:0}
.chipbtn{font:inherit;font-size:11.5px;border:0;cursor:pointer}
.needs-strip{border-left:4px solid var(--warn);margin:0 0 14px}
.needs-strip .panel,.needs-strip .need{box-shadow:none}
.needs-strip>.panel.result{border:0;background:none;padding:6px 0 0;margin:6px 0 0;border-top:1px solid var(--bd);border-radius:0}
.nrow{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;padding:8px 0;border-top:1px solid var(--bd)}.nrow .grow{flex:1;min-width:0}
.nowait{color:var(--mut);padding:4px 0}
.fbbar{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;margin:0 0 10px;padding:6px 8px;border:1px dashed var(--bd);border-radius:8px}
.fbbar .fbview,.fbbar .fbblk{display:inline-flex;align-items:center;gap:4px;font-size:12.5px;cursor:pointer}
.fbbar .btn.on{background:var(--fg);color:var(--bg)}
.fbbar textarea{flex:1 1 100%;min-height:52px;font:inherit}
.fbhead{display:inline-flex;align-items:center;gap:8px}
.dlayout{display:block}
.rail{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}
.rail-i{font:inherit;font-size:12.5px;display:flex;flex-wrap:wrap;align-items:center;gap:8px;row-gap:2px;text-align:left;padding:5px 10px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer}
.rail-i:hover{border-color:var(--bd2)}.rail-t{flex:1 1 auto;min-width:0;overflow-wrap:anywhere}.rail-b{color:var(--mut);font-size:11.5px;margin-left:auto;text-align:right}
@media (min-width:1100px){.dlayout.withrail{display:grid;grid-template-columns:220px minmax(0,1fr);gap:18px;align-items:start}.rail{flex-direction:column;flex-wrap:nowrap;position:sticky;top:calc(var(--hh) + 110px);margin:0;max-height:calc(100vh - var(--hh) - 130px);overflow:auto}}
.panes{min-width:0}
details.pane{border:1px solid var(--bd);border-radius:12px;background:var(--card);margin:0 0 10px;scroll-margin-top:calc(var(--hh) + 120px)}
details.pane>summary{display:flex;align-items:center;gap:10px;margin:0;padding:10px 14px;font-size:14px;font-weight:600;color:var(--fg);cursor:pointer;list-style:none;border-radius:12px}
details.pane>summary::-webkit-details-marker{display:none}
details.pane>summary::before{content:"\25B8";color:var(--mut);display:inline-block;width:1em;transition:transform .12s}
details.pane[open]>summary::before{transform:rotate(90deg)}
details.pane>summary:hover{background:var(--raised)}
details.pane .pbody{padding:0 14px 12px}
details.pane.flat>.pbody>.panel,details.pane.flat>.pbody>section.panel{border:0;background:none;box-shadow:none;padding:0;margin:0}
details.pane.flat>.pbody>.panel>.res-hd .kind{display:none}
.pk-wrap{overflow-x:auto;margin:4px 0 8px}table.pk{border-collapse:collapse;font-size:12.5px;width:100%}table.pk th,table.pk td{border-top:1px solid var(--bd);padding:3px 8px;text-align:left;vertical-align:top}table.pk th{color:var(--mut);font-weight:600;white-space:nowrap}.pk-h{font-size:12px;font-weight:600;color:var(--mut);margin:8px 0 0}a.pk-a,code.pk-a{font-family:var(--mono,ui-monospace,monospace);font-size:12px;word-break:break-all}
details.pane.sub{margin:6px 0;background:var(--bg)}details.pane.sub>summary{font-size:13px}
details.qrow{border-top:1px solid var(--bd);margin:0}details.qrow>summary{display:flex;gap:12px;align-items:baseline;padding:6px 0;font-size:13px;color:var(--fg)}.qrq{flex:1 1 40%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.qra{flex:1 1 40%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.loading{color:var(--mut);padding:10px 4px;display:flex;align-items:center;gap:8px}.skel-t{color:var(--mut);margin:0 0 6px;display:flex;align-items:center;gap:8px}
details.pane>summary:focus-visible,.rail-i:focus-visible,.vrmore>summary:focus-visible,details.qrow>summary:focus-visible,.chipbtn:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.vrmore{position:relative;display:inline-block}.vrmore>summary{display:inline-block;list-style:none;margin:0;font-size:12.5px;padding:3px 11px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer}.vrmore>summary::-webkit-details-marker{display:none}
.vrmore .vrmenu{position:absolute;right:0;top:calc(100% + 4px);z-index:30;display:flex;flex-direction:column;gap:4px;padding:8px;min-width:150px;border:1px solid var(--bd2);border-radius:10px;background:var(--raised);box-shadow:var(--shadow)}
.vrmore .vrmenu .btn{text-align:left}
.rulelink{margin:6px 2px 24px}
@media (prefers-reduced-motion:reduce){details.pane>summary::before{transition:none}}
</style></head><body>
<header><a class="brand" href="/">Pipeline</a><nav class="crumbs" id="crumbs" aria-label="Breadcrumb"></nav><a class="pill dmlink" href="/chat" title="Talk to the agent, as in the Slack DM">Ask the agent</a><span class="pill" id="slack"></span><span class="pill" id="upd"></span></header>
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
const TONE = { planned: 'neutral', ready: 'info', building: 'info', review: 'warn', pr: 'warn', done: 'ok', dropped: 'neutral', running: 'info', testing: 'info', waiting: 'warn', queued: 'queued', failed: 'bad', stopped: 'bad', gap: 'warn', 'contract-change': 'warn', unknown: 'neutral',
  accepting: 'info', integrating: 'warn', merged: 'ok', blocked: 'bad', green: 'ok', red: 'bad', pending: 'warn', open: 'warn' };
function laneChip(r) { return r && r.delivery === 'kanban' ? h('span', { class: 'chip info', title: 'Kanban lane: small fix, proven by CI only' }, ['kanban']) : null; }
function chip(text, kind, extra) { return h('span', { class: 'chip ' + (TONE[kind || text] || '') + (extra ? ' ' + extra : '') }, [text]); }
/* A stub's PR being open says nothing about whether anything is working on it. */
function stubChip(s) { return s.stopped ? h('span', { title: s.stoppedWhy || 'Nothing is working on it. Integrate or Resume /stub picks it up again.' }, [chip('integration stopped', 'blocked')]) : chip(s.phase || s.state, s.state); }
function prChip(pr) {
  const tone = pr.red ? 'red' : pr.pending ? 'pending' : pr.green ? 'green' : 'neutral';
  const a = h('a', { class: 'chip act ' + (TONE[tone] || ''), href: pr.url, target: '_blank', rel: 'noopener', title: 'checks: ' + pr.green + ' green, ' + pr.red + ' red, ' + pr.pending + ' pending' }, [h('span', { class: 'dot' }), 'PR #' + pr.number + ' · ' + pr.green + '/' + pr.red + '/' + pr.pending]);
  return a;
}

// ---- visual review (VR-03): the change graph of a dossier. The server lays it out (ELK); this only draws boxes and points.
// Hand-written SVG, every text is set as text content, no markup parsing on this path, no outside request.
const VR = {}, VRS = {}, VRPV = {}; /* VRPV: per dossier, whether the "All diagrams (preview)" view is shown instead of the review */
const VR_NS = 'ht' + 'tp://www.w3.org/2000/svg', VR_MARK = { added: '+', changed: '~', removed: '−', context: '=' };
const VR_PASS = { diff: () => true, before: (s) => s !== 'added', after: (s) => s !== 'removed' };
const VR_MODES = [['before', 'Before'], ['after', 'After'], ['diff', 'Diff']];
const VR_TABS = { L0: 'Features', L1: 'Flow', decision: 'Decisions', L2: 'Components' };
${vrWalkOrder.toString()}
${vrDecisionStats.toString()}
${paneRead.toString()}
${paneWrite.toString()}
${paneDefault.toString()}
${paneSlug.toString()}
${splitSections.toString()}
${fbEmpty.toString()}
${fbRead.toString()}
${fbWrite.toString()}
${fbSetNote.toString()}
${fbSetViewed.toString()}
${fbNote.toString()}
${fbIsViewed.toString()}
${fbCounts.toString()}
${fbPayload.toString()}
${fbPending.toString()}
${fbSettle.toString()}
${packetModel.toString()}
${packetBody.toString()}
function vrKey(repo, id) { return repo + '/' + id; }
async function vrLoad(repo, id, set) {
  const key = vrKey(repo, id) + (set ? '~all' : ''); let d = null;
  try { const r = await fetch('/api/visual-review?dossier=' + enc(id) + '&repo=' + enc(repo) + (set ? '&set=' + enc(set) : '')); d = r.ok ? await r.json() : null; } catch (e) { d = null; }
  const had = VR[key] && VR[key].data ? VR[key].data.rev : null;
  if (d && d.review) d.shownHashes = Object.assign({}, d.review.hashes || {}); /* the hashes of the graphs this load displays: a verdict says which one the owner saw */
  VR[key] = { data: d };
  if (d && d.review) { const st = VRS[key] = VRS[key] || {}; st.review = d.review; if (st.paint) st.paint(); }
  vrMapLoad(repo);
  if ((d ? d.rev : null) !== had) render();
}
/* The review packet (packet.ts, GET /api/review-packet): one pane per packet file that exists, none for a missing one. */
const PKT = {};
async function pktLoad(repo, id) {
  const key = vrKey(repo, id); let d = null;
  try { const r = await fetch('/api/review-packet?dossier=' + enc(id) + '&repo=' + enc(repo)); d = r.ok ? await r.json() : null; } catch (e) { d = null; }
  const had = JSON.stringify(PKT[key] || null); PKT[key] = d;
  if (JSON.stringify(d) !== had) render();
}
/* VR-06: the committed product map of a repo (stale banner, the diagram list), from the server's cached vr_map check. */
const VRM = {};
async function vrMapLoad(repo) {
  let d = null; try { const r = await fetch('/api/visual-review/map?repo=' + enc(repo)); d = r.ok ? await r.json() : null; } catch (e) { d = null; }
  const had = JSON.stringify(VRM[repo] && VRM[repo].status || null); const cur = VRM[repo] = VRM[repo] || {}; cur.status = d;
  if (JSON.stringify(d) !== had) { render(); const st = VRS[repo + '/~map']; if (st && st.paint) st.paint(); }
}
let vrOpenSeq = 0;
async function vrOpenDiagram(repo, path) {
  const seq = ++vrOpenSeq; /* a slow earlier answer must not overwrite a later click */
  const cur = VRM[repo] = VRM[repo] || {}; cur.open = path; cur.text = ''; cur.err = '';
  if (R.kind === 'arch' && R.repo === repo && new URLSearchParams(location.search).get('d') !== path) history.replaceState(null, '', hrefArch(repo, path));
  let d = null; try { const r = await fetch('/api/visual-review/diagram?repo=' + enc(repo) + '&path=' + enc(path)); d = await r.json(); if (!r.ok) { cur.err = d.error || ('HTTP ' + r.status); d = null; } } catch (e) { cur.err = 'request failed'; }
  if (seq !== vrOpenSeq) return;
  const key = repo + '/~map';
  if (d && d.kind === 'mermaid') { cur.text = d.text; VR[key] = { data: null }; }
  else if (d) { VR[key] = { data: { dossier: '', rev: path + ':' + Date.now(), views: { L0: d.views.L0 || null, L1: d.views.L1 || null, L2: d.views.L2 || null }, issues: [], mapRepo: repo, mapPath: path } }; if (!d.views.L0 && !d.views.L1 && !d.views.L2) { cur.err = 'The server sent no view for this diagram.'; VR[key] = { data: null }; } }
  render();
}
function vrOpenQ(dossier, repo) {
  return S.q.find(q => q.state === 'open' && q.dossier === dossier && qRepo(q) === repo && q.topic === 'visual-review' && q.qkind === 'product' && q.options.some(o => /^changes requested/i.test(o.label)) && q.options.some(o => /^approved/i.test(o.label)));
}
/* The repo page links to the architecture page (the product map and the diagrams live there, not among the dossiers). */
function vrRepoLink(repo) {
  const st = (VRM[repo] || {}).status; if (!st) return null;
  const n = st.diagrams.length, what = (st.hasMap ? 'Product map' + (st.stale ? ' (stale: ' + st.stale + ')' : '') + ' · ' : '') + n + ' diagram' + (n === 1 ? '' : 's');
  return h('section', { class: 'panel', 'data-k': 'vrlink' }, [h('h2', {}, ['Architecture']), h('p', { class: 'small' }, [h('a', { href: hrefArch(repo) }, ['Open the architecture page']), ' · ' + what])]);
}
function archView(repo) {
  const kids = [h('h1', {}, [rlabel(repo) + ' · Architecture']), h('p', { class: 'lead' }, ['The committed product map and the diagrams in docs/architecture. Open one on the canvas; Full screen (or f) gives it the whole window.'])];
  const p = vrRepoPanel(repo); kids.push(p || h('div', { class: 'empty' }, ['Loading…']));
  return h('div', {}, kids);
}
/* The architecture page: the committed product map (stale banner) and the committed diagrams, each openable on the canvas. */
function vrRepoPanel(repo) {
  const m = VRM[repo] || {}; const st = m.status; if (!st) return null;
  const kids = [h('h2', {}, ['Product map and diagrams'])];
  if (!st.hasMap) kids.push(h('p', { class: 'small mut' }, [st.message || 'No committed product map yet.']));
  else if (st.stale) kids.push(h('div', { class: 'vrban' }, ['The product map is stale: ' + st.stale + ' element' + (st.stale === 1 ? '' : 's') + ' (' + st.ids.join(', ') + ').']));
  else if (st.message) kids.push(h('p', { class: 'small mut' }, [st.message]));
  else kids.push(h('p', { class: 'small mut' }, ['The product map is up to date.']));
  if (st.diagrams.length) kids.push(h('ul', { class: 'vrmaplist' }, st.diagrams.map(d => h('li', {}, [h('span', { class: 'chip neutral' }, [d.kind]), h('span', { class: 'grow' }, [d.title + ' · ' + d.path]),
    h('button', { class: 'btn sm', type: 'button', 'data-act': 'vr-open', 'data-repo': repo, 'data-path': d.path }, [m.open === d.path ? 'Open (shown)' : 'Open on canvas'])]))));
  else kids.push(h('p', { class: 'small mut' }, ['No committed diagrams in docs/architecture.']));
  if (m.err) kids.push(h('div', { class: 'ctl-msg bad' }, [m.err]));
  if (m.text) kids.push(h('pre', { class: 'vrpre' }, [m.text]));
  const v = VR[repo + '/~map']; const d = v && v.data;
  if (d) kids.push(h('section', { class: 'vr', 'data-k': 'vr', 'data-raw': '1', 'data-html': '', 'data-vrkey': repo + '/~map@' + d.rev }, []));
  return h('section', { class: 'panel', 'data-k': 'vrmap' }, kids);
}
/* The panel above the dossier's questions: a host the morph leaves alone (data-raw, constant data-html); vrMount fills it. */
function vrPanel(r) {
  const k0 = vrKey(r.repo, r.id) + (VRPV[vrKey(r.repo, r.id)] ? '~all' : ''); const v = VR[k0]; const d = v && v.data;
  if (!d || (!['L0', 'L1', 'L2'].some(k => d.views[k]) && !d.previewAll)) return null;
  return h('section', { class: 'panel vr', 'data-k': 'vr', 'data-raw': '1', 'data-html': '', 'data-vrkey': k0 + '@' + d.rev }, []);
}
const VR_FS = { off: null, toggle: null, fit: null };
document.addEventListener('keydown', (e) => {
  if (!document.querySelector('.vr[data-vrkey]')) return;
  const tg = e.target && e.target.tagName; if (tg === 'INPUT' || tg === 'TEXTAREA' || tg === 'SELECT' || (e.target && e.target.isContentEditable)) return;
  if (e.key === 'Escape' && document.body.classList.contains('vrfs-on') && !document.querySelector('.vrwalk:not([hidden])') && VR_FS.off) { VR_FS.off(); e.preventDefault(); }
  else if (e.key === 'f' && !e.metaKey && !e.ctrlKey && !e.altKey && VR_FS.toggle) { VR_FS.toggle(); e.preventDefault(); }
});
window.addEventListener('resize', () => { if (document.body.classList.contains('vrfs-on') && VR_FS.fit) VR_FS.fit(); });
function vrMount() {
  const host = document.querySelector('.vr[data-vrkey]'); if (!host) { document.body.classList.remove('vrfs-on'); VR_FS.off = VR_FS.toggle = VR_FS.fit = null; return; }
  const tag = host.getAttribute('data-vrkey'); if (host.__vr === tag) { const k0 = tag.split('@')[0]; if (VRS[k0] && VRS[k0].paintBar) VRS[k0].paintBar(); return; }
  host.__vr = tag; const key = tag.split('@')[0]; const v = VR[key];
  host.replaceChildren(); if (v && v.data) vrBuild(host, key, v.data);
}
function vrEl(n, a, p) { const e = document.createElementNS(VR_NS, n); for (const k in a || {}) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; }
function vrDom(tag, cls, text, p) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; if (p) p.appendChild(e); return e; }
function vrEv(ev, hand) { return ev.tier === 'source' ? (ev.path || '?') + (ev.line ? ':' + ev.line + (ev.end_line && ev.end_line !== ev.line ? '-' + ev.end_line : '') : '') + (ev.note ? ' — ' + ev.note : '') : (hand ? 'authored: ' : 'inferred: ') + (ev.note || ''); }
/* A hand-authored graph (meta.generated_by "hand", e.g. imported from Mermaid) carries tier "inferred" evidence only because no tool proved it; there the dashed style follows the authored arrow instead, and no "? inferred" badge is drawn. */
function vrHand(g) { return !!(g && g.meta && g.meta.generated_by === 'hand'); }
function vrInferred(e, g) { if (vrHand(g)) return /\./.test(e.arrow || ''); return e.resolution ? e.resolution === 'inferred' : (e.evidence || []).length > 0 && e.evidence.every(x => x.tier === 'inferred'); }
const VR_TEXT = ${JSON.stringify(VR_TEXT)};
${vrFitLabel.toString()}
/* Node shapes (see nodeShape in vrlayout.ts: the layout names the shape of each non-rectangle in L.shapes and sizes the box for it). Shape is the node's type; colour stays the change status. */
const VR_REFCH = 6.4; /* px a character of a ref header (11 px text) */
const VR_CENTER = { terminal: 1, diamond: 1, io: 1, circle: 1, cylinder: 1 };
/* Draws the outline of shape sh in box b with the given paint attributes; returns the elements (the first carries class vrshp). */
function vrShapeEl(sh, b, at, p) {
  const x = b.x, y = b.y, w = b.w, h = b.h, cx = x + w / 2, cy = y + h / 2, pts = a => a.map(q => q[0] + ',' + q[1]).join(' ');
  const mk = (n, a) => vrEl(n, Object.assign({ class: 'vrshp' }, a, at), p);
  if (sh === 'diamond') return mk('polygon', { points: pts([[cx, y], [x + w, cy], [cx, y + h], [x, cy]]) });
  if (sh === 'io') return mk('polygon', { points: pts([[x + VR_TEXT.ioSkew, y], [x + w, y], [x + w - VR_TEXT.ioSkew, y + h], [x, y + h]]) });
  if (sh === 'asymmetric') return mk('polygon', { points: pts([[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x + 10, cy]]) });
  if (sh === 'junction') return mk('circle', { cx: cx, cy: cy, r: Math.min(w, h) / 2 - 1, fill: at.stroke });
  if (sh === 'circle') return mk('ellipse', { cx: cx, cy: cy, rx: w / 2, ry: h / 2 });
  if (sh === 'cylinder') { const ry = 7, e = mk('path', { d: 'M' + x + ',' + (y + ry) + ' A' + w / 2 + ',' + ry + ' 0 0 1 ' + (x + w) + ',' + (y + ry) + ' V' + (y + h - ry) + ' A' + w / 2 + ',' + ry + ' 0 0 1 ' + x + ',' + (y + h - ry) + ' Z' }); vrEl('path', { d: 'M' + x + ',' + (y + ry) + ' A' + w / 2 + ',' + ry + ' 0 0 0 ' + (x + w) + ',' + (y + ry), fill: 'none', stroke: at.stroke, 'stroke-width': at['stroke-width'], 'stroke-dasharray': at['stroke-dasharray'] }, p); return e; }
  const e = mk('rect', { x: x, y: y, width: w, height: h, rx: sh === 'terminal' ? h / 2 : sh === 'round' ? 16 : 6 });
  if (sh === 'subroutine') [x + 8, x + w - 8].forEach(lx => vrEl('line', { x1: lx, y1: y, x2: lx, y2: y + h, stroke: at.stroke, 'stroke-width': 1.2 }, p));
  return e;
}
/* Where the status mark sits: on the outline near the top right corner of the shape. */
function vrMarkAt(sh, b) { const x = b.x, y = b.y, w = b.w, h = b.h; if (sh === 'diamond') return [x + w * 0.75, y + h * 0.25]; if (sh === 'circle') return [x + w / 2 + w * 0.354, y + h / 2 - h * 0.354]; if (sh === 'terminal') return [x + w - h * 0.15, y + h * 0.15]; if (sh === 'junction') return [x + w + 2, y - 2]; return [x + w - 12, y + 12]; }
const VR_MM = { rect: ['[', ']'], round: ['(', ')'], terminal: ['([', '])'], diamond: ['{', '}'], io: ['[/', '/]'], junction: ['((', '))'], circle: ['((', '))'], cylinder: ['[(', ')]'], subroutine: ['[[', ']]'], asymmetric: ['>', ']'] };
const VR_SHAPE_KEY = [['rect', '▭', 'step'], ['diamond', '◇', 'decision'], ['terminal', '⬭', 'start / end'], ['io', '▱', 'input / output'], ['cylinder', '⛁', 'store'], ['junction', '●', 'junction']];
/* Sequence view: the arrow of one message from its sender to its receiver, styled by kind (call solid + filled head, reply dashed + open head, async solid + half head), coloured by status. Markers are per tone because a marker cannot take the line colour everywhere. */
function vrSeqTone(s) { return s === 'added' ? 'ok' : s === 'changed' ? 'warn' : s === 'removed' ? 'bad' : 'mut'; }
function vrSeqMarkers(defs) {
  ['ok', 'warn', 'bad', 'mut'].forEach(t => { [['f', 'M0 0L10 5L0 10z', 'var(--' + t + ')'], ['o', 'M0 0L10 5L0 10', 'none'], ['h', 'M0 0L10 5', 'none']].forEach(k => { const m = vrEl('marker', { id: 'vr-sq-' + k[0] + '-' + t, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: 'userSpaceOnUse', orient: 'auto' }, defs); vrEl('path', { d: k[1], fill: k[2], stroke: 'var(--' + t + ')', 'stroke-width': 1.4, 'stroke-linejoin': 'round' }, m); }); });
}
function vrSeqArrow(gg, a, status) {
  const t = vrSeqTone(status), head = a.kind === 'call' ? 'f' : a.kind === 'reply' ? 'o' : 'h', ps = a.points.map(p => p.join(',')).join(' ');
  vrEl('polyline', { points: ps, fill: 'none', stroke: 'transparent', 'stroke-width': 12 }, gg);
  vrEl('polyline', { class: 'vrline vrseq', 'data-kind': a.kind, points: ps, fill: 'none', stroke: 'var(--' + t + ')', 'stroke-width': status === 'context' ? 1.4 : 2.2, 'stroke-dasharray': a.kind === 'reply' ? '6 4' : '0', 'marker-end': 'url(#vr-sq-' + head + '-' + t + ')' }, gg);
}
function vrBuild(host, key, data) {
  const st = VRS[key] = VRS[key] || {}; const have = Object.keys(data.views).filter(k => data.views[k]); /* L0, L1, L2, then one arch:<stem> per new architecture diagram (Architecture impact) */
  if (!st.view || !data.views[st.view]) st.view = (!data.mapPath && data.views.L1) ? 'L1' : have[0]; /* the numbered Flow (L1) is the view a change diagram opens on */ st.mode = st.mode || 'diff';
  if (st.rev !== data.rev) { st.rev = data.rev; st.col = {}; st.lc = {}; /* a committed diagram opens with the groups the server named folded (the areas of the product map, the big groups of a large view) */ have.forEach(k => { if (!data.mapPath && data.views[k].view !== 'seq') return; const c = data.views[k].collapsed; if (c && c.length) st.col[k] = c.slice().sort(); }); }
  const bar = vrDom('div', 'vrbar', null, host); vrDom('h2', 'vrh', (data.mapPath && data.views[have[0]] && data.views[have[0]].title) || 'Change diagram', bar);
  const tabs = vrDom('span', 'vrgrp', null, bar), modes = vrDom('span', 'vrgrp', null, bar);
  const tb = {}, mb = {}; modes.hidden = !!data.mapPath;
  /* The Intent tab (before L0): the run's short summary of what the change sets out to accomplish (intent.md), as context for the review. */
  const intent = !data.mapPath && data.intent ? data.intent : null; let inB = null;
  if (intent) { inB = vrDom('button', 'btn sm', 'Intent', tabs); inB.type = 'button'; inB.title = 'What this change sets out to accomplish'; inB.onclick = () => setIntent(true); }
  /* The Sequence tabs (auto-drafted previews from the L1 flows, next to Decisions): context, no verdicts, no part of the approval. */ const prevKeys = have.filter(k => k.indexOf('seqpreview:') === 0);
  /* Sequence diagrams are on demand: their tabs stay hidden (the numbered Flow is the default) until the Sequence diagrams button opens them. */
  const isSeqTab = k => !data.mapPath && (k.indexOf('seqpreview:') === 0 || (k.indexOf('arch:') === 0 && !!data.views[k] && data.views[k].view === 'seq')); let sqB = null;
  function syncSeq() { const on = !!st.seqOn || isSeqTab(st.view); Object.keys(tb).forEach(k => { if (isSeqTab(k)) tb[k].hidden = !on; }); if (sqB) { sqB.setAttribute('aria-pressed', String(on)); sqB.textContent = on ? 'Hide sequences' : 'Sequence diagrams'; } }
  ['L0', 'L1', 'decision'].filter(k => k !== 'decision' || data.views[k]).concat(prevKeys, ['L2'], have.filter(k => k.indexOf('arch:') === 0)).forEach(k => { const isArch = k.indexOf('arch:') === 0, isPrev = k.indexOf('seqpreview:') === 0; const b = vrDom('button', 'btn sm', isPrev ? 'Sequence' + (prevKeys.length > 1 ? ' ' + (prevKeys.indexOf(k) + 1) : '') + ' (auto draft)' : isArch ? k.slice(5) : data.mapPath ? (data.views[k] && data.views[k].tab) || VR_TABS[k] : k === 'decision' ? VR_TABS[k] : k + ' ' + VR_TABS[k], tabs); if (k === 'decision') b.title = 'What happens at each condition of the change (start, steps, decisions, ends)'; if (isPrev) b.title = (data.views[k].title || k.slice(11)) + ': a sequence drawn mechanically from the Flow view. A preview: no verdicts, not part of the approval, never promoted.'; if (isArch) b.title = 'New architecture diagram ' + k.slice(5) + ' (Architecture impact)'; b.type = 'button'; b.disabled = !data.views[k]; b.hidden = (!!data.mapPath && !data.views[k]) || isSeqTab(k); tb[k] = b; b.onclick = () => { setIntent(false); st.view = k; wkOff(); draw(); }; });
  const seqTabs = have.filter(isSeqTab);
  if (seqTabs.length) { sqB = vrDom('button', 'btn sm', 'Sequence diagrams', tabs); sqB.type = 'button'; sqB.title = 'Show the sequence diagrams of this change (drawn on demand; the numbered Flow is the default view)'; sqB.onclick = () => { setIntent(false); if (isSeqTab(st.view)) { st.seqOn = false; st.view = data.views.L1 ? 'L1' : have.find(k => !isSeqTab(k)) || st.view; } else { st.seqOn = true; st.view = seqTabs[0]; } wkOff(); draw(); }; }
  syncSeq();
  /* Every part of the review is listed here, its content (an Open button for the canvas tab) or its stated "none - reason" card; nothing sits behind a driver. The preview view adds a comment box per part (kept beside the preview, never in review.json). */
  if (data.parts && !data.mapPath) {
    const nCont = data.parts.filter(x => x.kind === 'content').length;
    const pbox = vrDom('details', 'vrparts', null, host); pbox.open = st.partsOpen !== false; pbox.addEventListener('toggle', () => { st.partsOpen = pbox.open; });
    vrDom('summary', '', 'Diagram parts: ' + nCont + ' drawn, ' + (data.parts.length - nCont) + ' stated as none' + (data.previewAll ? ' (preview, not an approval)' : ''), pbox);
    data.parts.forEach(x => {
      const c = vrDom('div', 'vrpart ' + x.kind, null, pbox); vrDom('strong', '', x.label, c); vrDom('span', 'small mut', x.kind === 'content' ? ' drawn' : ' none', c);
      if (x.reason) vrDom('p', 'small', x.reason, c);
      if (x.views && x.views.length) { const o = vrDom('button', 'btn sm', 'Open', c); o.type = 'button'; o.onclick = () => { const v0 = x.views.find(v => data.views[v]); if (!v0) return; if (v0.indexOf('seqpreview:') === 0 && !st.seqOn && sqB) { sqB.click(); } const t = tb[v0]; if (t) t.click(); }; }
      if (data.previewAll) {
        const ta = vrDom('textarea', 'vrpcm', null, c); ta.rows = 2; ta.placeholder = 'Is this part useful? Your comment'; ta.value = ((data.comments || {})[x.key] || {}).comment || ''; ta.setAttribute('aria-label', 'Comment on ' + x.label);
        const sv = vrDom('button', 'btn sm', 'Save comment', c); sv.type = 'button'; const mg = vrDom('span', 'small mut', '', c);
        sv.onclick = async () => { sv.disabled = true; mg.textContent = 'Saving...'; try { const r = await fetch('/api/visual-review/preview-comment', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ dossier: data.dossier, repo: key.slice(0, key.indexOf('/')), part: x.key, comment: ta.value }) }); const j = await r.json(); mg.textContent = r.ok ? 'Saved' : (j.error || ('HTTP ' + r.status)); if (r.ok) data.comments = j.comments; } catch (e) { mg.textContent = 'Not saved'; } sv.disabled = false; };
      }
    });
    if (data.hasPreviewAll || data.previewAll) {
      const pv = vrDom('button', 'btn sm', data.previewAll ? 'Back to the review' : 'All diagrams (preview)', pbox); pv.type = 'button'; pv.title = data.previewAll ? 'Leave the preview' : 'Every diagram part generated after the fact, with a comment box each; never an approval';
      pv.onclick = async () => { const k0 = key.split('~')[0]; const sl = k0.indexOf('/'); VRPV[k0] = !data.previewAll; if (VRPV[k0]) await vrLoad(k0.slice(0, sl), k0.slice(sl + 1), 'preview-all'); render(); };
    }
  }
  VR_MODES.forEach(m => { const b = vrDom('button', 'btn sm', m[1], modes); b.type = 'button'; mb[m[0]] = b; b.onclick = () => { st.mode = m[0]; applyMode(); }; });
  const fitB = vrDom('button', 'btn sm', 'Fit all', bar); fitB.title = 'Show the whole diagram (labels may get small)'; fitB.type = 'button';
  /* A sequence diagram over the budget is folded into refs: Expand all opens every one, Fold all closes them again (nothing is ever cropped). */
  const exB = vrDom('button', 'btn sm', 'Expand all', bar); exB.type = 'button'; exB.hidden = true; exB.title = 'Open every folded group of messages (ref) of this sequence';
  const seqGroups = () => { const b = data.views[st.view]; return b && b.graph && b.graph.view === 'seq' ? (b.graph.groups || []) : []; };
  exB.onclick = () => { if (busy) return; st.prevCol = colOf(st.view).slice(); st.col[st.view] = colOf(st.view).length ? [] : (data.views[st.view].collapsed || []).slice().sort(); draw(); };
  const moreD = vrDom('details', 'vrmore', null, bar); vrDom('summary', '', 'More ▾', moreD); const menu = vrDom('div', 'vrmenu', null, moreD); /* the row keeps the view tabs, the Before/After/Diff modes, Fit all and Walkthrough; zoom, Mermaid and Full screen sit in this menu */
  const zin = vrDom('button', 'btn sm', 'Zoom in (+)', menu), zout = vrDom('button', 'btn sm', 'Zoom out (−)', menu); zin.type = zout.type = 'button'; zin.title = 'Zoom in'; zout.title = 'Zoom out';
  const wkB = vrDom('button', 'btn sm', 'Walkthrough', bar); wkB.type = 'button'; wkB.title = 'Step through the added, changed and removed elements in reading order'; wkB.setAttribute('aria-pressed', 'false'); wkB.hidden = !!data.mapPath; /* a committed diagram has no added, changed or removed elements to step through */
  const mmB = vrDom('button', 'btn sm', 'Mermaid', menu); mmB.type = 'button'; mmB.title = 'Export this view as mermaid flowchart text';
  /* Full screen: the canvas takes the whole window (a body class, since the morph owns the host's own attributes); Esc or the button closes it. */
  const fsB = vrDom('button', 'btn sm', '', menu); fsB.type = 'button'; fsB.title = 'Give the canvas the whole window (f; Esc closes)';
  bar.appendChild(moreD); [mmB, fsB].forEach(b => b.addEventListener('click', () => { moreD.open = false; }));
  const setFs = (on) => { st.fs = on; document.body.classList.toggle('vrfs-on', on); fsB.textContent = on ? '✕ Close full screen' : '⤢ Full screen'; fsB.setAttribute('aria-pressed', String(on)); requestAnimationFrame(() => fit()); };
  fsB.onclick = () => setFs(!st.fs); VR_FS.off = () => setFs(false); VR_FS.toggle = () => setFs(!st.fs); VR_FS.fit = () => fit();
  const leg = vrDom('div', 'vrleg small mut', null, host); leg.hidden = !!data.mapPath; /* the change legend: a committed diagram shows no change marks */
  ['added', 'changed', 'removed', 'context'].forEach(s => { const i = vrDom('span', 'vrlg', null, leg); const sw = vrDom('i', 'vrsw ' + s, VR_MARK[s], i); sw.setAttribute('aria-hidden', 'true'); i.appendChild(document.createTextNode(' ' + s)); });
  vrDom('span', 'vrlg', '- - - dashed edge = inferred', leg);
  const shLeg = vrDom('span', 'vrlg', null, leg); shLeg.hidden = true; shLeg.setAttribute('title', 'Shape = type of node; colour = change status'); VR_SHAPE_KEY.forEach(k => { const i = vrDom('span', 'vrshk', null, shLeg); const g0 = vrDom('b', null, k[1], i); g0.setAttribute('aria-hidden', 'true'); i.appendChild(document.createTextNode(' ' + k[2])); });
  const dcLeg = vrDom('span', 'vrlg vrdc', null, leg); dcLeg.hidden = true; dcLeg.setAttribute('title', 'Decisions and start-to-end paths before and after the change');
  const wbar = vrDom('div', 'vrwalk', null, host); wbar.hidden = true; wbar.setAttribute('role', 'group'); wbar.setAttribute('aria-label', 'Walkthrough');
  const wkPrev = vrDom('button', 'btn sm', '◀ Previous', wbar), wkInd = vrDom('strong', 'vrwi', '', wbar), wkNext = vrDom('button', 'btn sm', 'Next ▶', wbar), wkExit = vrDom('button', 'btn sm', 'Exit', wbar), wkLab = vrDom('span', 'vrwl', '', wbar); vrDom('span', 'small mut', 'Arrow keys or n / p to move, Esc to leave', wbar);
  wkPrev.type = wkNext.type = wkExit.type = 'button'; wkInd.setAttribute('aria-live', 'polite');
  const inBox = vrDom('div', 'vrintent', null, host); inBox.hidden = true;
  if (intent) { if (intent.title) vrDom('h3', '', intent.title, inBox); let ul = null;
    String(intent.text).split('\n').forEach(l => { const t = l.trim(); if (!t) { ul = null; return; } const m = /^([-*]|\d+\.)\s+(.*)$/.exec(t);
      if (m) { ul = ul || vrDom('ul', '', null, inBox); vrDom('li', '', m[2], ul); } else { ul = null; vrDom(/^#+\s/.test(t) ? 'h4' : 'p', '', t.replace(/^#+\s*/, ''), inBox); } });
    vrDom('p', 'small mut', 'Written by the run with the diagrams (' + intent.file + '). The L0, L1 and L2 tabs show the change itself.', inBox); }
  const body = vrDom('div', 'vrbody', null, host), svg = vrEl('svg', { class: 'vrsvg', role: 'img', 'aria-label': 'Change diagram' }); const cv = vrDom('div', 'vrcv', null, body); cv.tabIndex = -1; cv.appendChild(svg);
  /* The hover card: the full text of whatever element the pointer is on (box, group, edge), at every zoom, since box text is cut and gets small. */
  const hovIt = new WeakMap(), hc = vrDom('div', 'vrhc', null, cv); hc.hidden = true; hc.setAttribute('role', 'tooltip'); let hovOn = null;
  const hovHide = () => { hc.hidden = true; hovOn = null; };
  const hovShow = (g0, e) => { const it = hovIt.get(g0); if (!it) return hovHide();
    if (hovOn !== g0) { hovOn = g0; hc.replaceChildren(); vrDom('b', '', it.label || (it.source + ' → ' + it.target), hc);
      vrDom('div', 'mut', [it.kind, it.status, it.resolution === 'inferred' ? 'inferred' : ''].filter(Boolean).join(' · ') + (it.count > 1 && !it.collapsed ? ' · ' + it.count + ' links' : ''), hc);
      if (it.collapsed && it.members) vrDom('p', 'mut', it.members + ' inside; click to open', hc);
      if (it.stereotype) vrDom('div', 'mut', '«' + it.stereotype + '»', hc);
      if (Array.isArray(it.members) && it.members.length) { const ul = vrDom('ul', 'vrhcm', null, hc); it.members.slice(0, 24).forEach(m => vrDom('li', '', typeof m === 'string' ? m : (m.text || m.name || JSON.stringify(m)), ul)); if (it.members.length > 24) vrDom('li', 'mut', '+' + (it.members.length - 24) + ' more (inspector)', ul); }
      if (it.detail && it.detail.before) vrDom('p', '', 'Before: ' + it.detail.before, hc); if (it.detail && it.detail.after) vrDom('p', '', 'After: ' + it.detail.after, hc);
      const ev = it.evidence || []; if (ev.length) vrDom('p', 'mut', vrEv(ev[0], vrHand(cur.graph)) + (ev.length > 1 ? ' (+' + (ev.length - 1) + ' more)' : ''), hc); }
    hc.hidden = false; const r = cv.getBoundingClientRect(), w = hc.offsetWidth, h = hc.offsetHeight; let x = e.clientX - r.left + 14, y = e.clientY - r.top + 16;
    if (x + w > r.width - 4) x = Math.max(4, e.clientX - r.left - w - 10); if (y + h > r.height - 4) y = Math.max(4, e.clientY - r.top - h - 10);
    hc.style.left = x + 'px'; hc.style.top = y + 'px'; };
  svg.addEventListener('pointermove', e => { if (e.buttons) return hovHide(); const g0 = e.target.closest && e.target.closest('[data-id]'); g0 ? hovShow(g0, e) : hovHide(); });
  svg.addEventListener('pointerleave', hovHide); svg.addEventListener('pointerdown', hovHide); svg.addEventListener('wheel', hovHide, { passive: true });
  const insp = vrDom('aside', 'vrinsp', null, body);
  const note = vrDom('div', 'small mut vrnote', null, host);
  const slash = key.indexOf('/'), vrRepo = key.slice(0, slash), vrDossier = data.dossier || '';
  const reviewing = !!data.review;
  const ban = vrDom('div', 'vrban', null, host); ban.hidden = true;
  const pvB = vrDom('div', 'vrpv', null, host); pvB.hidden = true;
  const rbar = vrDom('div', 'vrrev', null, host); rbar.hidden = !reviewing;
  const sumEl = vrDom('span', 'vrsum', '', rbar), sendB = vrDom('button', 'btn primary sm', 'Send review', rbar), apB = vrDom('button', 'btn sm', 'Approve all', rbar), sendNote = vrDom('span', 'small mut', '', rbar); sendB.type = 'button'; apB.type = 'button'; apB.title = 'Mark every element of every diagram OK and answer Approved, in one go.';
  const prev = vrDom('div', 'vrprev', null, host); prev.hidden = true;
  const gone = vrDom('div', 'small mut', null, host);
  const mm = vrDom('div', 'vrmm', null, host); mm.hidden = true;
  const mmText = vrDom('textarea', 'vrta', null, mm); mmText.readOnly = true; mmText.rows = 8; mmText.setAttribute('aria-label', 'Mermaid text');
  const mmCopy = vrDom('button', 'btn sm', 'Copy', mm); mmCopy.type = 'button';
  let root = null, vp = { x: 0, y: 0, k: 1 }, els = {}, cur = null, drag = null, busy = false, wk = { on: false, list: [], i: 0 }, ovSvg = null, ovVp = null, ovRects = {};
  const ov = vrDom('div', 'vrov', null, cv); ov.hidden = true; ov.title = 'Overview: click or drag to move the view';
  const items = () => { const g = cur.graph, m = {}; (g.groups || []).forEach(x => m[x.id] = x); (g.lanes || []).forEach(x => m[x.id] = { id: x.id, label: x.label, status: 'context', evidence: [], kind: 'lane' }); g.nodes.forEach(x => m[x.id] = x); g.edges.forEach(x => m[x.id] = x); return m; };
  const apply = () => { if (root) root.setAttribute('transform', 'translate(' + vp.x + ' ' + vp.y + ') scale(' + vp.k + ')'); mapUpd(); };
  /* The readable scale: the one at which the smallest label text of this drawing renders at 12 px, read from the drawn text (not hard-coded). */
  function readableK() { let f = 12; if (root) root.querySelectorAll('text').forEach(t => { if (t.closest('.vr-mark')) return; const px = parseFloat(getComputedStyle(t).fontSize); if (px > 0 && px < f) f = px; }); return 12 / f; }
  /* Opening view: fit to the canvas box but never below the readable scale; when that overflows, start at the top-left (the start of the flow) and pan inside the box. "Fit all" (st.fitAll) shows the whole diagram. */
  function fit() { if (!cur || !cur.layout) return; const r = svg.getBoundingClientRect(), W = cur.layout.width, H = cur.layout.height, bw = r.width || 600, bh = r.height || 400; let k = Math.max(0.1, Math.min(bw / (W + 40), bh / (H + 40), 1.6)); if (!st.fitAll) k = Math.max(k, readableK()); const ox = W * k + 40 > bw, oy = H * k + 40 > bh; vp = { k: k, x: ox ? 20 : (bw - W * k) / 2, y: oy || data.mapPath ? 20 : (bh - H * k) / 2 }; apply(); }
  function zoomAt(f, mx, my) { const k = Math.max(0.15, Math.min(4, vp.k * f)); vp.x = mx - (mx - vp.x) * (k / vp.k); vp.y = my - (my - vp.y) * (k / vp.k); vp.k = k; apply(); }
  function inspect(id) {
    /* A repaint while the owner types: note the focus and cursor of the comment box first, so verdictBox puts them back. */
    const ae = document.activeElement, adk = ae && insp.contains(ae) && ae.getAttribute && ae.getAttribute('data-vd-dk');
    Object.keys(st.drafts || {}).forEach(k => { st.drafts[k].focus = false; });
    if (adk) { const dd = (st.drafts = st.drafts || {})[adk] = st.drafts[adk] || { comment: ae.value }; dd.comment = ae.value; dd.focus = true; dd.s0 = ae.selectionStart; dd.s1 = ae.selectionEnd; }
    const it = items()[id]; insp.replaceChildren(); if (!it) return;
    vrDom('h3', '', it.label || (it.source + ' → ' + it.target), insp);
    const meta = vrDom('p', 'small', null, insp); const b = vrDom('span', 'vrbadge ' + it.status, VR_MARK[it.status] + ' ' + it.status, meta);
    meta.appendChild(document.createTextNode(' ' + (it.kind || '') + (it.resolution ? ' · ' + it.resolution : '') + (it.label_source ? ' · label by ' + it.label_source : '')));
    if (it.members && it.members.length) { vrDom('p', 'small mut', 'Members', insp); const pre = vrDom('pre', 'small', null, insp); pre.textContent = it.members.join('\n'); }
    if (it.detail) { if (it.detail.before) vrDom('p', '', 'Before: ' + it.detail.before, insp); if (it.detail.after) vrDom('p', '', 'After: ' + it.detail.after, insp); }
    const ev = it.evidence || [];
    const tiers = ev.map(x => x.tier).filter((t, i, a) => a.indexOf(t) === i).join(' + ');
    vrDom('p', 'small mut', 'Evidence' + (tiers ? ' (' + tiers + ')' : '') + (ev.length ? '' : ': none'), insp);
    ev.forEach(x => vrDom('code', 'vrev', vrEv(x, vrHand(cur.graph)), vrDom('p', 'small', null, insp)));
    Object.keys(els).forEach(k => els[k].g.classList.toggle('sel', k === id));
    st.sel = id; if (reviewing && st.view.indexOf('seqpreview:') !== 0 && cur.graph.nodes.some(n => n.id === id && !n.collapsed)) verdictBox(id);
  }
  /* VR-04: the verdict of the owner on one element. Saved to review.json by the server; a Question with a comment also goes to the agent. */
  const VD = [['ok', 'OK'], ['question', 'Question'], ['change', 'Change'], ['remove', 'Remove from scope']];
  const VD_CODE = { ok: 'OK', question: '?', change: 'Δ', remove: '✕' };
  const lineFor = (view, id) => ((st.review && st.review.lines) || []).find(l => l.anchor.diagram === view && l.anchor.element === id);
  function verdictBox(id) {
    const old = lineFor(st.view, id), box = vrDom('div', 'vrvd', null, insp); box.setAttribute('data-vd', id);
    /* The unsaved draft (pick, comment, blocking) of this element: the periodic reload repaints the inspector from review.json, so a comment being typed is kept here and put back, cursor included, until it is saved or cleared. */
    const dk = st.view + '|' + id, drafts = st.drafts = st.drafts || {}, dr = drafts[dk];
    const hadFocus = !!(dr && dr.focus);
    let pick = dr && dr.pick !== undefined ? dr.pick : (old ? old.verdict : '');
    const keep = () => { drafts[dk] = { pick: pick, comment: ta.value, blocking: bc.checked, s0: ta.selectionStart, s1: ta.selectionEnd }; };
    const state = vrDom('p', 'small', old ? (old.state === 'outdated' ? 'Outdated: this verdict was made on an earlier version of the diagram (' + old.verdict + '). Save it again to confirm.' : 'Your verdict: ' + old.verdict) : 'Not yet looked at.', box);
    const grp = vrDom('div', 'vrgrp', null, box), btns = {};
    VD.forEach(v => { const b = vrDom('button', 'btn sm', v[1], grp); b.type = 'button'; b.setAttribute('data-vd-pick', v[0]); btns[v[0]] = b; b.setAttribute('aria-pressed', String(pick === v[0])); b.onclick = () => { pick = v[0]; Object.keys(btns).forEach(k => btns[k].setAttribute('aria-pressed', String(k === pick))); save.disabled = false; keep(); }; });
    const ta = vrDom('textarea', '', null, box); ta.maxLength = 1000; ta.value = dr ? dr.comment : (old ? old.comment : ''); ta.placeholder = 'Optional comment'; ta.setAttribute('aria-label', 'Comment on this element');
    const fbA = 'el:' + st.view + ':' + id, fbItem = vrRepo + '/' + vrDossier;
    if (FB_ANCHOR.test(fbA)) {
      const vl = vrDom('label', 'fbview', null, box), vc = vrDom('input', '', null, vl); vc.type = 'checkbox'; vc.checked = fbIsViewed(fbSavedOf(), fbState(fbItem), fbA); vl.appendChild(document.createTextNode(' viewed'));
      vc.onchange = () => { fbSetViewed(fbState(fbItem), fbA, vc.checked); fbKeep(fbItem); };
    }
    const bl = vrDom('label', 'fbblk', null, box), bc = vrDom('input', '', null, bl); bc.type = 'checkbox'; bc.checked = dr ? !!dr.blocking : !!(old && old.blocking); bl.appendChild(document.createTextNode(' blocking')); bl.title = 'A blocking Question or Change holds the approval back; informing only tells the agent';
    const row = vrDom('div', 'vrgrp', null, box), save = vrDom('button', 'btn primary sm', 'Save verdict', row), clr = vrDom('button', 'btn sm', 'Clear', row), msg = vrDom('span', 'small mut', '', row);
    save.type = clr.type = 'button'; save.disabled = !pick; clr.disabled = !old;
    ta.addEventListener('input', keep); ta.addEventListener('select', keep); ta.addEventListener('keyup', keep); bc.addEventListener('change', keep); ta.setAttribute('data-vd-dk', dk);
    if (hadFocus) { ta.focus(); try { ta.setSelectionRange(dr.s0, dr.s1); } catch (e) { /* no selection */ } }
    const go = async (verdict) => {
      save.disabled = clr.disabled = true; msg.textContent = 'Saving…';
      try {
        const r = await fetch('/api/visual-review/review', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ dossier: vrDossier, repo: vrRepo, diagram: st.view, element: id, verdict: verdict, comment: ta.value, blocking: bc.checked, graph_hash: (data.shownHashes || {})[st.view] }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) { msg.textContent = j.error || ('HTTP ' + r.status); save.disabled = !pick; clr.disabled = !old; if (r.status === 409) vrLoad(vrRepo, vrDossier); return; }
        delete drafts[dk]; st.review = j.review; paintAll(); if (j.followup) load(['questions']);
      } catch (e) { msg.textContent = 'request failed'; save.disabled = !pick; clr.disabled = !old; }
    };
    save.onclick = () => go(pick); clr.onclick = () => go('unset');
  }
  function paintBadges() {
    if (!cur || !cur.layout) return; svg.querySelectorAll('.vr-vd').forEach(e => e.remove());
    cur.graph.nodes.forEach(n => { const l = lineFor(st.view, n.id), e = els[n.id], b = cur.layout.boxes[n.id]; if (!l || !e || !b) return;
      const bd = cur.layout.badges && cur.layout.badges[n.id]; /* a sequence message has its own badge slot after the label, never on the text */ const t = vrEl('text', { class: 'vr-vd vrcode', x: bd ? bd.x : b.x + 10, y: bd ? bd.y + 11 : b.y + 4, stroke: 'var(--bg)', 'stroke-width': 6, 'paint-order': 'stroke', fill: 'var(--' + (l.state === 'current' ? ({ ok: 'ok', question: 'que', change: 'warn', remove: 'bad' })[l.verdict] : 'mut') + ')' }, e.g);
      t.textContent = VD_CODE[l.verdict] + (l.state === 'outdated' ? ' (old)' : ''); });
  }
  /* The summary, the Send button and the stale banner: cheap, runs on every page render. */
  function paintBar() {
    const m = VRM[vrRepo] && VRM[vrRepo].status;
    if (m && m.hasMap && m.stale) { ban.hidden = false; ban.textContent = 'The committed product map is stale: ' + m.stale + ' element' + (m.stale === 1 ? '' : 's') + ' (' + m.ids.join(', ') + ').'; } else { ban.hidden = true; ban.textContent = ''; }
    if (!reviewing || !st.review) return;
    const s = st.review.summary; sumEl.textContent = s.text;
    const q = vrOpenQ(vrDossier, vrRepo);
    sendB.disabled = !q || !s.checked;
    const flagged = s.question + s.change + s.remove;
    apB.disabled = !q || flagged > 0; apB.title = !q ? 'No open visual-review question to answer.' : flagged ? 'Some elements are marked Question, Change or Remove: clear them first, or send the review.' : 'Mark every element of every diagram OK and answer Approved, in one go.';
    sendNote.textContent = !q ? 'No open visual-review question for this dossier; Send review is enabled when the run asks for the visual review.' : !s.checked ? 'Check at least one element first.' : '';
    const g = st.review.lines.filter(l => l.state === 'gone');
    gone.textContent = g.length ? 'Element gone from the diagram (the comment is kept in review.json): ' + g.map(l => l.anchor.diagram + ' ' + l.anchor.element + (l.comment ? ' — ' + l.comment : '')).join('; ') : '';
  }
  function paintAll() { paintBar(); paintBadges(); if (st.sel && cur && cur.graph && cur.graph.nodes.some(n => n.id === st.sel)) inspect(st.sel); }
  st.paint = paintAll; st.paintBar = paintBar;
  sendB.onclick = async () => {
    prev.hidden = false; prev.replaceChildren(); vrDom('p', 'small mut', 'Preview of the answer to the question:', prev);
    let a = null; try { const r = await fetch('/api/visual-review/answer?dossier=' + enc(vrDossier) + '&repo=' + enc(vrRepo)); a = r.ok ? await r.json() : null; } catch (e) { a = null; }
    if (!a || !a.canSend) { vrDom('p', 'small', a ? 'There is no open question to answer.' : 'Could not build the answer.', prev); return; }
    vrDom('p', 'small', 'This sends option ' + a.option + ' (' + (a.choice === 'A' ? 'Approved' : 'Changes requested') + ') and closes the question. The first answer is final.', prev);
    const ta = vrDom('textarea', '', null, prev); ta.readOnly = true; ta.value = a.text; ta.setAttribute('aria-label', 'Answer text');
    const row = vrDom('div', 'vrgrp', null, prev), ok = vrDom('button', 'btn primary sm', 'Confirm and send', row), no = vrDom('button', 'btn sm', 'Cancel', row), msg = vrDom('span', 'small mut', '', row); ok.type = no.type = 'button';
    no.onclick = () => { prev.hidden = true; };
    ok.onclick = async () => { ok.disabled = true; msg.textContent = 'Sending…'; await post(a.question, { kind: 'answer', option: a.option, text: a.text, review: true }); if (S.qerr[a.question]) { msg.textContent = S.qerr[a.question]; ok.disabled = false; } else { prev.hidden = true; } };
  };
  apB.onclick = () => {
    const q = vrOpenQ(vrDossier, vrRepo); if (!q || !st.review) return;
    prev.hidden = false; prev.replaceChildren();
    const s = st.review.summary;
    vrDom('p', 'small', 'Mark all ' + s.total + ' elements of every diagram OK' + (s.unchecked ? ' (' + s.unchecked + ' not looked at one by one)' : '') + ' and answer ' + q.id + ' Approved. This closes the question; the first answer is final.', prev);
    const row = vrDom('div', 'vrgrp', null, prev), ok = vrDom('button', 'btn primary sm', 'Approve all and send', row), no = vrDom('button', 'btn sm', 'Cancel', row), msg = vrDom('span', 'small mut', '', row); ok.type = no.type = 'button';
    no.onclick = () => { prev.hidden = true; };
    ok.onclick = async () => {
      ok.disabled = no.disabled = true; msg.textContent = 'Approving…';
      try {
        const r = await fetch('/api/visual-review/approve-all', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ dossier: vrDossier, repo: vrRepo, hashes: data.shownHashes || {} }) });
        const j = await r.json().catch(() => ({}));
        if (j.review) { st.review = j.review; paintAll(); }
        if (!r.ok) { msg.textContent = j.error || ('HTTP ' + r.status); ok.disabled = no.disabled = false; if (r.status === 409 && /changed/.test(j.error || '')) vrLoad(vrRepo, vrDossier); return; }
        prev.hidden = true; load(['questions']);
      } catch (e) { msg.textContent = 'request failed'; ok.disabled = no.disabled = false; }
    };
  };
  function visible(status) { return VR_PASS[st.mode](status); }
  function applyMode() {
    Object.keys(mb).forEach(k => mb[k].setAttribute('aria-pressed', String(k === st.mode)));
    const g = cur.graph, ok = {}; g.nodes.forEach(n => ok[n.id] = visible(n.status));
    const up = {}; g.nodes.forEach(n => { const p = (vrLane(g.view) && (g.lanes || []).length ? n.lane : n.group); if (p) up[n.id] = p; }); (g.groups || []).forEach(x => { if (x.parent) up[x.id] = x.parent; });
    (g.groups || []).forEach(x => ok[x.id] = visible(x.status)); (g.lanes || []).forEach(x => ok[x.id] = true);
    g.nodes.forEach(n => { if (!ok[n.id]) return; let p = up[n.id], n2 = 0; while (p && n2++ < 50) { ok[p] = true; p = up[p]; } });
    Object.keys(els).forEach(id => { const e = els[id]; let on; if (e.edge) on = visible(e.status) && ok[e.edge[0]] !== false && ok[e.edge[1]] !== false; else on = ok[id] !== false; e.g.classList.toggle('vroff', !on); if (e.lab) e.lab.classList.toggle('vroff', !on); });
    note.textContent = vrNote(cur, st.mode);
    Object.keys(ovRects).forEach(id => ovRects[id].classList.toggle('vroff', !!els[id] && els[id].g.classList.contains('vroff')));
    if (wk.on) wkSync();
    if (!mm.hidden) showMm();
  }
  function vrNote(v, mode) { const c = { added: 0, changed: 0, removed: 0 }; v.graph.nodes.forEach(n => { if (c[n.status] !== undefined && visible(n.status)) c[n.status]++; }); const is = v.issues.length ? ' · ' + v.issues.length + ' validation issue' + (v.issues.length > 1 ? 's' : '') + ': ' + v.issues.slice(0, 3).map(i => i.message).join('; ') : ''; const ti = v.graph.title ? v.graph.title + ' · ' : ''; if (data.mapPath) return ti.replace(/ · $/, '') + (is ? is : ''); return ti + c.added + ' added, ' + c.changed + ' changed, ' + c.removed + ' removed shown' + is; }
  const colOf = v => st.col[v] || [];
  const canCol = id => !(vrLane(data.views[st.view].graph.view) && data.views[st.view].graph.view !== 'seq' && (data.views[st.view].graph.lanes || []).length) && (data.views[st.view].graph.groups || []).some(x => x.id === id);
  /* A collapse state is laid out by the server (the collapsed graph, edges aggregated); the answer is kept per state, so closing again is instant. */
  function draw(anchor, selId) {
    const base = data.views[st.view], ids = colOf(st.view);
    if (!ids.length || !base.graph || (!base.layout && !data.mapPath)) { paint(base, anchor, selId); return; }
    const ck = st.view + '|' + ids.join(','), hit = st.lc[ck];
    if (hit) { paint(Object.assign({}, base, hit), anchor, selId); return; }
    busy = true; note.textContent = 'Laying out the collapsed groups…';
    fetch('/api/visual-review/layout?' + (data.mapPath ? 'repo=' + enc(vrRepo) + '&path=' + enc(data.mapPath) : 'dossier=' + enc(vrDossier) + '&repo=' + enc(vrRepo) + '&view=' + enc(st.view)) + '&collapsed=' + enc(ids.join(',')))
      .then(r => r.json().then(j => { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }))
      .then(j => { busy = false; st.lc[ck] = { graph: j.graph, layout: j.layout }; if (st.view + '|' + colOf(st.view).join(',') === ck) paint(Object.assign({}, base, st.lc[ck]), anchor, selId); })
      .catch(e => { busy = false; st.col[st.view] = st.prevCol || []; note.textContent = 'Could not lay out the collapsed view: ' + e.message; });
  }
  function toggle(id) {
    if (busy || !cur || !cur.layout) return;
    const list = colOf(st.view).slice(), i = list.indexOf(id), b = cur.layout.boxes[id];
    st.prevCol = list.slice(); if (i >= 0) { list.splice(i, 1); /* a sequence ref opens with its sub-refs folded (two levels at most) */ if (cur.graph.view === 'seq') (data.views[st.view].graph.groups || []).forEach(x => { if (x.parent === id && list.indexOf(x.id) < 0) list.push(x.id); }); } else list.push(id); list.sort(); st.col[st.view] = list;
    draw(b ? { id: id, sx: vp.x + b.x * vp.k, sy: vp.y + b.y * vp.k } : null, id);
  }
  function paint(v, anchor, selId) {
    hovHide(); cur = v; Object.keys(tb).forEach(k => tb[k].setAttribute('aria-pressed', String(k === st.view))); exB.hidden = !seqGroups().length; exB.textContent = colOf(st.view).length ? 'Expand all' : 'Fold all'; syncSeq();
    const isPv = st.view.indexOf('seqpreview:') === 0; pvB.hidden = !isPv; pvB.textContent = isPv ? 'Auto-drafted preview, not part of the review. This sequence was built mechanically from the Flow view' + (v.note ? ' (' + v.note + ')' : '') + '. Read it as a picture of the order of the steps: the arrows are not verified calls. There are no verdicts here, it is never promoted into docs/architecture, and it does not block the approval.' : '';
    svg.replaceChildren(); els = {}; root = null; ovSvg = null; ovVp = null; ovRects = {}; ov.replaceChildren(); ov.hidden = true; st.sel = ''; insp.replaceChildren(); vrDom('p', 'small mut', 'Click an element for its description and evidence. Drag to pan; Ctrl or Cmd + wheel to zoom; the wheel scrolls the page; click a group header to fold it; in a folded view, click a folded box to open it.', insp);
    if (!cur.layout || !cur.graph) { note.textContent = cur.issues.map(i => i.message).join('; ') || 'This view could not be laid out.'; return; }
    const g = cur.graph, L = cur.layout, defs = vrEl('defs', {}, svg), mk = vrEl('marker', { id: 'vr-ar', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, markerUnits: 'userSpaceOnUse', orient: 'auto-start-reverse' }, defs);
    vrEl('path', { d: 'M0 0L10 5L0 10z', fill: 'var(--mut)' }, mk); vrSeqMarkers(defs); root = vrEl('g', {}, svg);
    const tip = (g0, it) => { hovIt.set(g0, it); g0.setAttribute('aria-label', (it.label || (it.source + ' to ' + it.target)) + ', ' + it.status); }; /* the hover card shows the rest */
    const cont = [].concat(g.groups || [], (g.lanes || []).map(l => ({ id: l.id, label: l.label, status: 'context', evidence: [] })));
    cont.forEach(c => { const b = L.boxes[c.id]; if (!b) return; const gg = vrEl('g', { 'data-id': c.id, 'data-status': c.status, class: 'vrg', tabindex: 0 }, root); els[c.id] = { g: gg, status: c.status };
      vrEl('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 8, fill: 'var(--bg)', stroke: 'var(--' + vrTone(c.status) + ')', 'stroke-width': c.status === 'context' ? 1 : 2, 'stroke-dasharray': c.status === 'context' ? '4 3' : '0' }, gg);
      const hl = L.arrows && L.texts && L.texts[c.id] ? L.texts[c.id] : null; /* a sequence lane header: its label wrapped to the column, whole */ const tx = vrEl('text', { x: b.x + 10, y: b.y + 19, fill: 'var(--fg)', 'font-weight': 600, 'font-size': 13 }, gg); if (hl) hl.forEach((t, i) => { const ts = vrEl('tspan', { x: b.x + 10, dy: i ? 16 : 0 }, tx); ts.textContent = t; }); else tx.textContent = (canCol(c.id) ? '▾ ' : '') + VR_MARK[c.status] + ' ' + c.label; vrMark(gg, c.status, b.x + b.w - 14, b.y + 14); if (L.arrows) vrEl('line', { class: 'vrlife', x1: b.x + b.w / 2, x2: b.x + b.w / 2, y1: b.y + (L.headH || 34), y2: b.y + b.h - 6, stroke: 'var(--mut)', 'stroke-width': 1, 'stroke-dasharray': '5 4', 'pointer-events': 'none' }, gg); tip(gg, c);
      if (canCol(c.id)) vrEl('rect', { class: 'vrhd', x: b.x, y: b.y, width: b.w, height: 30, fill: 'transparent', stroke: 'transparent', 'data-tog': c.id }, gg); });
    /* seq frames (alt, opt, loop, par ...): a labelled box behind the steps it spans, a dashed rule where a branch (else, and) starts. */
    (g.frames || []).forEach(f => { const b = L.frames && L.frames[f.id]; if (!b) return; const fg = vrEl('g', { class: 'vrfr', 'data-frame': f.id, 'pointer-events': 'none' }, root);
      vrEl('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 4, fill: 'none', stroke: 'var(--mut)', 'stroke-width': 1.2 }, fg);
      const lab = f.kind + (f.label ? ' [' + f.label + ']' : ''); const tw = Math.min(b.w - 8, Math.ceil(lab.length * 6.2) + 10); vrEl('rect', { x: b.x, y: b.y, width: tw, height: 15, fill: 'var(--card)', stroke: 'var(--mut)', 'stroke-width': 1 }, fg);
      const ft = vrEl('text', { x: b.x + 5, y: b.y + 11, 'font-size': 10.5, fill: 'var(--mut)' }, fg); ft.textContent = lab.length * 6.2 + 10 > tw ? lab.slice(0, Math.max(1, Math.floor((tw - 10) / 6.2) - 1)) + '…' : lab;
      (f.branches || []).forEach(br => { const sb = L.boxes[br.first]; if (!sb) return; const y = Math.max(b.y + 16, sb.y - 8); vrEl('line', { x1: b.x, x2: b.x + b.w, y1: y, y2: y, stroke: 'var(--mut)', 'stroke-width': 1, 'stroke-dasharray': '5 4' }, fg); const bt = vrEl('text', { x: b.x + 5, y: y + 11, 'font-size': 10.5, fill: 'var(--mut)' }, fg); bt.textContent = '[' + (br.label || br.kind) + ']'; }); });
    /* seq refs that are open: a box around the messages of the group with its header (label, message count, status colour) and a fold button; the box does not take clicks, only the header does. */
    (g.groups || []).forEach(x => { const b = L.refs && L.refs[x.id]; if (!b) return; const fg = vrEl('g', { class: 'vrref', 'data-ref': x.id }, root); const tone = vrTone(x.status), cnt = g.nodes.filter(n => { for (let p = n.group, k = 0; p && k < 50; k++) { if (p === x.id) return n.kind !== 'note'; const q = (g.groups || []).find(y => y.id === p); p = q && q.parent; } return false; }).length;
      vrEl('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 4, fill: 'none', stroke: 'var(--' + tone + ')', 'stroke-width': 1.4, 'stroke-dasharray': '2 3', 'pointer-events': 'none' }, fg);
      const lab = '▾ ref  ' + x.label + '  (' + cnt + ')', tw = Math.min(b.w - 4, Math.ceil(lab.length * VR_REFCH) + 12); vrEl('rect', { class: 'vrhd', x: b.x, y: b.y, width: tw, height: 16, fill: 'var(--card)', stroke: 'var(--' + tone + ')', 'stroke-width': 1, 'data-tog': x.id, style: 'cursor:pointer' }, fg);
      const rt = vrEl('text', { x: b.x + 6, y: b.y + 12, 'font-size': 11, fill: 'var(--fg)', 'pointer-events': 'none' }, fg); rt.textContent = lab.length * VR_REFCH + 12 > tw ? lab.slice(0, Math.max(1, Math.floor((tw - 12) / VR_REFCH) - 1)) + '…' : lab; vrEl('title', {}, fg).textContent = 'Click the header to fold ' + x.label + ' (' + cnt + ' messages)'; });
    g.edges.forEach(e => { const pts = L.edges[e.id]; if (!pts) return; const gg = vrEl('g', { 'data-id': e.id, 'data-status': e.status, 'data-resolution': e.resolution || '', class: 'vre', tabindex: 0 }, root); els[e.id] = { g: gg, status: e.status, edge: [e.source, e.target] };
      const ps = pts.map(p => p.join(',')).join(' '), inf = vrInferred(e, g);
      vrEl('polyline', { points: ps, fill: 'none', stroke: 'transparent', 'stroke-width': 12 }, gg);
      vrEl('polyline', { class: 'vrline', points: ps, fill: 'none', stroke: e.status === 'context' ? 'var(--mut)' : 'var(--' + vrTone(e.status) + ')', 'stroke-width': (e.status === 'context' ? 1.2 : 2.2) + (e.count > 1 ? Math.min(0.8, Math.log2(e.count) * 0.3) : 0), 'stroke-dasharray': inf ? '6 4' : '0', 'marker-end': 'url(#vr-ar)' }, gg);
      if (e.bidirectional) gg.querySelector('.vrline').setAttribute('marker-start', 'url(#vr-ar)');
      tip(gg, e); });
    shLeg.hidden = !L.shapes;
    const ds = g.view === 'decision' ? vrDecisionStats(g) : null; dcLeg.hidden = !ds; svg.setAttribute('aria-label', ds ? 'Decision flow: ' + g.nodes.length + ' nodes, ' + ds.decisions[1] + ' decisions, ' + ds.paths[1] + ' paths' : 'Change diagram');
    if (ds) dcLeg.textContent = 'decisions ' + ds.decisions[0] + ' → ' + ds.decisions[1] + ' · paths ' + ds.paths[0] + ' → ' + ds.paths[1] + (ds.errorEnds ? ' · new error ends ' + ds.errorEnds : '');
    g.nodes.forEach(n => { const b = L.boxes[n.id]; if (!b) return; const gg = vrEl('g', { 'data-id': n.id, 'data-status': n.status, class: 'vrn', tabindex: 0, role: 'button' }, root); els[n.id] = { g: gg, status: n.status, col: !!n.collapsed }; const sh = (L.shapes && L.shapes[n.id]) || 'rect'; vrEl('title', {}, gg).textContent = n.label + ' (' + (n.kind || 'node') + (sh === 'rect' ? '' : ', ' + sh) + ', ' + n.status + ')';
      if (n.collapsed) { gg.setAttribute('data-collapsed', '1'); vrEl('rect', { x: b.x + 5, y: b.y + 5, width: b.w, height: b.h, rx: 6, fill: 'var(--card)', stroke: 'var(--mut)', 'stroke-width': 1.5 }, gg); }
      const sqMsg = !!(L.arrows && L.arrows[n.id] && L.texts && L.texts[n.id]), sqRef = g.view === 'seq' && !!n.collapsed;
      if (sqMsg) {
        /* a sequence message: no card, its label sits on the arrow (a halo keeps it legible over lifelines), the status mark before it */
        vrEl('rect', { x: b.x - 2, y: b.y - 2, width: b.w + 4, height: b.h + 4, fill: 'transparent' }, gg);
        const tt = vrEl('text', { x: b.x, y: b.y + 11, 'font-size': 12, fill: 'var(--fg)', stroke: 'var(--bg)', 'stroke-width': 4, 'paint-order': 'stroke', 'stroke-linejoin': 'round', 'text-decoration': n.status === 'removed' ? 'line-through' : 'none' }, gg);
        L.texts[n.id].forEach((t, i) => { const ts = vrEl('tspan', { x: b.x, dy: i ? 15 : 0 }, tt); ts.textContent = t; });
        vrSeqArrow(gg, L.arrows[n.id], n.status); const mk1 = L.marks && L.marks[n.id]; if (n.status !== 'context' && mk1) vrMark(gg, n.status, mk1.x + 9, mk1.y + 9); tip(gg, n); return;
      }
      if (sqRef) {
        /* a folded ref: a band across the lanes it touches, the group label, the count of messages and the status colour; a click opens it in place */
        vrEl('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 4, fill: 'var(--' + vrBg(n.status) + ')', stroke: 'var(--' + vrTone(n.status) + ')', 'stroke-width': n.status === 'context' ? 1.4 : 2.2, 'stroke-dasharray': '2 3', style: 'cursor:pointer' }, gg);
        const rt = vrEl('text', { x: b.x + 10, y: b.y + b.h / 2 + 4, 'font-size': 12, fill: 'var(--fg)', 'pointer-events': 'none' }, gg); rt.textContent = '▸ ' + L.texts[n.id][0]; vrMark(gg, n.status, b.x + b.w - 14, b.y + b.h / 2); tip(gg, n); return;
      }
      vrShapeEl(sh, b, { fill: 'var(--' + vrBg(n.status) + ')', stroke: 'var(--' + vrTone(n.status) + ')', 'stroke-width': n.status === 'context' ? 1 : 2.5, 'stroke-dasharray': n.status === 'removed' ? '3 3' : '0' }, gg);
      const fit = vrFitLabel(n.label, b.w, !!n.stereotype, g.view, sh); const lines = fit.lines.slice();
      if (n.stereotype) lines.unshift('<<' + n.stereotype + '>>');
      if (fit.clipped) { const ctr = !!VR_CENTER[sh]; const w = vrEl('text', { x: ctr ? b.x + b.w / 2 : b.x + b.w - 10, y: b.y + b.h - 6, 'font-size': 12, 'font-weight': 700, fill: 'var(--bad)', 'text-anchor': 'middle' }, gg); w.textContent = '⚠'; const tt = vrEl('title', {}, w); tt.textContent = 'This label does not fit the box (' + fit.needed + ' lines needed): the text is cut off.'; }
      if (sh !== 'junction') lines.forEach((t, i) => { const mid = VR_CENTER[sh] ? { x: b.x + b.w / 2, y: b.y + b.h / 2 + 4 + (i - (lines.length - 1) / 2) * 15, 'text-anchor': 'middle' } : { x: b.x + 12, y: b.y + 19 + i * 15 }; const x = vrEl('text', Object.assign({ 'font-size': 12, fill: 'var(--fg)', 'text-decoration': n.status === 'removed' ? 'line-through' : 'none' }, mid), gg); x.textContent = (n.collapsed && i === 0 ? '▸ ' : '') + t; });
      const mk0 = vrMarkAt(sh, b); vrMark(gg, n.status, mk0[0], mk0[1]);
      if (L.arrows && L.arrows[n.id]) vrSeqArrow(gg, L.arrows[n.id], n.status);
      if (n.collapsed) { const kinds = {}; (n.member_kinds || []).forEach(k => kinds[k] = (kinds[k] || 0) + 1); const ks = Object.keys(kinds).sort((a, c) => kinds[c] - kinds[a]); const word = ks.length === 1 ? ks[0] + (n.members === 1 ? '' : 's') : (n.members === 1 ? 'item' : 'items'); const q = vrEl('text', { x: b.x + b.w - 8, y: b.y + b.h - 7, 'font-size': 11, 'text-anchor': 'end', fill: 'var(--mut)' }, gg); q.textContent = (n.members || 0) + ' ' + word; }
      else if (n.members && n.members.length) { const q = vrEl('text', { x: b.x + b.w - 8, y: b.y + b.h - 7, 'font-size': 11, 'text-anchor': 'end', fill: 'var(--mut)' }, gg); q.textContent = n.members.length + ' member' + (n.members.length === 1 ? '' : 's') + ' (hover)'; }
      else if (!vrHand(g) && (n.evidence || []).some(x => x.tier === 'inferred')) { const q = vrEl('text', { x: b.x + b.w - 8, y: b.y + b.h - 7, 'font-size': 11, 'text-anchor': 'end', fill: 'var(--mut)' }, gg); q.textContent = '? inferred'; }
      tip(gg, n); });
    /* Edge labels sit where the layout kept room for them (L.labels); without that, on the segment where they cross no node box. On top, with a --bg backing. */
    const nbx = g.nodes.map(n => L.boxes[n.id]).filter(Boolean), hit = (x, y, w, h) => nbx.some(b => x < b.x + b.w && x + w > b.x && y < b.y + b.h && y + h > b.y);
    g.edges.forEach(e => { const pts = L.edges[e.id]; if (!pts || !e.label || !els[e.id]) return; const w = Math.ceil(e.label.length * 6.1) + 8, h = 15, cs = [];
      for (let i = 0; i + 1 < pts.length; i++) { const a = pts[i], c = pts[i + 1], len = Math.abs(a[0] - c[0]) + Math.abs(a[1] - c[1]); for (const f of [0.5, 0.3, 0.7, 0.15, 0.85]) cs.push({ x: a[0] + (c[0] - a[0]) * f - w / 2, y: a[1] + (c[1] - a[1]) * f - h / 2 - 1, len: len - Math.abs(f - 0.5) * 8 }); }
      cs.sort((p, q) => q.len - p.len); const kept = L.labels && L.labels[e.id]; const pick = kept ? { x: kept.x + (kept.w - w) / 2, y: kept.y + (kept.h - h) / 2 } : cs.find(c => !hit(c.x, c.y, w, h)) || cs[0]; if (!pick) return;
      const lg = vrEl('g', { class: 'vrel', 'pointer-events': 'none' }, root); els[e.id].lab = lg; vrEl('rect', { x: pick.x, y: pick.y, width: w, height: h, rx: 3, fill: 'var(--bg)', 'fill-opacity': 0.88 }, lg);
      const t = vrEl('text', { x: pick.x + w / 2, y: pick.y + h - 4, 'font-size': 11, 'text-anchor': 'middle', fill: 'var(--mut)' }, lg); t.textContent = e.label; });
    mapBuild(); applyMode();
    if (anchor && L.boxes[anchor.id]) { const b = L.boxes[anchor.id]; vp.x = anchor.sx - b.x * vp.k; vp.y = anchor.sy - b.y * vp.k; apply(); } else fit();
    paintBadges(); if (selId && els[selId]) inspect(selId);
  }
  function vrLane(v) { return v === 'L1' || v === 'seq'; }
  function vrDown(v) { return vrLane(v) || v === 'decision'; }
  function vrBg(s) { return s === 'context' ? 'chip' : vrTone(s) + 'bg'; }
  function vrTone(s) { return s === 'added' ? 'ok' : s === 'changed' ? 'warn' : s === 'removed' ? 'bad' : 'mut'; }
  function vrMark(p, s, cx, cy) { const m = vrEl('g', { class: 'vr-mark', 'data-mark': s }, p); vrEl('circle', { cx: cx, cy: cy, r: 9, fill: 'var(--card)', stroke: 'var(--' + vrTone(s) + ')', 'stroke-width': 1.5 }, m); const t = vrEl('text', { x: cx, y: cy + 4, 'text-anchor': 'middle', 'font-size': 12, 'font-weight': 700, fill: 'var(--' + vrTone(s) + ')' }, m); t.textContent = VR_MARK[s]; }
  function toMermaid() {
    const g = cur.graph, esc = s => String(s).replace(/#/g, '#35;').replace(/"/g, '#quot;').replace(/</g, '#lt;').replace(/>/g, '#gt;').replace(/[\r\n]+/g, ' ');
    const vis = id => els[id] && !els[id].g.classList.contains('vroff'); const idOf = {}; let n = 0; const mid = id => idOf[id] = idOf[id] || ('n' + (n++));
    const lanes = vrLane(g.view) && (g.lanes || []).length; const cont = [].concat(g.groups || [], lanes ? g.lanes : []).filter(c => vis(c.id));
    const kids = {}, top = []; const put = (id, p) => { (p && cont.some(c => c.id === p) ? (kids[p] = kids[p] || []) : top).push(id); };
    cont.forEach(c => put(c.id, lanes && g.lanes.some(l => l.id === c.id) ? '' : c.parent)); g.nodes.filter(x => vis(x.id)).forEach(x => put(x.id, lanes ? x.lane : x.group));
    const out = ['flowchart ' + (vrDown(g.view) ? 'TD' : 'LR')], isC = id => cont.some(c => c.id === id), lab = {}; cont.forEach(c => lab[c.id] = c.label); g.nodes.forEach(x => lab[x.id] = x.label);
    const emit = (id, ind) => { if (isC(id)) { out.push(ind + 'subgraph ' + mid(id) + '["' + esc(lab[id]) + '"]'); (kids[id] || []).forEach(k => emit(k, ind + '  ')); out.push(ind + 'end'); } else { const br = VR_MM[(cur.layout && cur.layout.shapes && cur.layout.shapes[id]) || 'rect']; out.push(ind + mid(id) + br[0] + '"' + esc(MARK(id) + ' ' + lab[id]) + '"' + br[1]); } };
    const MARK = id => { const x = g.nodes.find(y => y.id === id); return x ? VR_MARK[x.status] : ''; };
    top.forEach(id => emit(id, '  '));
    g.edges.filter(e => vis(e.id)).forEach(e => { const d = vrInferred(e, g), l = e.label ? '"' + esc(e.label) + '"' : '', b = e.bidirectional ? '<' : ''; out.push('  ' + mid(e.source) + (l ? (d ? ' ' + b + '-. ' + l + ' .-> ' : ' ' + b + '-- ' + l + ' --> ') : (d ? ' ' + b + '-.-> ' : ' ' + b + '--> ')) + mid(e.target)); });
    out.push('  classDef added fill:#cfeccf,stroke:#2d7a2d', '  classDef changed fill:#fde9b8,stroke:#b07a00', '  classDef removed fill:#f6d0d0,stroke:#a33,stroke-dasharray:3 3', '  classDef context fill:#eeeeea,stroke:#999');
    ['added', 'changed', 'removed', 'context'].forEach(s => { const ids = g.nodes.filter(x => x.status === s && vis(x.id)).map(x => mid(x.id)); if (ids.length) out.push('  class ' + ids.join(',') + ' ' + s); });
    return out.join('\n');
  }
  function showMm() { mmText.value = toMermaid(); }
  mmB.onclick = () => { mm.hidden = !mm.hidden; if (!mm.hidden) showMm(); };
  mmCopy.onclick = () => { showMm(); mmText.select(); let p; try { p = navigator.clipboard.writeText(mmText.value); } catch (e) { p = null; } if (!p) { try { document.execCommand('copy'); } catch (e) {} } mmCopy.textContent = 'Copied'; setTimeout(() => { mmCopy.textContent = 'Copy'; }, 1500); };
  fitB.onclick = () => { st.fitAll = !st.fitAll; fitB.textContent = st.fitAll ? 'Readable size' : 'Fit all'; fitB.title = st.fitAll ? 'Back to the size where labels are readable (12 px)' : 'Show the whole diagram (labels may get small)'; fit(); }; zin.onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(1.25, r.width / 2, r.height / 2); }; zout.onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(0.8, r.width / 2, r.height / 2); };
  let panMoved = false;
  svg.addEventListener('pointerdown', e => { panMoved = false; const own = e.target.closest('[data-id]'); /* a group box pans; a node, a group header and a folded box act on click */ if (own && (!own.classList.contains('vrg') || e.target.closest('[data-tog]') || (els[own.getAttribute('data-id')] && els[own.getAttribute('data-id')].col))) return; panMoved = false; drag = { x: e.clientX - vp.x, y: e.clientY - vp.y, sx: e.clientX, sy: e.clientY, id: e.pointerId }; if (!own) { svg.setPointerCapture(e.pointerId); svg.classList.add('drag'); } });
  svg.addEventListener('pointermove', e => { if (!drag) return; if (!panMoved && Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) { panMoved = true; if (!svg.hasPointerCapture(drag.id)) { svg.setPointerCapture(drag.id); svg.classList.add('drag'); } } if (!panMoved) return; vp.x = e.clientX - drag.x; vp.y = e.clientY - drag.y; apply(); });
  svg.addEventListener('pointerup', () => { drag = null; svg.classList.remove('drag'); });
  svg.addEventListener('wheel', e => { if (!(e.ctrlKey || e.metaKey)) { /* a vertical wheel always scrolls the page (it used to pan the canvas off screen); only a horizontal swipe pans, and only while the diagram is wider than the box */ if (!cur || !cur.layout || Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return; const r = svg.getBoundingClientRect(); if (cur.layout.width * vp.k + 40 <= r.width) return; e.preventDefault(); vp.x -= e.deltaX; apply(); return; } e.preventDefault(); const r = svg.getBoundingClientRect(); zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  svg.addEventListener('click', e => { if (panMoved) { panMoved = false; return; } const tg = e.target.closest('[data-tog]'); if (tg) { toggle(tg.getAttribute('data-tog')); return; } const t = e.target.closest('[data-id]'); if (!t) return; const id = t.getAttribute('data-id'); if (els[id] && els[id].col) toggle(id); else inspect(id); });
  svg.addEventListener('keydown', e => { if (e.key !== 'Enter' && e.key !== ' ') return; const t = e.target.closest && e.target.closest('[data-id]'); if (t) { e.preventDefault(); const id = t.getAttribute('data-id'); if (els[id] && els[id].col || canCol(id)) toggle(id); else inspect(id); } });
  /* Mini-map: boxes at 170x110 at most, a rectangle for what the canvas shows; click or drag moves the view. Only when the graph is larger than the canvas. */
  function mapBuild() {
    const L = cur.layout, W = Math.max(L.width, 1), H = Math.max(L.height, 1), k = Math.min(170 / W, 110 / H);
    ovSvg = vrEl('svg', { viewBox: '0 0 ' + W + ' ' + H, width: Math.max(8, Math.round(W * k)), height: Math.max(8, Math.round(H * k)) }, ov); ovRects = {};
    const one = (id, cls, status) => { const b = L.boxes[id]; if (b) ovRects[id] = vrEl('rect', { class: cls + ' ' + status, x: b.x, y: b.y, width: b.w, height: b.h }, ovSvg); };
    (cur.graph.groups || []).concat(cur.graph.lanes || []).forEach(c => one(c.id, 'vrovg', c.status || 'context')); cur.graph.nodes.forEach(n => one(n.id, 'vrovn', n.status));
    ovVp = vrEl('rect', { class: 'vrovvp' }, ovSvg);
  }
  function mapUpd() {
    if (!cur || !cur.layout || !ovVp) return; const r = svg.getBoundingClientRect(), L = cur.layout; if (L.arrows) { ov.hidden = true; return; } /* a sequence is a narrow column of lanes: the overview would sit on top of them */
    const big = L.width * vp.k > r.width + 1 || L.height * vp.k > r.height + 1; ov.hidden = !big; if (!big) return;
    ovVp.setAttribute('x', -vp.x / vp.k); ovVp.setAttribute('y', -vp.y / vp.k); ovVp.setAttribute('width', r.width / vp.k); ovVp.setAttribute('height', r.height / vp.k);
  }
  let mdrag = false;
  const mapTo = e => { if (!ovSvg) return; const q = ovSvg.getBoundingClientRect(), r = svg.getBoundingClientRect(), s = q.width / cur.layout.width; vp.x = r.width / 2 - ((e.clientX - q.left) / s) * vp.k; vp.y = r.height / 2 - ((e.clientY - q.top) / s) * vp.k; apply(); };
  ov.addEventListener('pointerdown', e => { if (!ovSvg) return; mdrag = true; ov.setPointerCapture(e.pointerId); mapTo(e); e.preventDefault(); });
  ov.addEventListener('pointermove', e => { if (mdrag) mapTo(e); });
  ov.addEventListener('pointerup', () => { mdrag = false; }); ov.addEventListener('pointercancel', () => { mdrag = false; });
  /* Walkthrough: the added, changed and removed elements of the view in reading order (vrWalkOrder), one at a time. */
  const wlist = () => vrWalkOrder(cur.graph, cur.layout).filter(id => els[id] && !els[id].g.classList.contains('vroff'));
  function centre(id) {
    const L = cur.layout; let b = L.boxes[id]; const ps = L.edges[id];
    if (!b && ps && ps.length) { const xs = ps.map(p => p[0]), ys = ps.map(p => p[1]); b = { x: Math.min.apply(null, xs), y: Math.min.apply(null, ys), w: Math.max.apply(null, xs) - Math.min.apply(null, xs), h: Math.max.apply(null, ys) - Math.min.apply(null, ys) }; }
    if (!b) return; const r = svg.getBoundingClientRect(), fitK = Math.min(r.width / (b.w + 80), r.height / (b.h + 80));
    vp.k = Math.min(Math.max(vp.k, 0.7), Math.max(fitK, 0.15)); vp.x = r.width / 2 - (b.x + b.w / 2) * vp.k; vp.y = r.height / 2 - (b.y + b.h / 2) * vp.k; apply();
  }
  function wkClear() { svg.querySelectorAll('.vrhalo').forEach(x => x.remove()); Object.keys(els).forEach(k => els[k].g.classList.remove('walk')); }
  function wkShow(quiet) {
    wkClear(); const id = wk.list[wk.i], n = wk.list.length, e = els[id]; if (!e) return;
    e.g.classList.add('walk'); const b = cur.layout.boxes[id];
    if (b) vrEl('rect', { class: 'vrhalo', x: b.x - 5, y: b.y - 5, width: b.w + 10, height: b.h + 10, rx: 10, fill: 'none', stroke: 'var(--acc)', 'stroke-width': 3, 'stroke-dasharray': '7 4' }, e.g);
    const it = items()[id]; wkInd.textContent = 'Step ' + (wk.i + 1) + ' of ' + n; wkLab.textContent = it ? (it.label || (it.source + ' → ' + it.target)) + ' [' + it.status + ']' : '';
    wkPrev.disabled = wk.i === 0; wkNext.disabled = wk.i === n - 1;
    if (!quiet) { centre(id); inspect(id); }
  }
  function wkOn() {
    if (!cur || !cur.layout) return; const l = wlist();
    if (!l.length) { note.textContent = 'No added, changed or removed element to walk through in this view and mode.'; return; }
    wk.on = true; wk.list = l; wk.i = Math.max(0, l.indexOf(st.sel)); wbar.hidden = false; wkB.setAttribute('aria-pressed', 'true'); svg.classList.add('vrwk'); wkShow(false); cv.focus();
  }
  function wkOff() { if (!wk.on) return; wk.on = false; wbar.hidden = true; wkB.setAttribute('aria-pressed', 'false'); svg.classList.remove('vrwk'); wkClear(); }
  function wkSync() { const id = wk.list[wk.i], l = wlist(); if (!l.length) { wkOff(); return; } wk.list = l; const j = l.indexOf(id); wk.i = j >= 0 ? j : Math.min(wk.i, l.length - 1); wkShow(true); }
  const wkStep = d => { if (!wk.on) return; const i = wk.i + d; if (i < 0 || i >= wk.list.length) return; wk.i = i; wkShow(false); };
  const wkKey = e => {
    if (!wk.on || e.ctrlKey || e.metaKey || e.altKey || (e.target && /^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName))) return; const k = e.key;
    if (k === 'Escape') { e.preventDefault(); wkOff(); } else if (k === 'ArrowRight' || k === 'ArrowDown' || k === 'n') { e.preventDefault(); wkStep(1); } else if (k === 'ArrowLeft' || k === 'ArrowUp' || k === 'p') { e.preventDefault(); wkStep(-1); }
  };
  cv.addEventListener('keydown', wkKey); wbar.addEventListener('keydown', wkKey);
  wkB.onclick = () => { if (wk.on) wkOff(); else wkOn(); }; wkPrev.onclick = () => wkStep(-1); wkNext.onclick = () => wkStep(1); wkExit.onclick = wkOff;
  if (reviewing && !st.review) st.review = data.review;
  function setIntent(on) { on = !!on && !!intent; st.intent = on; inBox.hidden = !on; body.hidden = on; leg.hidden = on || !!data.mapPath; if (inB) inB.setAttribute('aria-pressed', String(on)); if (on) { wkOff(); Object.keys(tb).forEach(k => tb[k].setAttribute('aria-pressed', 'false')); } }
  draw(); paintBar(); setFs(!!st.fs); setIntent(st.intent === undefined ? !!intent : st.intent);
}

// ---- state ----
const S = { sweep: {}, dm: { list: [], conv: null, enabled: true, busy: false, err: '' }, q: [], runs: [], accounts: [], usageBusy: false, slack: false, scannedAt: '', stubs: { repos: [], openWork: [] }, loaded: false, detail: null, live: false, changedAt: 0, showAll: {}, ctl: {}, pend: {}, qerr: {}, tab: {}, po: {}, fb: {}, fbBusy: null, fbErr: null, rulings: {}, frameH: {}, dsel: {}, chat: {}, docPrs: {}, auto: {}, flows: {} };
let R = { kind: 'home' };
let routeSig = '';
let scrollTarget = '';

function parseRoute() {
  const seg = location.pathname.split('/').filter(Boolean).map(decodeURIComponent);
  if (!seg.length) return { kind: 'home' };
  if (seg[0] === 'questions') return { kind: 'questions' };
  if (seg[0] === 'chat') return { kind: 'chat', chat: seg[1] || '' };
  if (seg[0] === 'repo' && seg[1]) {
    const repo = seg[1];
    if (seg.length === 2) return { kind: 'repo', repo };
    if (seg[2] === 'stub' && seg[3]) return { kind: 'stub', repo, branch: seg.slice(3).join('/') };
    if (seg[2] === 'graph') return { kind: 'repo', repo };
    if (seg[2] === 'architecture' && seg.length === 3) return { kind: 'arch', repo };
    return { kind: 'item', repo, id: seg[2], forceDossier: seg[3] === 'dossier' };
  }
  return { kind: 'home' };
}
function hashQ() { const m = location.hash.match(/^#(?:q=)?(.+)$/); return m ? decodeURIComponent(m[1]) : ''; }
const hrefRepo = (repo) => '/repo/' + enc(repo);
const hrefItem = (repo, id) => '/repo/' + enc(repo) + '/' + enc(id);
const hrefArch = (repo, path) => '/repo/' + enc(repo) + '/architecture' + (path ? '?d=' + enc(path) : '');
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
const RUNNER_LIVE = ['running', 'testing', 'waiting', 'paused', 'queued'];
const runnerActive = (rn) => !!rn && RUNNER_LIVE.includes(rn.state);
/* What each runner command is doing, in the words of the blue chip: one map for every view. Mirrors COMMAND_LABEL in timeline.ts. */
const CMD_DOING = { plan: 'planning', 'fix-deferred': 'fixing deferred items', 'resolve-merge': 'the agent is fixing the merge', 'after-merge': 'cleaning up after the merge', stub: 'integrating', 'product-summary': 'writing the product summary', 'merge-queue': 'merging the finished dossiers' };
function stageText(r) {
  const live = runnerActive(r.runner);
  if (live && r.runner.command && r.runner.command !== 'work-on' && CMD_DOING[r.runner.command]) return CMD_DOING[r.runner.command].replace(/^./, c => c.toUpperCase());
  if (!r.phase || r.phase === 'plan') return live ? 'Starting' : '';
  return 'Phase ' + r.phase + (r.phaseName ? ' · ' + r.phaseName : '');
}
/* The blue "fixing" chip of something that is working right now: the command's words (or the merge queue's step, or the
   build's phase), with the wait appended; queued is its own colour, waiting for you is warn. Null when nothing runs, so the
   caller keeps its static status chip. force: a dossier worked on in a terminal (no runner) still reads "working". */
function activityChip(r, force) {
  const rn = r && r.runner; const m = r && r.merge;
  const step = m && MERGE_STEP[m.state] ? MERGE_STEP[m.state] : '';
  if (!step && !runnerActive(rn) && !force) return null;
  return activityOf(rn && runnerActive(rn) ? rn : null, step, r && r.updates && r.updates[0] ? r.updates[0].text.slice(0, 200) : '', r);
}
function activityOf(rn, step, title, r) {
  let what = step || (rn ? (rn.command === 'work-on' || !rn.command ? ((r && stageText(r)) || 'building') : (CMD_DOING[rn.command] || rn.command)) : ((r && stageText(r)) || 'working'));
  if (waitsForStub(rn)) what = 'queued · waiting for the stub'; else if (rn && rn.state === 'waiting' && rn.waitingFor === 'ci') what += ' · waiting on CI'; else if (rn && rn.state === 'waiting' && rn.waitingFor === 'login') what += ' · waiting for an account sign-in'; else if (rn && rn.state === 'waiting') what += ' · waiting for you'; else if (rn && rn.state === 'paused') what += ' · paused (usage limit)'; else if (rn && rn.state === 'queued') what += ' · queued for a slot';
  // Queued (for a slot, or for the stub) is its own colour with a steady dot: nothing runs yet.
  const queued = waitsForStub(rn) || (rn && rn.state === 'queued');
  const cls = queued ? 'queued' : rn && rn.state === 'waiting' ? 'warn' : 'fixing';
  return h('span', { class: 'chip ' + cls, title: title || '' }, [h('span', { class: queued ? 'dot que' : 'dot info live' }), what]);
}
/* The run's state in plain words, no "runner" jargon. */
const RUN_STATE = { running: 'in progress', testing: 'testing \u00b7 slot released', waiting: 'waiting for you', paused: 'paused · usage limit', queued: 'queued · waiting for a slot', parked: 'parked · waits for its blockers', stopped: 'stopped', failed: 'failed', done: 'done' };
const waitsForStub = (rn) => !!rn && rn.state === 'waiting' && rn.waitingFor === 'stub';
/* A runner's state, or just a state's name. A run held for the stub is not waiting for you. */
const runChip = (x) => { const st = (x && x.state) || x; if (st === 'parked' && x && x.parkedFor && x.parkedFor.length) return h('span', { title: 'starts by itself once every blocker is done' }, [chip('parked · waits for ' + x.parkedFor.join(', '), 'queued')]); if (x && st === 'waiting' && x.waitingFor === 'ci') return chip('waiting on CI', 'running'); if (x && st === 'waiting' && x.waitingFor === 'login') return chip('waiting for an account sign-in', 'waiting'); return waitsForStub(x) ? chip('queued · waiting for the stub', 'queued') : chip(RUN_STATE[st] || st, st === 'parked' ? 'queued' : st); };
function itemMode(run) { if (R.forceDossier) return 'dossier'; return run.view === 'run' ? 'run' : 'dossier'; }
function newestStub(repo) { const r = S.stubs.repos.find(x => x.repo === repo); return r && r.stubs.length ? r.stubs[0] : null; }
const doneKey = (r) => (r.updated || '') + r.id;

// ---- questions ----
/* Dossier ids repeat across repos: a question's own repo wins whenever the page knows it, even before that repo's run of the dossier shows up. */
function qRepo(q) { const c = S.runs.filter(x => x.id === q.dossier); const r = c.find(x => x.repo === q.repo) || c.find(x => q.root && (q.root === x.worktree || q.root.indexOf(x.worktree + '/') === 0 || q.root === x.main || q.root.indexOf(x.main + '/') === 0)) ; if (r) return r.repo; if (q.repo && repoNames().includes(q.repo)) return q.repo; return c.length === 1 ? c[0].repo : q.repo; }
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
  const who = l.from === 'runner' ? 'closed automatically' : l.accepted ? 'agent · took your words as the answer' : l.from === 'owner' && l.kind === 'followup' ? 'you' : l.from + ' ' + l.kind;
  return h('div', { class: 'msg ' + l.from }, [h('div', { class: 'who' }, [who + (l.option ? ' ' + l.option : '') + (l.via ? ' via ' + l.via : '') + ' · ', when(l.ts)]), h('div', {}, [l.text])]);
}
/* Question text as light markdown, built with h() (never innerHTML): blank lines split paragraphs, '- ' / '1. ' lines
   are lists, '---' a rule, '# x' a small heading, **bold** and code spans inline; other line breaks are kept. */
function mdInline(t) {
  return String(t).split(/(\*\*[^*\n]+\*\*|\x60[^\x60\n]+\x60)/).filter(x => x !== '').map(x =>
    /^\*\*[^*]+\*\*$/.test(x) ? h('strong', {}, [x.slice(2, -2)]) : /^\x60[^\x60]+\x60$/.test(x) ? h('code', {}, [x.slice(1, -1)]) : x);
}
function mdLite(text) {
  const out = []; let para = null, list = null;
  const end = () => { para = null; list = null; };
  String(text || '').split('\n').forEach(raw => {
    const t = raw.trim();
    if (!t) return end();
    if (/^(-{3,}|\*{3,})$/.test(t)) { end(); out.push(h('hr', {}, [])); return; }
    const hd = /^#{1,6}\s+(.*)$/.exec(t);
    if (hd) { end(); out.push(h('h4', {}, mdInline(hd[1]))); return; }
    const li = /^([-*]|\d+[.)])\s+(.*)$/.exec(t);
    if (li) {
      const tag = /\d/.test(li[1]) ? 'ol' : 'ul';
      if (!list || list.tag !== tag) { para = null; list = { tag, el: h(tag, {}, []) }; out.push(list.el); }
      list.el.appendChild(h('li', {}, mdInline(li[2]))); return;
    }
    list = null;
    if (para) { para.appendChild(h('br', {}, [])); mdInline(t).forEach(k => para.appendChild(typeof k === 'string' ? document.createTextNode(k) : k)); }
    else { para = h('p', {}, mdInline(t)); out.push(para); }
  });
  return out;
}
// A multi-line question follows the card layout (references/owner-questions.md, "The card layout"): its first line
// is the decision, shown as the heading, and everything after a Details line is folded.
function qBody(text) {
  const lines = String(text || '').split('\n');
  let i = 0; while (i < lines.length && !lines[i].trim()) i++;
  const head = (lines[i] || '').trim().replace(/^#{1,6}\s+/, '');
  const rest = lines.slice(i + 1);
  const di = rest.findIndex(l => /^\s*(\*\*Details\*\*:?|#{1,6}\s+Details|Details:)\s*$/i.test(l));
  const front = di < 0 ? rest : rest.slice(0, di), back = di < 0 ? [] : rest.slice(di + 1);
  return [h('div', { class: 'qh' }, mdInline(head))].concat(mdLite(front.join('\n')),
    back.some(l => l.trim()) ? [h('details', { class: 'qdet' }, [h('summary', {}, ['Details']), h('div', {}, mdLite(back.join('\n')))])] : []);
}
function qCard(q, opts) {
  const open = q.state === 'open'; const repo = qRepo(q);
  const kids = [
    h('div', { class: 'top' }, [chip(rlabel(repo), 'neutral'), h('a', { class: 'chip', href: hrefItem(repo, q.dossier) }, [q.dossier]), h('span', { class: 'grow' }), q.qkind ? chip(q.qkind, 'neutral') : null, h('span', { class: 'mono' }, [q.id + ' · ']), opts.queue ? when(q.ts, 'waiting ') : when(q.ts)]),
    /\n/.test(q.text || '') ? h('div', { class: 'q md' }, qBody(q.text)) : h('div', { class: 'q' }, [q.text]),
    // The diagrams live on the dossier page, not in the card: link them, whatever the question text says.
    q.topic === 'visual-review' && opts.strip ? h('div', { class: 'ctl' }, [h('button', { class: 'btn primary', type: 'button', 'data-act': 'pane-go', 'data-pane': 'diagram' }, ['Open the diagrams for ' + q.dossier])]) : q.topic === 'visual-review' ? h('div', { class: 'ctl' }, [h('a', { class: 'btn primary', href: hrefItem(repo, q.dossier), 'data-k': 'vrlink' }, ['Open the diagrams for ' + q.dossier])]) : null,
    q.blocks ? h('div', { class: 'blocks' }, ['Blocks: ' + q.blocks]) : null];
  // An open question is a chat until a button closes it: your messages and the agent's replies, in order, then the
  // buttons (the question's own, plus any the agent offered in a reply, such as its reading of your words).
  const talk = q.lines.slice(1).filter(l => (l.from === 'owner' && l.kind === 'followup') || (l.from === 'agent' && l.kind === 'reply'));
  if (open && talk.length) {
    const last = talk[talk.length - 1];
    kids.push(h('div', { class: 'qchat', 'data-k': 'qc:' + q.id }, talk.map((l, i) => h('div', { class: 'cm ' + (l.from === 'owner' ? 'me' : 'them'), 'data-k': 'qc:' + q.id + ':' + i }, [
      h('div', { class: 'who' }, [(l.from === 'owner' ? 'You' : 'Agent') + (l.via ? ' via ' + l.via : '') + ' · ', when(l.ts)]), h('div', { class: 'tx' }, [l.text]),
      l.options && l.options.length ? h('div', { class: 'small mut' }, ['Added ' + l.options.map(o => o.key + ' · ' + o.label).join(', ') + ' to the buttons below']) : null]))
      .concat(last.from === 'owner' ? [h('div', { class: 'cm them wait', 'data-k': 'qc:' + q.id + ':wait' }, [h('span', { class: 'dot info live' }), ' Waiting for the agent\'s reply…'])] : [])));
  }
  kids.push(optButtons(q));
  if (opts.thread && !open) kids.push(h('div', {}, q.lines.slice(1).map(msgBlock)));
  if (open) kids.push(h('textarea', { placeholder: talk.length ? 'Reply to the agent' : 'Ask the agent something, or tell it what you want', 'data-qid': q.id, 'aria-label': 'Message to the agent about ' + q.id }),
    h('div', { class: 'ctl' }, [h('button', { class: 'btn primary', type: 'button', 'data-act': 'reply', 'data-qid': q.id }, ['Send']),
      h('span', { class: 'small mut' }, ['Your message goes to the agent and it replies here. Only a button answers and closes the question; the agent adds one for what it takes your words to mean.'])]));
  kids.push(h('div', { class: 'err' }, [S.qerr[q.id] || '']));
  return h('div', { class: 'need' + (open ? '' : ' thr') + (opts.hit === q.id ? ' hit' : ''), id: 'q-' + q.id, 'data-k': 'q:' + q.id + (opts.thread ? ':t' : ':x') }, kids);
}

// ---- controls ----
/* A pressed run button is hidden the moment it is pressed (S.pend, client) and stays hidden while the server holds its marker
   (r.pending: the run is starting). It returns only when the press failed or the run never showed up (the note is in r.pendingNotes). */
const pkey = (r, a) => r.repo + '/' + r.id + ':' + a;
const isPend = (r, a) => !!S.pend[pkey(r, a)] || !!(r.pending && r.pending.indexOf(a) >= 0);
const pressed = (repo, id, a) => { S.pend[repo + '/' + id + ':' + a] = true; };
const settle = (repo, id, a) => { delete S.pend[repo + '/' + id + ':' + a]; };
function controls(r, big) {
  const key = r.repo + '/' + r.id; const st = S.ctl[key] || {}; const kids = [];
  // Start runs the dossier's next flow: Plan for a planned dossier, Start (build) for a ready one.
  const NAME = { start: r.controls && r.controls.start && r.controls.start.command === 'plan' ? 'Plan' : 'Start', resume: 'Resume', stop: 'Stop', 'fix-deferred': 'Fix all deferred' };
  ['start', 'resume', 'stop', 'fix-deferred'].forEach(a => {
    const c = r.controls && r.controls[a]; if (!c || !c.show || (a !== 'stop' && isPend(r, a))) return;
    const confirming = st.confirm === a; const busy = st.busy === a;
    const label = busy ? { start: NAME.start === 'Plan' ? 'Starting the plan…' : 'Starting…', resume: 'Resuming…', stop: 'Stopping…', 'fix-deferred': 'Starting the fix…' }[a] : confirming ? 'Confirm: ' + NAME[a].toLowerCase() : NAME[a];
    kids.push(h('button', { class: 'btn act' + (big ? ' lg' : '') + (big && a !== 'stop' && !confirming ? ' go' : '') + (confirming ? ' confirm ' + a : ''), type: 'button', 'data-act': 'ctl', 'data-ctl': a, 'data-repo': r.repo, 'data-id': r.id, disabled: !c.ok || !!st.busy }, [label]));
    if (confirming) kids.push(h('button', { class: 'btn act' + (big ? ' lg' : ''), type: 'button', 'data-act': 'ctl-cancel', 'data-repo': r.repo, 'data-id': r.id }, ['Cancel']));
    if (!c.ok && c.reason) kids.push(chip(c.reason, 'blocked'));
  });
  if (r.runner && r.runnerLog) kids.push(h('button', { class: 'btn sm ghost', type: 'button', 'data-act': 'copy', 'data-text': r.runnerLog, 'data-label': 'Copy log path', title: r.runnerLog }, ['Copy log path']));
  const out = [];
  if (r.planningRun) kids.push(chip('Being planned by ' + r.planningRun.key.slice(r.repo.length + 1) + ' (' + r.planningRun.state + ')', 'blocked'));
  if (kids.length) out.push(h('div', { class: 'ctl' }, kids));
  const pn = r.pendingNotes && (r.pendingNotes.start || r.pendingNotes.resume || r.pendingNotes['fix-deferred']);
  if (pn) out.push(h('div', { class: 'ctl-msg bad' }, [pn]));
  else if (st.msg) out.push(h('div', { class: 'ctl-msg ' + (st.ok ? 'ok' : 'bad') }, [st.msg]));
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
    diskView(), dirtyMainView(), accountsView(), runningView(), h('div', { class: 'grid' }, cards),
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
function dirtyMainView() {
  const ds = S.dirtyMain || []; if (!ds.length) return null;
  return h('div', { class: 'diskbar', 'data-k': 'dirty-main' }, ds.map(d => h('div', {}, [h('b', {}, [d.repo + ': the main checkout has local edits']),
    ' — ' + d.files.length + ' tracked file(s) changed in ' + d.root + ' (' + d.files.slice(0, 5).join(', ') + (d.files.length > 5 ? ', …' : '') + '). ',
    'Runs never edit it: commit these on a branch from origin/development, or discard them (git checkout -- <file>), so git pull works there again.'])));
}
function diskView() {
  const d = S.disk; if (!d || !d.low) return null;
  return h('div', { class: 'diskbar', 'data-k': 'disk' }, [h('b', {}, ['Disk almost full: ' + Math.round(d.freeGiB) + ' GiB free']),
    ' — below the ' + Math.round(d.floorGiB) + ' GiB floor, so runs queue instead of starting a stage. ',
    d.reclaiming ? 'Reclaiming unused build output now…' : (d.reclaimable ? d.reclaimable + ' idle build folder(s) can go; the next check reclaims them.' : 'Nothing idle is left to reclaim: free space by hand (python3 scripts/disk_guard.py status lists what is kept and why).')]);
}
function accountsView() {
  const as = S.accounts || []; if (!as.length) return null;
  const cards = as.map(a => h('div', { class: 'acard' + (a.next ? ' next' : ''), 'data-k': 'acct:' + a.name }, [
    h('div', { class: 'ahd' }, [h('span', { class: 'aname' }, [a.name]), a.next ? chip('next runs use this', 'done') : null, a.limitedUntil ? chip('blocked ' + fmtReset(a.limitedUntil).replace('resets ', 'until '), 'blocked') : null,
      a.runs.length ? chip(a.runs.length + ' running', 'neutral') : null]),
    h('div', { class: 'small mut' }, [[a.email, a.org].filter(Boolean).join(' · ')]),
    a.expected && a.email && a.expected.toLowerCase() !== a.email.toLowerCase() ? h('div', { class: 'small', title: 'accounts.json binds this account to ' + a.expected + ', but its config dir is logged in as ' + a.email + '. The runner does not use it until it is logged in as ' + a.expected + ' again: CLAUDE_CONFIG_DIR=' + a.dir + ' claude, then /login.' }, [chip('logged in as the wrong account · not used', 'blocked')]) : null,
    a.sameLoginAs ? h('div', { class: 'small', title: 'Both config dirs are logged in with the same email, so they share one set of limits: switching between them gains nothing. Log one of them in with your other account.' }, [chip('same login as ' + a.sameLoginAs, 'blocked')]) : null,
    meter('5-hour', a.fiveHour), meter('Weekly', a.week),
    h('div', { class: 'small mut' }, [a.sampledAt ? when(a.sampledAt, 'read ') : 'never read'])]));
  return h('section', { class: 'sec', 'data-k': 'sec:acct' }, [h('div', { class: 'sechd' }, [h('h2', {}, ['Accounts']), h('span', { class: 'grow' }),
    h('button', { class: 'btn sm', type: 'button', 'data-act': 'usage-refresh', disabled: S.usageBusy ? true : null, title: 'One tiny haiku call per account reads its current windows' }, [S.usageBusy ? 'Checking…' : 'Check usage'])]),
    h('div', { class: 'agrid' }, cards)]);
}
/* Every run consuming tokens right now, across repos: its state, step, account, agents at work, context and last activity. */
/* A /plan run before its dossier exists. From a deferred item it links to the source dossier's Deferred tab; from a
   free-form seed it shows the seed slug, then "W-219 (seed: slug)" once the run numbered the dossier. */
function planLabel(p) { const slug = p.item.replace(/^seed-/, ''); return p.src ? p.src + ' ' + p.item : p.claimed ? p.claimed + ' (seed: ' + slug + ')' : p.item; }
function planRow(p) {
    const right = [activityOf({ command: 'plan', state: p.state }, '', '')]; if (p.account) right.push(h('span', { class: 'rmeta mono', title: 'Account this run uses' }, [p.account]));
    // A free-form /plan (src empty): no source dossier to link to.
    return (h(p.src ? 'a' : 'div', { class: 'srow', ...(p.src ? { href: hrefItem(p.repo, p.src) } : {}), 'data-k': 'plan:' + p.key, 'data-rank': String(p.state === 'waiting' ? 0 : p.state === 'running' || p.state === 'testing' ? 1 : 2) }, [h('span', { class: 'sid mono' }, [planLabel(p)]),
      h('span', { class: 'sname' }, [h('span', {}, ['Planning: ' + p.title]), h('span', { class: 'small mut rsub' }, [rlabel(p.repo) + (p.src ? ' · new dossier from ' + p.src + ' ' + p.item : ' · new dossier from a free-form brief') + (p.lastTs ? ' · ' : ''), p.lastTs ? when(p.lastTs, 'last update ') : null])]),
      h('span', { class: 'sright' }, right)]));
  }
function runningView() {
  const live = S.runs.filter(r => r.runner && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state));
  const agents = live.reduce((n, r) => n + (r.agents ? r.agents.running : 0), 0);
  const working = live.filter(r => r.runner.state === 'running').length;
  const rows = live.sort((a, b) => (a.repo + a.id).localeCompare(b.repo + b.id)).map(r => {
    const last = r.updates && r.updates[0]; const nq = openQs(r).length;
    const right = [activityChip(r) || runChip(r.runner)];
    if (r.runner.account) right.push(h('span', { class: 'rmeta mono', title: 'Account this run uses' }, [r.runner.account]));
    if (r.agents && r.agents.running) right.push(h('span', { class: 'chip fixing' }, [h('span', { class: 'dot info live' }), r.agents.running + ' agent' + (r.agents.running === 1 ? '' : 's')]));
    if (r.session && r.session.context && r.runner.state === 'running') right.push(h('span', { class: 'rmeta mono', title: 'Context in use' }, [kfmt(r.session.context) + ' ctx']));
    if (nq) right.push(h('span', { class: 'qb' }, ['? ' + nq]));
    if (laneChip(r)) right.push(laneChip(r));
    if (r.blocked) right.push(chip(r.blocked + ' blocked', 'blocked'));
    return h('a', { class: 'srow', href: hrefItem(r.repo, r.id), 'data-k': 'run:' + r.repo + '/' + r.id, 'data-rank': String(nq ? 0 : ['queued', 'paused', 'parked'].includes(r.runner.state) ? 2 : 1) }, [h('span', { class: 'sid mono' }, [r.id]),
      h('span', { class: 'sname' }, [h('span', {}, [r.title]), h('span', { class: 'small mut rsub' }, [rlabel(r.repo) + ' · ', (stageText(r) || 'starting'), last ? ' · ' : '', last ? when(last.ts, 'last update ') : null]), nowLine(r)]),
      h('span', { class: 'sright' }, right)]);
  });
  (S.plans || []).forEach(p => rows.push(planRow(p)));
  // Group by what needs the owner: waiting for you, working, queued. The rank is on each row; headers carry the counts.
  const RK = ['Waiting for you', 'Working', 'Queued']; const rk = (n) => +n.getAttribute('data-rank');
  rows.sort((a, b) => rk(a) - rk(b));
  const grouped = []; let lastRk = -1;
  rows.forEach(n => { const k = rk(n); if (k !== lastRk) { lastRk = k; grouped.push(h('div', { class: 'sgh', 'data-k': 'sgh:' + k }, [h('b', {}, [RK[k]]), String(rows.filter(x => rk(x) === k).length)])); } grouped.push(n); });
  const nAll = live.length + (S.plans || []).length;
  const head = nAll ? nAll + ' run' + (nAll === 1 ? '' : 's') + ' · ' + (working + (S.plans || []).filter(p => p.state === 'running').length) + ' working' + (agents ? ' · ' + agents + ' sub-agent' + (agents === 1 ? '' : 's') : '') : 'Nothing is running';
  return h('section', { class: 'sec', 'data-k': 'sec:live' }, [h('div', { class: 'sechd' }, [h('h2', {}, ['Running now']), h('span', { class: 'small mut', title: S.slots ? S.slots.reason : '' }, [head + (S.slots ? ' · ' + S.slots.cap + ' slots' : '')])]),
    grouped.length ? h('div', { class: 'srows' }, grouped) : null]);
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
  const live = r.runner ? ['running', 'testing', 'waiting'].includes(r.runner.state) : (r.session && r.session.state === 'active');
  const a = r.session && r.session.activity; if (!live || !a) return null;
  const since = (ts) => ts && Date.parse(ts) ? ' · for ' + dur(Date.now() - Date.parse(ts)) : '';
  let text = '', tone = 'neutral';
  if (a.doing) text = 'Now: ' + a.doing + since(a.doingSince);
  else if (a.background.length) { const first = a.background.slice().sort((x, y) => (x.since || '').localeCompare(y.since || ''))[0]; text = 'Waiting on: ' + a.background.map(b => b.label).join(', ') + since(first.since); }
  else if (r.runner && r.runner.state === 'waiting') text = '';
  else text = 'Thinking';
  const quietMs = a.lastTs && Date.parse(a.lastTs) ? Date.now() - Date.parse(a.lastTs) : 0;
  const quiet = r.runner && (r.runner.state === 'running') && quietMs >= 20 * 60000 ? 'quiet for ' + dur(quietMs) : '';
  if (quiet && !a.doing && !a.background.length) tone = 'warn';
  if (!text && !quiet) return null;
  return { text, quiet, tone, title: a.lastTs ? 'Last transcript activity ' + new Date(a.lastTs).toLocaleString() : '' };
}
function nowLine(r) {
  const n = nowDoing(r); if (!n) return null;
  return h('div', { class: 'nowline small', title: n.title }, [n.text ? h('span', { class: 'nowtext' }, [h('span', { class: 'dot info live' }), n.text]) : null,
    n.quiet ? chip(n.quiet + '', n.tone) : null]);
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
const isLive = (r) => !!r && ((r.runner && (r.runner.state === 'running' || r.runner.state === 'testing' || r.runner.state === 'waiting')) || (r.session && r.session.state === 'active'));
function subItem(repo, id, noPr, done, ttl) {
  const r = S.runs.find(x => x.repo === repo && x.id === id); const nq = r ? openQs(r) : [];
  const title = (r && r.title) || ttl || '';
  const st = done ? 'done' : r ? stageText(r) : '';
  const kids = [h('span', { class: 'sid mono', style: 'font-weight:700' }, [id]), h('span', { class: 'sname', title: id + (title ? ' · ' + title : '') }, [title])];
  const ac = done ? null : activityChip(r);
  if (ac) kids.push(ac);
  else {
    if (st) kids.push(chip(st, done ? 'done' : 'neutral'));
    if (!done && isLive(r)) kids.push(h('span', { class: 'dot info live', title: 'running' }));
  }
  if (noPr) kids.push(chip('no PR yet', 'neutral'));
  if (laneChip(r)) kids.push(laneChip(r));
  if (nq.length) kids.push(h('span', { class: 'qb', title: nq.length + ' open question(s)' }, ['? ' + nq.length]));
  return h('li', { 'data-k': 'sub:' + repo + '/' + id }, [h('a', { class: 'sub-item' + (done ? ' done' : ''), href: hrefItem(repo, id) }, kids)]);
}
/* One dossier on a stub card: id, title, and where it stands, one clean row that scales to many. */
/* Merge: all of the dossier's open PRs into its stub as one GitHub stack (gh stack merge, no agent), then the
   cleanup run. Merge shows what it would do; Confirm does it. Only for work finished and waiting on a stub. */
/* The merge queue's steps while it works on a stack. Nothing here needs the owner; a PR that does reads "waiting for your merge". */
const MERGE_STEP = { waiting: 'merge waits for its run', fixing: 'merge queue · preparing the merge', rerunning: 'merge queue · re-running failed checks', resolving: 'merge queue · the agent is fixing it', merging: 'merging', cleanup: 'merged · cleaning up' };
function mergeCtl(r) {
  if (!r) return null;
  const k = r.repo + '/' + r.id + ':merge'; const st = S.ctl[k] || {}; const m = r.merge;
  // The merge queue: first in, first out; conflicts go to the dossier's agent, the script merges, then the cleanup runs.
  const QLABEL = { queued: m && m.position ? 'in the merge queue · #' + m.position : 'in the merge queue', ...MERGE_STEP, merging: 'merging…' };
  if (m && QLABEL[m.state]) return h('span', { class: 'mergectl' }, [h('span', { class: 'chip fixing', title: (m.note || '') + (m.prs && m.prs.length ? ' · ' + m.prs.map(n => '#' + n).join(', ') : '') }, [h('span', { class: 'dot info live' }), QLABEL[m.state]]),
    m.state === 'queued' && m.position > 1 ? h('button', { class: 'btn sm', type: 'button', 'data-act': 'merge-unqueue', 'data-repo': r.repo, 'data-id': r.id }, ['Leave queue']) : null]);
  const live = r.runner && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state);
  if (live || !(r.status === 'done' || r.status === 'pr')) return null;
  const kids = [];
  if (m && m.state === 'failed' && !st.plan) kids.push(h('span', { class: 'chip blocked', title: m.error }, ['merge failed']));
  if (S.pend[k]) return null;
  if (st.plan && st.ok) kids.push(h('button', { class: 'btn sm primary', type: 'button', 'data-act': 'merge-go', 'data-repo': r.repo, 'data-id': r.id, disabled: st.busy ? true : null, title: 'Merges with a merge commit, all or nothing' }, [st.busy ? 'Queueing…' : 'Confirm: queue to ' + st.plan.replace(/^would /, '')]),
    h('button', { class: 'btn sm', type: 'button', 'data-act': 'merge-cancel', 'data-repo': r.repo, 'data-id': r.id }, ['Cancel']));
  else if (st.plan) kids.push(h('span', { class: 'chip blocked', title: st.plan }, ['cannot merge: ' + st.plan.replace(/^refused — /, '').slice(0, 90)]));
  else kids.push(h('button', { class: 'btn sm', type: 'button', 'data-act': 'merge-plan', 'data-repo': r.repo, 'data-id': r.id, disabled: st.busy ? true : null, title: 'Merge all of this dossier\'s PRs into its stub as one stack, then clean up' }, [st.busy ? 'Checking…' : 'Merge']));
  if (st.err) kids.push(h('span', { class: 'err' }, [st.err]));
  return h('span', { class: 'mergectl' }, kids);
}
async function mergeAct(btn, act) {
  const repo = btn.dataset.repo, id = btn.dataset.id, k = repo + '/' + id + ':merge'; const st = S.ctl[k] = S.ctl[k] || {};
  if (act === 'merge-cancel') { S.ctl[k] = {}; render(); return; }
  if (st.busy) return; st.busy = true; st.err = ''; if (act === 'merge-go') pressed(repo, id, 'merge'); render();
  try {
    const url = '/api/runs/' + enc(repo) + '/' + enc(id) + '/merge';
    const r = await fetch(url, act === 'merge-go' || act === 'merge-unqueue' ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: act === 'merge-unqueue' ? '{"cancel":true}' : '{}' } : {});
    const j = await r.json().catch(() => ({}));
    if (act === 'merge-plan') { if (r.ok) { st.plan = j.text || ''; st.ok = !!j.ok; } else st.err = j.error || ('HTTP ' + r.status); }
    else if (r.ok && j.ok) S.ctl[k] = {}; else st.err = j.error || ('HTTP ' + r.status);
  } catch (e) { st.err = 'request failed'; }
  if (S.ctl[k]) S.ctl[k].busy = false; await load(['runs']); if (act === 'merge-go') { settle(repo, id, 'merge'); render(); }
}
function stubRow(repo, id, o) {
  const r = S.runs.find(x => x.repo === repo && x.id === id); const nq = r ? openQs(r) : [];
  const title = (r && r.title) || o.title || '';
  const right = [];
  if (o.merged) right.push(chip('merged', 'done'));
  else if (o.working) {
    right.push(activityChip(r, true));
  } else if (r) {
    const rn = r.runner;
    if (r.merge && r.merge.state === 'queued') right.push(chip('in the merge queue' + (r.merge.position ? ' · #' + r.merge.position : ''), 'pending'));
    else if (rn && ['stopped', 'failed'].includes(rn.state)) right.push(chip((rn.command || 'run') + ' ' + rn.state, 'blocked'));
    // Open deferred items keep eager merge from queueing it (auto_work.held_by_deferred): say that, not "waiting for your merge".
    else if ((r.status === 'done' || r.status === 'pr') && !o.noPr && r.deferredOpen > 0) right.push(chip('PR ready · held by ' + r.deferredOpen + ' open deferred · fix or rule them', 'pending'));
    else if (r.status === 'done' || r.status === 'pr') right.push(chip(o.noPr ? 'finished · no PR' : 'PR ready · waiting for your merge', 'done'));
    else { const st = stageText(r); right.push(chip(st ? st + ' · not running' : 'not started', 'neutral')); }
    if ((r.status === 'done' || r.status === 'pr') && !o.noPr) { const mc = mergeCtl(r); if (mc) right.push(mc); }
  }
  if (o.noPr && !(r && !o.working && (r.status === 'done' || r.status === 'pr'))) right.push(chip('no PR yet', 'neutral'));
  if (r && r.deferredUndecided) right.push(h('span', { class: 'chip', title: r.deferredUndecided + ' deferred item(s) waiting on your decision' }, [r.deferredUndecided + ' deferred']));
  if (nq.length) right.push(h('span', { class: 'qb', title: nq.length + ' open question(s)' }, ['? ' + nq.length]));
  if (laneChip(r)) right.push(laneChip(r));
  return h('a', { class: 'srow' + (o.merged ? ' done' : ''), href: hrefItem(repo, id), 'data-k': 'srow:' + repo + '/' + id, title: id + (title ? ' · ' + title : '') },
    [h('span', { class: 'sid mono' }, [id]), h('span', { class: 'sname' }, [title || '—']), h('span', { class: 'sright' }, right)]);
}
/* A numbered /plan run on its stub card: the id it claimed, its title, planning. No link: the dossier page exists once the plan writes the file. */
function planStubRow(p) {
  const right = [activityOf({ command: 'plan', state: p.state }, '', '')];
  return h('div', { class: 'srow', 'data-k': 'srow:' + p.repo + '/' + p.claimed, title: p.claimed + ' · planned by ' + p.item + (p.title ? ' · ' + p.title : '') },
    [h('span', { class: 'sid mono' }, [p.claimed]), h('span', { class: 'sname' }, ['Planning: ' + (p.title || p.item)]), h('span', { class: 'sright' }, right)]);
}
function stubGroup(key, label, rows, fold) {
  if (!rows.length) return null;
  const body = h('div', { class: 'srows' }, rows);
  const head = label + ' · ' + rows.length;
  if (fold) return h('details', { class: 'sgrp', 'data-k': key, open: rows.length <= 6 ? true : null }, [h('summary', { class: 'sgrp-h' }, [head]), body]);
  return h('div', { class: 'sgrp', 'data-k': key }, [h('div', { class: 'sgrp-h' }, [head]), body]);
}
/* Questions the integration cannot go on without: a queued dossier's run waiting on the owner holds the queue up. */
function holdsChip(repo, ids) {
  const n = S.q.filter(q => q.state === 'open' && qRepo(q) === repo && ids.includes(q.dossier)).length;
  return n ? h('a', { class: 'chip warn', href: '/questions', title: 'The integration may be waiting for these answers' }, ['? ' + n + (n === 1 ? ' question' : ' questions') + ' waiting for you']) : null;
}
/* Integrate: merge every finished dossier on the stub through the merge queue (first in, first out), then run /stub. */
function integrateBtn(repo, branch) {
  const k = repo + '/' + branch + ':integrate'; const st = S.ctl[k] || {};
  if (st.confirm) return h('span', { class: 'mergectl' }, [h('button', { class: 'btn sm primary', type: 'button', 'data-act': 'stub-ctl', 'data-op': 'integrate', 'data-repo': repo, 'data-branch': branch, title: 'First opens the stub\'s PR into development, which closes the stub: new work goes to the next one. Then merges the finished dossiers on it one by one (conflicts go to their agent), then runs /stub: the full stack run and the audit. Dossiers still in flight move to the next stub.' }, [st.busy ? 'Starting…' : 'Confirm: integrate']),
    h('button', { class: 'btn sm', type: 'button', 'data-act': 'stub-cancel', 'data-key': k }, ['Cancel'])]);
  return h('button', { class: 'btn sm', type: 'button', 'data-act': 'stub-confirm', 'data-key': k, title: 'Close the stub (its PR into development), merge the finished dossiers, then run /stub' }, ['Integrate']);
}
async function stubCtl(btn) {
  const repo = btn.dataset.repo, branch = btn.dataset.branch, op = btn.dataset.op, k = repo + '/' + branch + ':' + (op === 'integrate' ? 'integrate' : 'run');
  const st = S.ctl[k] = S.ctl[k] || {}; if (st.busy) return; st.busy = true; st.err = ''; if (op === 'resume') S.pend[k + ':resume'] = true; render();
  try {
    const r = await fetch('/api/stub/control', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ repo, branch, action: op }) });
    const j = await r.json().catch(() => ({})); if (!(r.ok && j.ok !== false)) st.err = j.error || j.output || ('HTTP ' + r.status);
  } catch (e) { st.err = 'request failed'; }
  S.ctl[k] = st.err ? { err: st.err } : {}; await load(['stubs', 'runs']); if (R.kind === 'stub') await loadDetail(); delete S.pend[k + ':resume']; render();
}
function stubTree(repo, s) {
  const cs = [stubChip(s)]; if (s.stage) cs.push(chip(s.stage, 'neutral')); if (s.pr) cs.push(prChip(s.pr));
  // Two groups only: Working (a live run of any kind: building, planning, fixing deferred items, resolving a merge,
  // cleaning up, or queued for a slot) and Not working (nothing runs: a PR waiting for your merge, stopped, failed, or
  // not started). Merged dossiers fold away below.
  const runOf = (id) => S.runs.find(x => x.repo === repo && x.id === id);
  // A dossier with a runner works when its run is live (or the merge queue is busy with it); only one without a runner
  // falls back to recent session activity (a build run in a terminal).
  const working = (id) => { const r = runOf(id); if (!r) return false;
    if (r.merge && MERGE_STEP[r.merge.state]) return true;
    if (r.runner) return ['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state);
    return !!(r.session && r.session.state === 'active'); };
  const mem = s.members || [];
  // A /plan run that already numbered its dossier (a seed plan's claimed id) is new work for the current stub, before its
  // dossier file exists anywhere the stub list reads: it is a Working row here, not only a row of the Planning section.
  const memIds = new Set(mem.map(m => m.id)), os0 = openStubs(repo)[0];
  const planning = os0 && os0.branch === s.branch ? (S.plans || []).filter(p => p.repo === repo && p.claimed && !memIds.has(p.claimed)).map(planStubRow) : [];
  const busy = planning.concat(mem.filter(m => working(m.id)).map(m => stubRow(repo, m.id, { noPr: m.noPr, working: true })));
  const idle = mem.filter(m => !working(m.id)).map(m => stubRow(repo, m.id, { noPr: m.noPr }));
  const merged = s.dossiers.map(d => stubRow(repo, d.id, { merged: true, title: d.title }));
  const k = 'sg:' + repo + '/' + s.branch + ':';
  const groups = [stubGroup(k + 'busy', 'Working', busy), stubGroup(k + 'idle', 'Not working', idle), stubGroup(k + 'merged', 'Merged into the stub', merged, true)].filter(Boolean);
  // The whole card opens the stub's page (its rows stay links to their dossiers); an open stub or a failed integration offers Integrate.
  const integ = s.canIntegrate ? integrateBtn(repo, s.branch) : null;
  if (s.state === 'integrating') { const hold = holdsChip(repo, (s.members || []).map(m => m.id)); if (hold) cs.push(hold); }
  return h('div', { class: 'scard clickable', 'data-href': hrefStub(repo, s.branch), title: 'Open ' + s.branch, 'data-k': 'ostub:' + repo + '/' + s.branch }, [
    h('div', { class: 'shead' }, [h('a', { class: 'title mono', href: hrefStub(repo, s.branch) }, [s.branch]), h('span', { class: 'meta' }, ['cut ' + s.cut]), h('span', { class: 'chips' }, cs), h('span', { class: 'grow' }), integ]),
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
  // A dossier's open PR that targets no open stub (from before stubs existed) would otherwise be shown nowhere.
  const outs = rr && rr.outside ? rr.outside : [];
  if (outs.length) kids.push(h('div', { class: 'scol-sec', 'data-k': 'outside:' + repo }, [h('h3', { title: 'Open PRs of a dossier whose base is not an open stub (development, a release, or stacked on one). Nothing merges them into a stub.' }, ['Outside any stub']),
    h('ul', { class: 'tree' }, outs.map(o => h('li', {}, [h('a', { class: 'sub-item', href: hrefItem(repo, o.id) }, [h('span', { class: 'sid mono' }, [o.id]), activityChip(findRun(repo, o.id))]),
      h('div', { class: 'small' }, o.prs.map(p => h('span', { style: 'margin-right:8px' }, [h('a', { href: p.url, target: '_blank', rel: 'noopener' }, ['#' + p.number]), ' → ' + p.base + ' ', p.mergeable === 'CONFLICTING' ? chip('⚠ CONFLICTING', 'blocked') : null])))])))]));
  // Dossiers on an open stub card show their deferred count there; this lists the rest (their stub merged).
  const onCard = {}; os.forEach(s => { (s.members || []).forEach(m => { onCard[m.id] = 1; }); s.dossiers.forEach(d => { onCard[d.id] = 1; }); });
  (rr && rr.outside ? rr.outside : []).forEach(o => { onCard[o.id] = 1; });
  const dfd = S.runs.filter(r => r.repo === repo && r.deferredUndecided > 0 && !onCard[r.id]).sort((a, b) => b.deferredUndecided - a.deferredUndecided);
  if (dfd.length) kids.push(h('div', { class: 'scol-sec', 'data-k': 'dfd:' + repo }, [h('h3', {}, ['Deferred on merged work · ' + dfd.reduce((n, r) => n + r.deferredUndecided, 0) + ' open']),
    h('ul', { class: 'tree' }, dfd.map(r => h('li', {}, [h('a', { class: 'sub-item', href: hrefItem(repo, r.id) }, [h('span', { class: 'sid mono' }, [r.id]), h('span', { class: 'sname', title: r.title }, [r.title]), activityChip(r) || chip(r.deferredUndecided + ' open', 'ready')])])))]));
  if (merged.length) kids.push(h('details', { class: 'mstubs', 'data-k': 'mstubs:' + repo }, [h('summary', {}, ['Merged stubs (' + merged.length + ')']),
    h('ul', { class: 'tree' }, merged.map(s => h('li', {}, [h('a', { class: 'sub-item', href: hrefStub(repo, s.branch) }, [h('span', { class: 'sid mono' }, [s.branch]), h('span', { class: 'sname', title: s.branch }, [s.dossiers.length + ' dossier(s)'])])])))]));
  return h('section', { class: 'scol full', 'data-k': 'sec:stubs' }, kids);
}
/* Non-code PRs into development: plans and research documents, which may skip the stub. Each row says whether it can
   merge now and why not; Merge all merges the ready ones by script, oldest first. */
function docPrsView(repo) {
  const st = S.docPrs[repo];
  if (!st) { S.docPrs[repo] = { loading: true }; fetch('/api/repo/' + enc(repo) + '/doc-prs').then(r => r.json()).then(j => { S.docPrs[repo] = j; render(); }).catch(() => { S.docPrs[repo] = { error: 'request failed' }; render(); }); return null; }
  if (st.loading) return null;
  const prs = st.prs || []; if (!prs.length && !st.merging && !st.error) return null;
  const ready = prs.filter(p => p.ready).length; const queueable = prs.filter(p => (!p.draft || p.draftReady) && !(p.queue && ['queued', 'waiting', 'fixing', 'batching', 'rerunning', 'resolving', 'merging'].includes(p.queue.state))).length;
  const rows = prs.map(p => h('a', { class: 'srow', href: p.url, target: '_blank', rel: 'noopener', 'data-k': 'dp:' + p.number }, [h('span', { class: 'sid mono' }, ['#' + p.number]),
    h('span', { class: 'sname' }, [h('span', {}, [(p.dossier ? p.dossier + ' · ' : '') + p.title]), h('span', { class: 'small mut rsub' }, [p.kind + ' · ' + p.head])]),
    h('span', { class: 'sright' }, [docPrState(p)])]));
  const head = [h('h2', {}, ['Non-code PRs into development']), h('span', { class: 'small mut' }, [prs.length + ' open · ' + ready + ' ready']), h('span', { class: 'grow' }),
    h('button', { class: 'btn sm', type: 'button', 'data-act': 'docprs-refresh', 'data-repo': repo }, ['↻']),
    h('button', { class: 'btn sm primary', type: 'button', 'data-act': 'docprs-merge', 'data-repo': repo, disabled: st.merging || !queueable ? true : null, title: 'Puts every open plan and docs PR in the merge queue, oldest first. The queue fixes what blocks each one (a taken dossier number renumbered, red checks re-run, then the dossier\'s agent for a conflict or a check still red) and merges it. It never touches a stub.' }, [st.merging ? 'Queueing…' : 'Merge all (' + queueable + ')'])];
  return h('section', { class: 'sec', 'data-k': 'sec:docprs' }, [h('div', { class: 'sechd' }, head),
    st.error ? h('div', { class: 'err' }, [st.error]) : null, rows.length ? h('div', { class: 'srows' }, rows) : null,
    st.log ? h('details', { 'data-k': 'dp:log' }, [h('summary', { class: 'small mut' }, ['Last merge run']), h('pre', { class: 'small' }, [st.log])]) : null]);
}
/* A non-code PR's state: its merge-queue step when queued, else ready, or what the queue will have to fix. */
function docPrState(p) {
  const q = p.queue;
  const QL = { queued: 'in the merge queue' + (q && q.position ? ' · #' + q.position : ''), waiting: 'waits for its run', fixing: q && /^updating/.test(q.note || '') ? 'updating · then its checks' : 'renumbering', batching: 'in batch' + (q && q.batch ? ' #' + q.batch : '') + ' · waiting for its checks', rerunning: 're-running red checks', resolving: 'its agent is resolving', merging: 'merging…', merged: 'merged' };
  if (q && QL[q.state]) return h('span', { title: q.note || '' }, [h('span', { class: 'chip ' + (q.state === 'merged' ? 'done' : 'fixing') }, [q.state === 'merged' ? null : h('span', { class: 'dot info live' }), QL[q.state]])]);
  if (q && q.state === 'failed') return h('span', { title: q.error || '' }, [chip('could not merge: ' + (q.error || '').slice(0, 60), 'blocked')]);
  if (p.ready) return h('span', { title: p.note || '' }, [chip(p.draftReady ? 'ready to merge · draft marked ready first' : 'ready to merge', 'done')]);
  return h('span', { title: p.blocked + ' — Merge all lets the queue fix this' }, [chip(p.blocked.replace(/^dossier id taken: (\S+) .*; will renumber.*$/, '$1 taken · will renumber').slice(0, 60), !p.docsOnly || p.draft ? 'blocked' : 'neutral')]);
}
async function docPrsAct(btn, act) {
  const repo = btn.dataset.repo;
  if (act === 'docprs-merge') { S.docPrs[repo] = Object.assign({}, S.docPrs[repo], { merging: true }); render(); await fetch('/api/repo/' + enc(repo) + '/doc-prs/merge', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }).catch(() => {}); }
  const j = await fetch('/api/repo/' + enc(repo) + '/doc-prs?fresh=1').then(r => r.json()).catch(() => ({ error: 'request failed' }));
  S.docPrs[repo] = j; render();
  if (j.merging) setTimeout(() => docPrsAct({ dataset: { repo } }, 'docprs-refresh'), 5000);
}
/* Automatic work: one switch per repository (off by default); scripts/auto_work.py decides the cycle. The switch also turns the
   scripted stub merge on: the cycle ends with it, so the two are one choice. Only the controls and a pause reason show here: the
   stub cards already show what the cycle builds and lands. */
function autoWorkView(repo) {
  const v = S.auto[repo]; if (!v) return null;
  const st = v.state, busy = S.ctl['aw:' + repo] && S.ctl['aw:' + repo].busy;
  const idle = !st || st.step === 'idle';
  const head = [h('h2', {}, ['Automatic work']), h('span', { class: 'small mut' }, [v.on ? 'on' : 'off']), h('span', { class: 'grow' }),
    v.on && idle && !(st && st.request) ? h('button', { class: 'btn sm', type: 'button', 'data-act': 'aw-act', 'data-aw': 'start-now', 'data-repo': repo, disabled: busy ? true : null, title: 'Start a cycle now instead of waiting for the next stub to merge.' }, ['Start cycle now']) : null,
    st && st.paused ? h('button', { class: 'btn sm primary', type: 'button', 'data-act': 'aw-act', 'data-aw': 'resume', 'data-repo': repo, disabled: busy ? true : null, title: 'Go on: a failed run is started again, a failed integration runs again.' }, ['Resume']) : null,
    h('button', { class: 'btn sm' + (v.on ? '' : ' primary'), type: 'button', 'data-act': 'aw-act', 'data-aw': v.on ? 'disable' : 'enable', 'data-repo': repo, disabled: busy ? true : null,
      title: v.on ? 'Turn it off. Nothing new starts; runs that are live go on.' : 'Turn it on. From the next stub merge on, the dossiers that are ready (and the planned ones, planned first) are built, their deferred items fixed, the non-code PRs merged, the stub integrated and merged, with no click. Every owner question still waits for you. It also turns the scripted stub merge on.' }, [v.on ? 'Turn off' : 'Turn on'])];
  const kids = [h('div', { class: 'sechd' }, head)];
  if (S.ctl['aw:' + repo] && S.ctl['aw:' + repo].msg) kids.push(h('div', { class: 'err' }, [S.ctl['aw:' + repo].msg]));
  if (v.on && st && st.paused) kids.push(h('div', { class: 'err' }, [st.paused.reason]));
  return h('section', { class: 'sec', 'data-k': 'sec:autowork' }, kids);
}
async function autoWorkAct(btn) {
  const repo = btn.dataset.repo, key = 'aw:' + repo; S.ctl[key] = { busy: true }; render();
  const r = await fetch('/api/autowork', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ repo, action: btn.dataset.aw }) }).then(x => x.json()).catch(() => ({ error: 'request failed' }));
  S.ctl[key] = (r && (r.error || r.ok === false)) ? { msg: r.error || r.output || 'failed' } : {};
  const d = await fetchJ('/api/autowork'); if (d && d.repos) { S.auto = {}; d.repos.forEach(v => { S.auto[v.repo] = v; }); }
  render();
}
/* Delivery flows: per repository two switches, stub and kanban (never both off). Written by the dashboard to ~/.agentic/delivery-flows.json. */
function flowsView(repo) {
  const v = S.flows[repo]; if (!v) return null;
  const busy = S.ctl['fl:' + repo] && S.ctl['fl:' + repo].busy;
  const btn = (flow, label, title) => h('button', { class: 'btn sm' + (v[flow] ? '' : ' primary'), type: 'button', 'data-act': 'fl-act', 'data-flow': flow, 'data-on': v[flow] ? '0' : '1', 'data-repo': repo, disabled: busy ? true : null, title }, [label + ': ' + (v[flow] ? 'on, turn off' : 'off, turn on')]);
  const kids = [h('div', { class: 'sechd' }, [h('h2', {}, ['Delivery flows']), h('span', { class: 'small mut' }, [v.stub && v.kanban ? 'stub and kanban' : v.kanban ? 'kanban only' : 'stub only']), h('span', { class: 'grow' }),
    btn('stub', 'Stub flow', 'The weekly stub with the full local-stack proof. At least one flow stays on.'),
    btn('kanban', 'Kanban flow', 'Small fixes proven by CI only, no stub. With both on, /plan decides per dossier. At least one flow stays on.')])];
  if (S.ctl['fl:' + repo] && S.ctl['fl:' + repo].msg) kids.push(h('div', { class: 'err' }, [S.ctl['fl:' + repo].msg]));
  return h('section', { class: 'sec', 'data-k': 'sec:flows' }, kids);
}
async function flowsAct(btn) {
  const repo = btn.dataset.repo, key = 'fl:' + repo; S.ctl[key] = { busy: true }; render();
  const r = await fetch('/api/delivery-flows', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ repo, flow: btn.dataset.flow, on: btn.dataset.on === '1' }) }).then(x => x.json()).catch(() => ({ error: 'request failed' }));
  S.ctl[key] = (r && (r.error || r.ok === false)) ? { msg: r.error || 'failed' } : {};
  const d = await fetchJ('/api/delivery-flows'); if (d && d.repos) { S.flows = {}; d.repos.forEach(v => { S.flows[v.repo] = v; }); }
  render();
}
/* Pattern sweep: when /pattern-sweep last ran for this repo (an alarm when never run or older than 30 days). */
function sweepView(repo) {
  const v = S.sweep[repo]; if (!v) return null;
  const txt = v.lastRun === null ? 'Pattern sweep: never run' : 'Pattern sweep: last run ' + (v.days === 0 ? 'today' : v.days + (v.days === 1 ? ' day ago' : ' days ago')) + (v.items === null ? '' : ' (' + v.items + (v.items === 1 ? ' item)' : ' items)'));
  return h('section', { class: 'sec', 'data-k': 'sec:sweep' }, [h('div', { class: 'sechd' }, [h('h2', {}, ['Pattern sweep']), h('span', { class: 'small ' + (v.stale ? 'err' : 'mut'), title: v.summary || 'Run /pattern-sweep in an interactive session of this repo.' }, [txt])])]);
}
function repoView(repo) {
  const rs = S.runs.filter(r => r.repo === repo); const prog = rs.filter(inProgress); const os = openStubs(repo);
  const ow = (S.stubs.openWork || []).find(o => o.repo === repo); const oq = S.q.filter(q => q.state === 'open' && qRepo(q) === repo).sort((a, b) => a.ts.localeCompare(b.ts));
  const kids = [h('h1', {}, [rlabel(repo)]), h('p', { class: 'lead' }, [prog.length + ' running · ' + ((S.plans || []).filter(p => p.repo === repo).length ? (S.plans || []).filter(p => p.repo === repo).length + ' planning · ' : '') + oq.length + ' open question' + (oq.length === 1 ? '' : 's') + ' · ' + os.length + ' open stub' + (os.length === 1 ? '' : 's')])];
  if (oq.length) kids.push(section('Waiting on you', oq.length, h('div', {}, oq.map(q => qCard(q, { thread: true }))), 'oq'));
  const frame = ow ? h('iframe', { class: 'report', 'data-repo': repo, src: '/open-work/' + enc(repo) + '?theme=dark&v=' + enc(ow.updated), title: 'Open work', scrolling: 'no', style: S.frameH[repo] ? 'height:' + S.frameH[repo] + 'px' : null }) : h('div', { class: 'empty' }, ['The open-work report is not generated yet.']);
  // A /plan run writing a new dossier has no dossier file yet, so neither the stubs nor Open work list it: show it here.
  // One that already numbered its dossier sits on the current stub's card instead (planStubRow), when there is an open stub.
  const pl = (S.plans || []).filter(p => p.repo === repo && !(p.claimed && os.length));
  if (pl.length) kids.push(section('Planning', pl.length, h('div', { class: 'srows' }, pl.map(planRow)), 'plans'));
  // The stubs (the current one and the next) first, then Open work, each full width, one under the other.
  kids.push(sideColumn(repo));
  const aw = autoWorkView(repo); if (aw) kids.push(aw);
  const fv = flowsView(repo); if (fv) kids.push(fv);
  const dp = docPrsView(repo); if (dp) kids.push(dp);
  const rl = rulingsView(repo); if (rl) kids.push(rl);
  const sw = sweepView(repo); if (sw) kids.push(sw);
  const vm = vrRepoLink(repo); if (vm) kids.push(vm);
  kids.push(h('section', { class: 'main-col', 'data-k': 'sec:report' }, [h('h2', {}, ['Open work']), frame]));
  return h('div', {}, kids);
}
/* The dossier sits on an open stub with a PR into it (the stub card's "PR open · waiting for your merge"). */
function onOpenStub(r) { return openStubs(r.repo).some(s => (s.members || []).some(m => m.id === r.id && !m.noPr)); }
function header(r, mode, fbAnchors) {
  const nq = openQs(r); const cs = [chip(r.status)];
  const ac = activityChip(r);
  if (ac) cs.unshift(ac); else if (r.runner && r.runner.state !== 'done') cs.push(runChip(r.runner));
  const st = ac ? '' : stageText(r); if (st) cs.push(chip(st, 'neutral'));
  if (r.session && r.session.context) cs.push(h('span', { title: 'Tokens in the orchestrator\'s context right now. It compacts at 200k, so this falls back after each compaction and each new stage.' }, [chip('context ' + kfmt(r.session.context) + ' / 200k', 'neutral')]));
  if (r.pr) cs.push(prChip(r.pr)); if (r.jira && r.jira !== 'null') cs.push(chip(r.jira, 'neutral'));
  nq.forEach(q => cs.push(h('button', { class: 'chip warn chipbtn', type: 'button', 'data-act': 'pane-go', 'data-pane': 'needs', title: 'Answer it in the Needs you now strip below' }, ['? open question'])));
  const acts = [];
  if (mode === 'run') acts.push(h('a', { class: 'btn', href: hrefItem(r.repo, r.id) + '/dossier' }, ['View dossier']));
  else if (r.updates.length || inProgress(r)) acts.push(h('a', { class: 'btn', href: hrefItem(r.repo, r.id) }, ['Back to run']));
  /* Sticky: one row of title and controls, one of chips and the live line. The long title and summary are cut to one line (the full text on hover). */
  return h('div', { class: 'panel dhead', 'data-k': 'dhead' }, [
    h('div', { class: 'drow' }, [h('h1', { class: 'dtitle', title: r.id + ' · ' + r.title + (r.summary ? '\n\n' + r.summary : '') }, [r.id + ' · ' + r.title]), h('div', { class: 'row dctl' }, controls(r, true).concat(onOpenStub(r) ? [mergeCtl(r)] : [], acts))]),
    fbAnchors && fbAnchors.length ? h('div', { class: 'drow' }, [fbHead(r.repo + '/' + r.id, fbAnchors)]) : null,
    h('div', { class: 'drow' }, [h('div', { class: 'chips' }, cs), nowLine(r)])]);
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
  if (runner) head.push(runChip(runner));
  kids.push(h('div', { class: 'ph1' }, head));
  if (withStepper) kids.push(stepper(n.step, d.phaseNames));
  if (n.doing) kids.push(h('div', { class: 'doing' }, [icon(n.doing.kind), h('div', {}, [h('div', { class: 'tx' }, [n.doing.text.split('\n')[0]]), h('div', { class: 'small mut' }, [when(n.doing.ts)])])]));
  else kids.push(h('div', { class: 'small mut' }, ['Nothing posted yet.']));
  if (!waiting.length) kids.push(h('div', { class: 'nowait' }, ['Nothing is waiting on you.']));
  return h('div', { class: 'now', 'data-k': 'now' }, kids);
}
function tabs(key, list) {
  const cur = S.tab[key] || list[0][0];
  return h('div', { class: 'tabs', role: 'tablist', 'data-k': 'tabs' }, list.map(t => h('button', { class: 'tab' + (cur === t[0] ? ' on' : ''), type: 'button', role: 'tab', 'aria-selected': String(cur === t[0]), 'data-act': 'tab', 'data-key': key, 'data-tab': t[0] }, [t[1]])));
}
/* ---- panes: native details, open or closed remembered per viewer (localStorage, tried and caught: no storage still works) ----
   PANES is the registry of the dossier page. An entry is function (c) returning {key, title, badge, def, body, cls} or null when it has
   nothing to show; the page lists the panes in this order, in the rail and below. A new pane (a review packet, a sequence diagram,
   a feedback bar) is one more push. */
const PANES = [];
/* ---- unified feedback control: one bar (viewed, OK / Question / Change / Comment, blocking or informing) on each pane, dossier section and
   diagram element. Drafts stay in this browser (localStorage, tried and caught) until the one "Send review" in the header writes them to
   review.json (POST /api/visual-review/feedback). A viewed tick is information only: nothing is forced. ---- */
const FB_ANCHOR = /^(summary|(doc|adr|deferred|pane|el):[A-Za-z0-9][A-Za-z0-9_.:-]*)$/;
const fbSavedOf = () => { const d = S.detail && S.detail.url === detailUrl() ? S.detail.data : null; return (d && d.feedback) || { notes: [], viewed: [] }; };
function fbState(item) { return S.fb[item] || (S.fb[item] = fbRead(paneStore(), item)); }
function fbKeep(item) { fbWrite(paneStore(), item, fbState(item)); }
const FB_KINDS = [['ok', 'OK', 'Looks right'], ['question', 'Question', 'Ask the agent'], ['change', 'Change', 'Ask for a change'], ['comment', 'Comment', 'A remark, no action asked']];
function fbBar(item, anchor, label) {
  if (!FB_ANCHOR.test(anchor)) return null;
  const saved = fbSavedOf(), st = fbState(item), n = fbNote(saved, st, anchor), v = fbIsViewed(saved, st, anchor);
  const kids = [h('label', { class: 'fbview', title: 'Mark as looked at (information only, nothing is forced)' }, [h('input', { type: 'checkbox', 'data-fb': 'viewed', 'data-item': item, 'data-anchor': anchor, checked: v, 'aria-label': 'Viewed: ' + label }), 'viewed'])];
  FB_KINDS.forEach(k => kids.push(h('button', { class: 'btn sm' + (n.verdict === k[0] ? ' on' : ''), type: 'button', 'data-act': 'fb-verdict', 'data-item': item, 'data-anchor': anchor, 'data-verdict': k[0], 'aria-pressed': String(n.verdict === k[0]), title: k[2] }, [k[1]])));
  if (n.verdict && n.verdict !== 'ok' && n.verdict !== 'comment') kids.push(h('label', { class: 'fbblk', title: 'Blocking holds the approval back until you clear it; informing only tells the agent' }, [h('input', { type: 'checkbox', 'data-fb': 'blocking', 'data-item': item, 'data-anchor': anchor, checked: n.blocking, 'aria-label': 'Blocking: ' + label }), 'blocking']));
  if (n.verdict) kids.push(chip(n.draft ? 'draft, not sent' : 'sent', n.draft ? 'warn' : 'ok'));
  if (n.verdict && n.verdict !== 'ok') kids.push(h('textarea', { 'data-fb': 'comment', 'data-item': item, 'data-anchor': anchor, maxlength: '1000', placeholder: 'Optional comment (kept here until Send review)', 'aria-label': 'Comment on ' + label }, [n.comment]));
  return h('div', { class: 'fbbar', 'data-k': 'fb:' + anchor, role: 'group', 'aria-label': 'Feedback on ' + label }, kids);
}
/* The header part: "N of M viewed" over the panes and sections that carry a bar, and the one Send review (all drafts at once). */
function fbHead(item, anchors) {
  const saved = fbSavedOf(), st = fbState(item), c = fbCounts(anchors, saved, st), pend = fbPending(saved, st), busy = S.fbBusy === item;
  return h('div', { class: 'fbhead', 'data-k': 'fbhead' }, [h('span', { class: 'chip neutral', title: 'Panes and dossier sections you marked viewed. Information only.' }, [c.text]),
    h('button', { class: 'btn primary sm', type: 'button', 'data-act': 'fb-send', 'data-item': item, disabled: !pend || busy, title: pend ? 'Write your ' + pend + ' draft change' + (pend === 1 ? '' : 's') + ' to review.json and tell the agent what needs it' : 'Nothing drafted' }, [busy ? 'Sending…' : 'Send review' + (pend ? ' (' + pend + ')' : '')]),
    pend ? h('button', { class: 'btn sm', type: 'button', 'data-act': 'fb-discard', 'data-item': item }, ['Discard drafts']) : null, S.fbErr && S.fbErr.item === item ? h('span', { class: 'err' }, [S.fbErr.text]) : null]);
}
async function fbSend(item) {
  const m = item.split('/'), saved = fbSavedOf(), st = fbState(item), sent = fbPayload(saved, st); if (!sent || S.fbBusy) return;
  S.fbBusy = item; S.fbErr = null; render();
  try {
    const r = await fetch('/api/visual-review/feedback', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ dossier: m[1], repo: m[0], notes: sent.notes, viewed_add: sent.viewed_add, viewed_remove: sent.viewed_remove }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) S.fbErr = { item: item, text: j.error || ('HTTP ' + r.status) };
    else { fbSettle(st, sent); fbKeep(item); if (S.detail && S.detail.data) S.detail.data.feedback = { notes: j.review.notes || [], viewed: j.review.viewed || [] }; load(['questions']); }
  } catch (e) { S.fbErr = { item: item, text: 'request failed' }; }
  S.fbBusy = null; render();
}
const paneStore = () => { try { return window.localStorage; } catch (e) { return null; } };
function paneOpen(item, key, def) { const k = item + '.' + key; if (S.po[k] !== undefined) return S.po[k]; return paneRead(paneStore(), item, key, def); }
function paneSet(item, key, open) { S.po[item + '.' + key] = open; paneWrite(paneStore(), item, key, open); }
/* One pane: a details with a summary (title and state badge). Its body is built only while it is open, so a closed pane costs no DOM. */
function pane(item, p) {
  const open = paneOpen(item, p.key, p.def);
  const sum = h('summary', {}, [h('span', { class: 'ptitle' }, [p.title]), p.badge ? h('span', { class: 'chip ' + (p.badge.tone || 'neutral') }, [p.badge.text]) : null]);
  return h('details', { class: 'pane' + (p.cls ? ' ' + p.cls : ''), id: 'pane-' + p.key.replace(/[^A-Za-z0-9_-]/g, '_'), 'data-k': 'pane:' + p.key, 'data-pane': item, 'data-key': p.key, open: open }, [sum, h('div', { class: 'pbody' }, open ? [p.fb ? fbBar(item, p.fb, p.title) : null].concat(p.body()) : [])]);
}
function reviewOf(r) { const st = VRS[vrKey(r.repo, r.id)]; const v = VR[vrKey(r.repo, r.id)]; return (st && st.review) || (v && v.data && v.data.review) || null; }
PANES.push(
  c => c.d && c.d.blocks && c.d.blocks.length ? { key: 'blocked', title: 'Blocked by the safety check', badge: { text: String(c.d.blocks.length), tone: 'warn' }, def: paneDefault('blocked', c.pd), cls: 'flat', body: () => [blockedView(c.r, c.d)] } : null,
  c => { const sv = c.mode !== 'run' && c.d ? summaryView(c.r, c.d) : null; return sv ? { key: 'summary', fb: 'summary', title: 'Product summary', badge: c.d.summary ? { text: 'written', tone: 'ok' } : { text: 'not written', tone: 'warn' }, def: paneDefault('summary', c.pd), cls: 'flat', body: () => [sv] } : null; },
  c => { const vp = vrPanel(c.r); if (!vp) return null; const rs = c.rv && c.rv.summary;
    return { key: 'diagram', fb: 'pane:diagram', title: 'Change diagram', badge: rs ? { text: rs.checked + ' of ' + rs.total + (rs.outdated ? ' · ' + rs.outdated + ' outdated' : ''), tone: rs.unchecked || rs.outdated || rs.question || rs.change ? 'warn' : 'ok' } : null, def: paneDefault('diagram', c.pd), cls: 'flat', body: () => [vp] }; },
  c => { if (c.mode === 'run' || !c.d || !c.d.html) return null; const sp = splitSections(c.d.html);
    return { key: 'dossier', fbKids: sp.sections.map(s => 'doc:' + s.slug), title: 'Dossier', badge: sp.sections.length ? { text: sp.sections.length + ' section' + (sp.sections.length === 1 ? '' : 's'), tone: 'neutral' } : null, def: paneDefault('dossier', c.pd), cls: 'flat',
      body: () => [sp.intro.trim() ? mdBlock(sp.intro, 'doc:intro') : null].concat(sp.sections.map(s => pane(c.item, { key: 'sec:' + s.slug, fb: 'doc:' + s.slug, title: s.title, def: false, cls: 'sub', body: () => [mdBlock(s.html, 'doc:' + s.slug)] }))) }; },
  c => { const pk = PKT[vrKey(c.r.repo, c.r.id)]; if (!pk || !pk.files) return null; return Object.keys(pk.files).map(n => packetModel(n, pk.files[n])).filter(Boolean).map(m => ({ key: m.key, fb: FB_ANCHOR.test('pane:' + m.key) ? 'pane:' + m.key : undefined, title: m.title, badge: m.badge, def: false, cls: 'flat', body: () => packetBody(h, m, pk.root) })); },
  c => c.mode === 'run' || !c.r.answered.length ? null : { key: 'answered', fb: 'pane:answered', title: 'Answered questions', badge: { text: String(c.r.answered.length), tone: 'neutral' }, def: paneDefault('answered', c.pd), cls: 'flat',
    body: () => c.r.answered.map(a => h('details', { class: 'qrow', 'data-k': 'qa:' + a.id }, [h('summary', {}, [h('span', { class: 'qrq' }, [a.text.split('\n')[0]]), h('span', { class: 'qra mut' }, [(a.option ? a.option + ' · ' : '') + a.answer.split('\n')[0]])]),
      h('div', { class: 'panel qa' }, [h('div', {}, ['Q: ' + a.text]), h('div', { class: 'a' }, ['A: ' + (a.option ? a.option + ' · ' : '') + a.answer + ' · ', when(a.answeredAt || a.asked)]), h('a', { class: 'small', href: '/questions#q=' + enc(a.id) }, ['thread'])])])) },
  c => { const dp = deferredProgress(c.r, c.d); return dp ? { key: 'deferredprog', title: 'Fix run for the deferred issues', badge: fixLive(c.r) ? { text: 'running', tone: 'info' } : null, def: paneDefault('deferredprog', c.pd), cls: 'flat', body: () => [dp] } : null; },
  c => c.nDf ? { key: 'deferred', fb: 'pane:deferred', title: 'Deferred issues', badge: { text: c.r.deferredOpen + ' open / ' + c.nDf, tone: c.r.deferredOpen ? 'warn' : 'neutral' }, def: paneDefault('deferred', c.pd), cls: 'flat', body: () => [deferredView(c.r)] } : null,
  c => { const rv = resultView(c.r, c.d); return rv ? { key: 'result', fb: 'pane:result', title: 'Final reply', def: paneDefault('result', c.pd), cls: 'flat', body: () => [rv] } : null; },
  c => c.d && (c.mode === 'run' || c.hasTl) ? { key: 'timeline', title: c.d.planning ? 'Planning' : 'Implement dossier', badge: c.d.entries && c.d.entries.length ? { text: c.d.entries.length + ' updates', tone: 'neutral' } : null, def: paneDefault('timeline', c.pd), cls: 'flat',
    body: () => [c.mode === 'run' && (c.d.activeSection || '') === '' ? nowView(c.d, c.r.runner, true) : null, groupsView(c.d.groups || [], c.key, 'No updates yet — the agent posts here as the run progresses')] } : null,
  c => c.d ? (c.secs.filter(x => x.cmd).map(x => ({ key: 'tl:' + x.cmd, title: x.label, def: paneDefault('tl', c.pd) || (c.d.activeSection === x.cmd && c.mode === 'run'), cls: 'flat',
    body: () => [c.mode === 'run' && c.d.activeSection === x.cmd ? nowView(c.d, c.r.runner, true) : null, groupsView(x.groups, c.key + ':' + x.cmd, 'No updates yet — the agent posts here as the run progresses')] }))) : null,
  c => c.d && (c.mode === 'run' || c.hasLog) ? { key: 'log', title: 'Build log', badge: c.hasLog ? { text: String(c.d.log.length), tone: 'neutral' } : null, def: paneDefault('log', c.pd), cls: 'flat',
    body: () => [c.hasLog ? h('ol', { class: 'tl', 'data-k': 'log' }, c.d.log.slice().reverse().map((l, i) => h('li', { class: 'log', 'data-k': 'l:' + i + l.slice(0, 30) }, [h('div', { class: 'tx' }, [l])]))) : h('div', { class: 'empty' }, ['The dossier has no build log yet.'])] } : null
);
/* "Needs you now": what waits on the owner, answerable here. The open questions are the question cards (the same buttons and answer
   endpoints as /questions); the rest is a one-line row with the button that opens its pane. Always visible, never collapsible. */
function needsYou(r, d, mode) {
  const nq = openQs(r); const bs = d && d.blocks ? d.blocks.length : 0; const rv = reviewOf(r); const rows = [];
  nq.forEach(q => rows.push(qCard(q, { thread: true, strip: true })));
  if (bs) rows.push(h('div', { class: 'nrow', 'data-k': 'nr:blocked' }, [chip('blocked', 'warn'), h('span', { class: 'grow' }, [bs + ' command' + (bs === 1 ? '' : 's') + ' refused by the safety check: allow or deny']), h('button', { class: 'btn sm', type: 'button', 'data-act': 'pane-go', 'data-pane': 'blocked' }, ['Open'])]));
  if (rv && rv.summary && (rv.summary.unchecked || rv.summary.outdated || rv.summary.question || rv.summary.change) && vrPanel(r)) rows.push(h('div', { class: 'nrow', 'data-k': 'nr:review' }, [chip('review', 'info'), h('span', { class: 'grow' }, [rv.summary.text]), h('button', { class: 'btn sm', type: 'button', 'data-act': 'pane-go', 'data-pane': 'diagram' }, ['Open the diagrams'])]));
  const lm = liveMessageView(r); if (lm) rows.push(lm);
  const waiting = nq.length + (bs ? 1 : 0);
  return h('section', { class: 'panel needs-strip', 'data-k': 'needs', id: 'needs', role: 'region', 'aria-label': 'Needs you now' }, [
    h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Needs you now']), waiting ? chip(String(waiting), 'warn') : null]),
    waiting || (rv && rv.summary && rv.summary.unchecked) ? null : h('div', { class: 'nowait' }, ['Nothing is waiting on you.'])].concat(rows));
}
function itemView(repo, id) {
  const r = findRun(repo, id);
  if (!r) return h('div', {}, [h('h1', {}, [id]), h('div', { class: 'empty', role: 'status' }, [S.loaded ? 'No such dossier in ' + repo + '.' : 'Loading the dossier…'])]);
  const mode = itemMode(r); const d = S.detail && S.detail.url === detailUrl() ? S.detail.data : null;
  const key = repo + '/' + id; const item = key;
  const secs = (d && d.sections) || [];
  const rv = reviewOf(r);
  const needs = openQs(r).length > 0 || !!(d && d.blocks && d.blocks.length);
  const busy = r.runner && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state);
  const pd = { mode: mode, needs: needs, reviewing: !!vrOpenQ(id, repo) || !!(rv && rv.summary && (rv.summary.unchecked || rv.summary.outdated || rv.summary.question || rv.summary.change)), fixLive: fixLive(r), fixWaiting: fixLive(r) && openQs(r).length > 0 };
  const c = { r: r, d: d, mode: mode, key: key, item: item, secs: secs, rv: rv, pd: pd, busy: busy, nDf: r.deferred ? r.deferred.items.length : 0,
    hasTl: !!(d && d.entries && d.entries.length > 0), hasLog: !!(d && d.log && d.log.length > 0) };
  const list = []; PANES.forEach(f => { const x = f(c); if (x) (Array.isArray(x) ? x : [x]).forEach(y => list.push(y)); });
  const fbAnchors = []; list.forEach(p => { if (p.fb) fbAnchors.push(p.fb); (p.fbKids || []).forEach(a => fbAnchors.push(a)); });
  const kids = [header(r, mode, fbAnchors), needsYou(r, d, mode)];
  if (!d) kids.push(h('div', { class: 'loading', role: 'status', 'data-k': 'loading' }, [h('span', { class: 'dot info live' }), ' Loading the dossier… the header and the questions above are already current.']));
  const rail = list.length > 1 ? h('nav', { class: 'rail', 'aria-label': 'Sections', 'data-k': 'rail' }, list.map(p => h('button', { class: 'rail-i', type: 'button', 'data-act': 'pane-go', 'data-pane': p.key, 'data-k': 'ri:' + p.key }, [
    h('span', { class: 'dot ' + (p.badge && p.badge.tone === 'warn' ? 'warn' : p.badge && p.badge.tone === 'ok' ? 'ok' : p.badge && p.badge.tone === 'info' ? 'info' : ''), 'aria-hidden': 'true' }), h('span', { class: 'rail-t' }, [p.title]), p.badge ? h('span', { class: 'rail-b' }, [p.badge.text]) : null]))) : null;
  const panes = h('div', { class: 'panes', 'data-k': 'panes' }, list.map(p => pane(item, p)));
  kids.push(h('div', { class: 'dlayout' + (rail ? ' withrail' : ''), 'data-k': 'dlayout' }, [rail, panes]));
  if (d && d.rulings && d.rulings.length) kids.push(h('p', { class: 'small mut rulelink', 'data-k': 'rulelink' }, ['Standing rulings: ' + d.rulings.length + ' for ' + repo + ' · ', h('a', { href: hrefRepo(repo) + '#rulings' }, ['manage them on the repo page'])]));
  return h('div', {}, kids);
}
/* The deferred ledger: open items with a copyable /plan seed, fixed ones with their
   commit, the ones routed to a new dossier; "Fix all deferred" sits in the header. */
function dfState(r, i) {
  if (i.state === 'fixed') return r.deferred.pr ? h('a', { href: r.deferred.pr, target: '_blank', rel: 'noopener', title: i.note ? 'commit ' + i.note : '' }, [chip('fixed · stacked PR ' + prNum(r.deferred.pr), 'done')]) : chip('fixed' + (i.note ? ' · ' + i.note.slice(0, 8) : ''), 'done');
  // Ruled to its own dossier: /fix-deferred only routes it; the dossier exists once /plan <ID> D-n has run and logged
  // DEFERRED-DOSSIER, and then the chip links to it. Until then, say that, and hand over the exact command.
  if (i.state === 'dossier') {
    // Planned in an open plan PR: the dossier exists on its plan branch only until that PR merges into development.
    if (i.dossier && i.planBranch) return h('span', { title: 'Planned as ' + i.dossier + ' on ' + i.planBranch + '; its plan PR into development is not merged yet' }, [chip('planned as ' + i.dossier + ' · plan PR open', 'planned')]);
    if (i.dossier) return h('a', { href: hrefItem(r.repo, i.dossier) }, [chip('→ dossier ' + i.dossier, 'planned')]);
    const cmd = '/plan ' + r.id + ' ' + i.id;
    if (i.planning && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(i.planning)) return h('span', { title: 'A /plan run is writing this dossier' + (i.planning === 'waiting' ? '; it waits for your answer' : '') }, [chip(i.planning === 'waiting' ? 'being planned · ? waiting for you' : 'being planned', i.planning === 'waiting' ? 'warn' : 'pending')]);
    const st = S.ctl[r.repo + '/' + r.id + ':plan:' + i.id] || {}; const pnote = r.pendingNotes && r.pendingNotes['plan:' + i.id];
    return h('span', { class: 'dfplan', title: 'You ruled this item to its own dossier. No dossier is written yet' + (i.planning === 'failed' || i.planning === 'stopped' ? ' (its /plan run ' + i.planning + ')' : '') + '. Plan it starts ' + cmd + ' in the background; its questions come to you here.' }, [chip('own dossier · not planned yet', 'planned'),
      isPend(r, 'plan:' + i.id) ? null : h('button', { class: 'btn sm', type: 'button', 'data-act': 'plan-item', 'data-repo': r.repo, 'data-id': r.id, 'data-item': i.id }, ['Plan it']),
      h('button', { class: 'btn sm', type: 'button', 'data-act': 'copy', 'data-text': cmd }, ['Copy ' + cmd]), (pnote || st.err) ? h('span', { class: 'err' }, [pnote || st.err]) : null]);
  }
  if (i.state === 'deprecated') return h('span', { title: i.note || '' }, [chip(i.ownerKept ? 'dropped · your ruling' : 'dropped', 'neutral')]);
  if (i.state === 'open' && i.question && S.q.some(q => q.id === i.question && q.state === 'open')) return h('a', { href: '/questions#q=' + enc(i.question) }, [chip('? waiting for you', 'warn')]);
  if (i.state === 'fixing') return h('span', { title: i.note || '' }, [chip(fixLive(r) ? 'being fixed' : 'fix not finished', fixLive(r) ? 'pending' : 'blocked')]);
  return chip('not tackled', 'ready');
}
/* Fixing the deferred issues: the stacked PR, where each item stands (being fixed, fixed, its own dossier, dropped), and the questions it waits on; live while /fix-deferred runs, the final state once it is done. */
const fixLive = (r) => !!(r.runner && r.runner.command === 'fix-deferred' && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state));
const prNum = (u) => { const m = String(u || '').match(/(\d+)\/?$/); return m ? '#' + m[1] : 'PR'; };
function deferredProgress(r, d) {
  const df = r.deferred; if (!df || !df.items.length) return null;
  const live = fixLive(r); const ran = live || !!df.pr || !!df.branch || df.items.some(i => i.state === 'fixing');
  if (!ran) return null;
  const n = (s) => df.items.filter(i => i.state === s).length;
  const parts = [['fixed', n('fixed'), 'ok'], ['being fixed', n('fixing'), 'acc'], ['to its own dossier', n('dossier'), 'warn'], ['dropped', n('deprecated'), 'mut'], ['not tackled', df.items.filter(i => i.state === 'open').length, 'mut2']];
  const total = df.items.length; const nq = openQs(r).length;
  const head = [h('span', { class: 'kind' }, [live ? 'Fixing deferred issues' : 'Deferred issues · after the fix run']), live ? h('span', { class: 'dot info live' }) : null];
  if (live) head.push(runChip(r.runner)); else if (r.runner && r.runner.command === 'fix-deferred' && r.runner.state !== 'done') head.push(runChip(r.runner));
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
      h('div', { class: 'small mut' }, ['Always allow saves the rule for every later session of ' + r.repo + ' (~/.agentic/rulings, and its .claude/settings.local.json) and restarts the stage when the agent gets your answer, so the rule applies at once (it redoes the work since the stage\'s last checkpoint). Widen it to a prefix, e.g. Bash(ssh vm-test *), to cover similar commands.'])]);
  });
  const kids = [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Blocked by the safety check · ' + bs.length]), d.blockQuestion ? h('span', { class: 'small mut' }, ['ruling goes to ' + d.blockQuestion]) : h('span', { class: 'small mut' }, ['the agent has not asked yet'])])].concat(cards);
  if (st.msg) kids.push(h('div', { class: 'ctl-msg ' + (st.ok ? 'ok' : 'bad') }, [st.msg]));
  return h('section', { class: 'panel blocked', 'data-k': 'blocked' }, kids);
}
/* The standing "Always allow" rulings of this repo (~/.agentic/rulings): who saved each, when, for which question; Remove drops it.
   Repo-wide, so they live on the repo page; a dossier page only links here. */
function rulingsView(repo) {
  const rs = S.rulings[repo]; if (!rs || !rs.length) return null;
  const st = S.chat['rul:' + repo] = S.chat['rul:' + repo] || {};
  const rows = rs.map(x => h('div', { class: 'blk', 'data-k': 'rul:' + x.rule }, [
    h('pre', { class: 'blk-cmd' }, [x.rule]),
    h('div', { class: 'small mut' }, ['saved by ' + (x.by || 'owner') + (x.question ? ' · answering ' + x.question : '') + ' · ', when(x.ts)]),
    h('div', { class: 'ctl' }, [h('button', { class: 'btn', type: 'button', 'data-act': 'rmrule', 'data-repo': repo, 'data-rule': x.rule, disabled: st.busy ? true : null }, ['Remove'])])]));
  const kids = [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Standing rulings for ' + repo + ' · ' + rs.length]), h('span', { class: 'small mut' }, ['applied to every later session of this repo'])])].concat(rows);
  if (st.msg) kids.push(h('div', { class: 'ctl-msg ' + (st.ok ? 'ok' : 'bad') }, [st.msg]));
  return h('section', { class: 'panel rulings', id: 'rulings', 'data-k': 'rulings' }, kids);
}
/* What the dossier changed, in product terms (/product-summary, written after Phase 9), above the dossier itself. */
function summaryView(r, d) {
  const key = r.repo + '/' + r.id; const st = S.chat['sum:' + key] = S.chat['sum:' + key] || {};
  if (d.summary) return h('section', { class: 'panel psum', 'data-k': 'psum' }, [h('div', { class: 'res-hd' }, [h('span', { class: 'kind' }, ['Product summary']), when(d.summary.ts)]), mdBlock(d.summary.html, 'psum:' + d.summary.ts)]);
  const built = ['done', 'pr', 'review'].indexOf(r.status) >= 0 && !(r.runner && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state));
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
  if (!r.runner || !['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state)) return null;
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
      can ? h('div', { class: 'dfc-a' }, [h('button', { class: 'btn sm ghost', type: 'button', 'data-act': 'copy', 'data-text': '/plan ' + r.id + ' ' + i.id, title: 'Copy the /plan seed for this item' }, ['Copy /plan seed']),
        h('button', { class: 'btn sm ghost', type: 'button', 'data-act': 'df-drop', 'data-repo': r.repo, 'data-id': r.id, 'data-item': i.id, 'data-drop': '1', title: 'Drop this item: leaving it open is dropping it, there is no open past this stub. It ends dropped and is not picked up by Fix all deferred' }, ['Drop'])])
        : i.state === 'deprecated' && i.ownerKept ? h('div', { class: 'dfc-a' }, [h('button', { class: 'btn sm ghost', type: 'button', 'data-act': 'df-drop', 'data-repo': r.repo, 'data-id': r.id, 'data-item': i.id, 'data-drop': '0', title: 'Count it as open again' }, ['Reopen'])]) : null]);
  });
  const links = [];
  if (df.report) links.push(h('a', { class: 'small', href: '/deferred/' + enc(r.repo) + '/' + enc(r.id), target: '_blank', rel: 'noopener' }, ['full report']));
  if (df.pr) links.push(h('a', { class: 'small', href: df.pr, target: '_blank', rel: 'noopener' }, ['stacked PR']));
  const bar = [];
  if (pick.length) {
    const confirming = st.confirm === 'fix-sel'; const busy = st.busy === 'fix-sel';
    const why = !fix || !fix.show ? (r.runner && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(r.runner.state) ? 'the run is still going' : 'available once the build is finished') : '';
    bar.push(h('button', { class: 'btn sm', type: 'button', 'data-act': 'dsel-all', 'data-key': key }, [all ? 'Clear selection' : 'Select all ' + pick.length]));
    bar.push(h('span', { class: 'small mut' }, [n + ' of ' + pick.length + ' selected']));
    bar.push(h('span', { class: 'grow' }));
    if (why) bar.push(chip(why, 'blocked'));
    if (confirming) bar.push(h('button', { class: 'btn act', type: 'button', 'data-act': 'ctl-cancel', 'data-repo': r.repo, 'data-id': r.id }, ['Cancel']));
    if (!isPend(r, 'fix-deferred')) bar.push(h('button', { class: 'btn act' + (confirming ? ' confirm' : ' go'), type: 'button', 'data-act': 'fix-sel', 'data-repo': r.repo, 'data-id': r.id, disabled: !n || !!why || !!st.busy ? true : null },
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
  const cs = [stubChip(s), chip('cut ' + s.cut, 'neutral')]; if (s.stage) cs.push(chip(s.stage, 'neutral')); if (s.pr) cs.push(prChip(s.pr));
  const impl = s.dossiers.length ? h('div', { class: 'mini' }, s.dossiers.map(x => h('div', { class: 'card', 'data-k': 'i:' + x.id }, [
    h('a', { class: 'stretch title', href: hrefItem(x.repo || repo, x.id) }, [h('span', { class: 'id' }, [x.id + ' ']), x.title]), h('div', { class: 'chips' }, [activityChip(findRun(x.repo || repo, x.id)), chip(x.status || '?')])]))) : h('div', { class: 'empty' }, ['No dossier is implemented on this stub yet.']);
  const key = repo + '/' + branch; const tab = S.tab[key] || 'timeline'; const body = [];
  body.push(tabs(key, [['timeline', 'Timeline'], ['record', 'Record']]));
  if (!d || !d.groups) body.push(h('div', { class: 'empty' }, ['Loading…']));
  else if (tab === 'record') body.push(d.record.length ? h('ol', { class: 'tl', 'data-k': 'rec' }, d.record.slice().reverse().map(tlEntry)) : h('div', { class: 'empty' }, [d.hasRecord ? 'The record file has no sections.' : 'No record file for this stub.']));
  else { body.push(nowView(d, d.runner, !!(d.runner && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(d.runner.state)))); body.push(groupsView(d.groups, key, 'No updates yet — the agent posts here as the stub progresses')); }
  // The stub's own run, like a dossier's: Integrate (merge queue, then /stub), its state, Stop and Resume.
  const rn = d && d.runner; const ig = d && d.integrate; const ctl = [];
  if (rn && rn.state !== 'done') cs.push(runChip(rn));
  if (ig && ig.state === 'merging') { const hold = holdsChip(repo, ig.finished || []); if (hold) cs.push(hold); }
  if (d && d.land && d.land.state === 'waiting') cs.push(h('span', { title: (d.land.reasons || []).join('\n') }, [chip('auto-merge is holding it', 'pending')]));
  const liveRun = rn && ['running', 'testing', 'waiting', 'paused', 'queued'].includes(rn.state);
  if (s.canIntegrate) ctl.push(integrateBtn(repo, branch));
  if (liveRun) ctl.push(h('button', { class: 'btn', type: 'button', 'data-act': 'stub-ctl', 'data-op': 'stop', 'data-repo': repo, 'data-branch': branch }, ['Stop']));
  else if (rn && ['stopped', 'failed'].includes(rn.state) && !S.pend[repo + '/' + branch + ':run:resume'] && !(d.pending && d.pending.hidden)) ctl.push(h('button', { class: 'btn', type: 'button', 'data-act': 'stub-ctl', 'data-op': 'resume', 'data-repo': repo, 'data-branch': branch }, ['Resume /stub']));
  const err = (d && d.pending && d.pending.note) || (S.ctl[repo + '/' + branch + ':integrate'] || {}).err || (S.ctl[repo + '/' + branch + ':run'] || {}).err;
  if (ig && ig.inflight && ig.inflight.length) ctl.push(h('span', { class: 'small mut' }, ['In flight, moving to the next stub: ' + ig.inflight.join(', ')]));
  return h('div', {}, [h('div', { class: 'panel' }, [h('h1', { class: 'mono', style: 'margin-top:0' }, [branch]), h('div', { class: 'chips' }, cs), h('div', { class: 'row small mut' }, [s.refAge ? when(s.refAge, 'ref updated ') : null]),
    ctl.length ? h('div', { class: 'row' }, ctl) : null, err ? h('div', { class: 'err' }, [err]) : null,
    h('h2', {}, ['Implemented dossiers', h('span', { class: 'n' }, [String(s.dossiers.length)])]), impl])].concat(body));
}

// ---- header, crumbs ----
/* The dashboard chat: the same agent as the Slack DM (references/dm-agent.md, the runs MCP server). A new
   conversation is a new session, like a new DM message; a message in a conversation resumes it, like a thread reply. */
async function loadChat() {
  const want = R.chat;
  const [l, c] = await Promise.all([fetchJ('/api/chat'), want ? fetchJ('/api/chat/' + enc(want)) : Promise.resolve(null)]);
  if (l) { S.dm.list = l.chats || []; S.dm.enabled = l.enabled !== false; }
  if (R.kind === 'chat' && R.chat === want) { S.dm.conv = want ? c : null; S.dm.loadedFor = want; }
}
function chatView() {
  const D = S.dm; const cur = R.chat;
  const list = [h('a', { class: 'btn' + (cur ? '' : ' primary'), href: '/chat', 'data-k': 'dm:new' }, ['+ New conversation'])];
  D.list.forEach(c => list.push(h('a', { class: 'dmitem' + (c.id === cur ? ' cur' : ''), href: '/chat/' + enc(c.id), 'data-k': 'dl:' + c.id }, [
    h('div', { class: 'dmt' }, [c.pending ? h('span', { class: 'dot info live' }) : null, c.title || '(untitled)']),
    h('div', { class: 'small mut' }, [c.turns + (c.turns === 1 ? ' message · ' : ' messages · '), when(c.last)])])));
  if (!D.list.length) list.push(h('p', { class: 'small mut', 'data-k': 'dl:none' }, ['No conversations yet.']));
  const conv = cur && D.conv && D.conv.id === cur ? D.conv : null;
  const busy = D.busy || !!(conv && conv.messages.some(m => m.pending));
  const kids = [];
  if (!cur) {
    kids.push(h('h1', { 'data-k': 'dm:h' }, ['Ask the agent']));
    kids.push(h('p', { class: 'lead', 'data-k': 'dm:lead' }, ['The same agent as the Slack DM: it reads the runs, questions, stubs and dossiers through the runs tools, and can act on them (answer, message a run, start or stop one). Each conversation is its own session; a message in it continues that session.']));
  } else if (!conv) kids.push(h('div', { class: 'empty', 'data-k': 'dm:load' }, [D.loadedFor === cur ? 'No such conversation.' : 'Loading…']));
  else {
    kids.push(h('h2', { 'data-k': 'dm:t' }, [conv.title]));
    conv.messages.forEach(m => kids.push(h('div', { class: 'chat ' + (m.from === 'owner' ? 'owner' : 'agent') + (m.pending ? ' wait' : ''), 'data-k': 'dm:' + m.id + (m.pending ? ':p' : '') }, [
      h('div', { class: 'who' }, [(m.from === 'owner' ? 'You' : 'Agent') + ' · ', when(m.ts)]),
      m.pending ? h('div', {}, [h('span', { class: 'dot info live' }), ' The agent is answering…']) : mdBlock(m.html || '', 'dmm:' + m.id)])));
  }
  if (!D.enabled) kids.push(h('div', { class: 'err', 'data-k': 'dm:off' }, ['The agent is off on this server (ASD_DM_AGENT=0).']));
  else if (!cur || conv) {
    kids.push(h('textarea', { 'data-dm': '1', 'data-k': 'dm:ta:' + (cur || 'new'), placeholder: cur ? 'Reply in this conversation (Cmd+Enter to send)' : 'Ask about the runs, or tell the agent what to do (Cmd+Enter to send)', disabled: busy ? true : null }));
    kids.push(h('div', { class: 'ctl', 'data-k': 'dm:ctl' }, [h('button', { class: 'btn primary', type: 'button', 'data-act': 'dm-send', disabled: busy ? true : null }, [D.busy ? 'Sending…' : 'Send']),
      h('span', { class: 'small mut dmhint' }, [cur ? 'Continues this conversation’s session.' : 'Starts a new session.'])]));
  }
  if (D.err) kids.push(h('div', { class: 'err', 'data-k': 'dm:err' }, [D.err]));
  return h('div', { class: 'dmwrap' }, [h('nav', { class: 'dmlist', 'aria-label': 'Conversations' }, list), h('section', { class: 'dmthread' }, [h('div', { class: 'panel' }, kids)])]);
}
async function sendDm() {
  const D = S.dm; const ta = document.querySelector('textarea[data-dm]'); const text = ta ? ta.value.trim() : '';
  if (!text || D.busy) return;
  D.busy = true; D.err = ''; render();
  try {
    const body = R.chat ? { text, id: R.chat } : { text };
    const r = await fetch('/api/chat', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.id) { if (ta) ta.value = ''; D.busy = false; if (j.id !== R.chat) { navigate('/chat/' + enc(j.id)); return; } }
    else D.err = j.error || ('HTTP ' + r.status);
  } catch (e) { D.err = 'request failed'; }
  D.busy = false; await load(['chat']);
}
function crumbs() {
  const out = []; const add = (t, href) => { if (out.length) out.push(h('span', {}, ['›'])); out.push(href ? h('a', { href }, [t]) : h('span', { class: 'cur' }, [t])); };
  add('Home', R.kind === 'home' ? null : '/');
  if (R.kind === 'questions') add('Open questions');
  if (R.kind === 'chat') add('Ask the agent');
  if (R.repo) add(rlabel(R.repo), R.kind === 'repo' ? null : hrefRepo(R.repo));
  if (R.kind === 'item') add(R.id, R.forceDossier ? hrefItem(R.repo, R.id) : null);
  if (R.kind === 'item' && R.forceDossier) add('Dossier');
  if (R.kind === 'stub') add(R.branch);
  if (R.kind === 'arch') add('Architecture');
  return out;
}
function detailUrl() {
  if (R.kind === 'stub') return '/api/stub?repo=' + enc(R.repo) + '&branch=' + enc(R.branch);
  if (R.kind === 'item') { const r = findRun(R.repo, R.id); if (!r) return null; return '/api/timeline/' + enc(R.repo) + '/' + enc(R.id); }
  return null;
}
function build() {
  if (!S.loaded && !S.ready) return h('div', { class: 'skel', role: 'status', 'aria-label': 'Loading' }, [h('p', { class: 'skel-t' }, [h('span', { class: 'dot info live' }), ' Loading the pipeline…']), h('i'), h('i'), h('i'), h('i')]);
  if (R.kind === 'questions') return questionsView(); if (R.kind === 'repo') return repoView(R.repo);
  if (R.kind === 'chat') return chatView();
  if (R.kind === 'arch') return archView(R.repo);
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
  const sig = R.kind + '|' + (R.chat || '') + '|' + (R.repo || '') + '|' + (R.id || '') + '|' + (R.branch || '') + '|' + (R.forceDossier ? 'd' : '');
  const view = document.getElementById('view'); const fresh = document.createElement('div'); fresh.appendChild(build());
  if (sig !== routeSig) { routeSig = sig; view.replaceChildren(); window.scrollTo(0, 0); }
  morph(view, fresh);
  const cr = document.createElement('div'); crumbs().forEach(c => cr.appendChild(c)); morph(document.getElementById('crumbs'), cr);
  const sl = document.createElement('div'); sl.appendChild(h('span', { class: 'dot ' + (S.slack ? 'ok' : '') })); sl.appendChild(h('span', {}, [S.slack ? 'Slack connected' : 'Slack off'])); morph(document.getElementById('slack'), sl);
  const up = document.createElement('div'); up.appendChild(h('span', { class: 'dot ' + (S.live ? 'ok' : 'warn'), title: S.live ? 'live updates' : 'polling every 60 s' }));
  const t = new Date(S.changedAt || Date.now()).toISOString(); up.appendChild(when(t, 'updated ') || h('span')); if (S.refreshing || (S.stubs && S.stubs.refreshing)) up.appendChild(h('span', { class: 'muted', title: 'Showing the last saved view while the server rebuilds it' }, [' · refreshing…'])); morph(document.getElementById('upd'), up);
  document.title = (R.kind === 'chat' ? 'Ask the agent · ' : R.kind === 'item' ? R.id + ' · ' : R.kind === 'repo' ? R.repo + ' · ' : R.kind === 'arch' ? 'Architecture · ' + R.repo + ' · ' : '') + 'Pipeline · Owner questions';
  vrMount();
  if (R.kind === 'repo' && location.hash === '#rulings') { const e = document.getElementById('rulings'); if (e && S.hashDone !== location.href) { S.hashDone = location.href; e.scrollIntoView({ block: 'start' }); } }
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
  if (all || w.has('runs') || w.has('scan')) jobs.push(fetchJ('/api/runs').then(d => { if (d && d.pageId) { if (S.pageId && S.pageId !== d.pageId) { location.reload(); return; } S.pageId = d.pageId; } if (d && d.runs) { S.runs = d.runs; S.slack = d.slack; S.scannedAt = d.scannedAt; S.refreshing = !!d.refreshing; S.accounts = d.accounts || []; S.disk = d.disk || null; S.dirtyMain = d.dirtyMain || []; S.plans = d.plans || []; S.usageBusy = !!d.usageBusy; if (!S.loaded) { S.ready = true; render(); } } }));
  if (all || w.has('stubs') || w.has('scan')) jobs.push(fetchJ('/api/stubs').then(d => { if (d && d.repos) S.stubs = d; }));
  if (all || w.has('runs') || w.has('scan')) jobs.push(fetchJ('/api/autowork').then(d => { if (d && d.repos) { S.auto = {}; d.repos.forEach(v => { S.auto[v.repo] = v; }); } }));
  if (all || w.has('runs') || w.has('scan')) jobs.push(fetchJ('/api/delivery-flows').then(d => { if (d && d.repos) { S.flows = {}; d.repos.forEach(v => { S.flows[v.repo] = v; }); } }));
  // The non-code PR list is fetched once per repo; reload it when the server says it changed (or any run changed) while it is on screen.
  if ((all || w.has('docprs') || w.has('runs')) && R.kind === 'repo') { const st = S.docPrs[R.repo]; if (st && !st.loading) jobs.push(fetch('/api/repo/' + enc(R.repo) + '/doc-prs').then(r => r.json()).then(j => { if (!S.docPrs[R.repo] || !S.docPrs[R.repo].merging || j.merging) S.docPrs[R.repo] = j; }).catch(() => {})); }
  if ((all || w.has('runs')) && R.kind === 'repo') { const sr = R.repo; jobs.push(fetchJ('/api/pattern-sweep/' + enc(sr)).then(d => { if (d && d.repo) S.sweep[sr] = d; })); }
  if ((all || w.has('runs') || w.has('rulings')) && R.kind === 'repo') { const rr = R.repo; jobs.push(fetchJ('/api/rulings/' + enc(rr)).then(d => { if (d && Array.isArray(d.rules)) S.rulings[rr] = d.rules; })); }
  if ((all || w.has('chat')) && R.kind === 'chat') jobs.push(loadChat());
  await Promise.all(jobs);
  await loadDetail();
  if (R.kind === 'item') { S.loaded = true; S.changedAt = Date.now(); render(); } /* the header, the questions and the dossier text show before the (slow) diagrams arrive */
  if (R.kind === 'item') { pktLoad(R.repo, R.id); await vrLoad(R.repo, R.id); }
  if (R.kind === 'repo' || R.kind === 'arch') await vrMapLoad(R.repo);
  if (R.kind === 'arch') { const want = new URLSearchParams(location.search).get('d'); const m = VRM[R.repo]; if (want && (!m || m.open !== want)) vrOpenDiagram(R.repo, want); }
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
  clearTimeout(st.t); st.confirm = null; st.busy = a; st.msg = ''; if (a !== 'stop') pressed(repo, id, a); render();
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/' + a, { method: 'POST' });
    const j = await r.json().catch(() => ({})); st.msg = j.output || j.error || ('HTTP ' + r.status); st.ok = r.ok && j.ok !== false;
  } catch (e) { st.msg = 'request failed'; st.ok = false; }
  st.busy = null; await load(['runs']); if (a !== 'stop') { settle(repo, id, a); render(); }
}
async function fixSelected(btn) {
  const repo = btn.dataset.repo, id = btn.dataset.id, key = repo + '/' + id; const st = S.ctl[key] = S.ctl[key] || {};
  if (st.confirm !== 'fix-sel') { st.confirm = 'fix-sel'; clearTimeout(st.t); st.t = setTimeout(() => { if (st.confirm === 'fix-sel') { st.confirm = null; render(); } }, 8000); render(); return; }
  clearTimeout(st.t); st.confirm = null; st.busy = 'fix-sel'; st.msg = ''; st.last = 'fix-sel'; pressed(repo, id, 'fix-deferred'); render();
  const items = Object.keys(S.dsel[key] || {});
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/fix-deferred', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ items }) });
    const j = await r.json().catch(() => ({})); st.msg = j.output || j.error || ('HTTP ' + r.status); st.ok = r.ok && j.ok !== false;
    if (st.ok) S.dsel[key] = {};
  } catch (e) { st.msg = 'request failed'; st.ok = false; }
  st.busy = null; await load(['runs']); settle(repo, id, 'fix-deferred'); render();
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
async function removeRule(btn) {
  const repo = btn.dataset.repo, rule = btn.dataset.rule, st = S.chat['rul:' + repo] = S.chat['rul:' + repo] || {};
  if (st.confirm !== rule) { st.confirm = rule; btn.textContent = 'Confirm: Remove'; setTimeout(() => { if (st.confirm === rule) { st.confirm = null; render(); } }, 8000); return; }
  st.confirm = null; st.busy = true; st.msg = ''; render();
  try {
    const r = await fetch('/api/rulings/' + enc(repo), { method: 'DELETE', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ rule }) });
    const j = await r.json().catch(() => ({})); st.ok = r.ok && j.ok !== false; st.msg = j.output || j.error || ('HTTP ' + r.status);
  } catch (e) { st.ok = false; st.msg = 'request failed'; }
  st.busy = false; await load(['runs']);
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
  const st = S.ctl[k] = S.ctl[k] || {}; if (st.busy) return; st.busy = true; st.err = ''; pressed(repo, id, 'plan:' + item); render();
  try {
    const r = await fetch('/api/runs/' + enc(repo) + '/' + enc(id) + '/plan-item', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ item }) });
    const j = await r.json().catch(() => ({})); if (!(r.ok && j.ok !== false)) st.err = j.error || j.output || ('HTTP ' + r.status);
  } catch (e) { st.err = 'request failed'; }
  st.busy = false; await load(['runs']); settle(repo, id, 'plan:' + item); render();
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
    else if (act === 'vr-open') vrOpenDiagram(t.dataset.repo, t.dataset.path);
    else if (act === 'ctl-cancel') { const s = S.ctl[t.dataset.repo + '/' + t.dataset.id]; if (s) s.confirm = null; render(); }
    else if (act === 'tab') { S.tab[t.dataset.key] = t.dataset.tab; render(); }
    else if (act === 'dsel') { const m = S.dsel[t.dataset.key] = S.dsel[t.dataset.key] || {}; if (m[t.dataset.id]) delete m[t.dataset.id]; else m[t.dataset.id] = true; render(); }
    else if (act === 'dsel-all') { const r0 = S.runs.find(x => x.repo + '/' + x.id === t.dataset.key); if (r0) { const pk = r0.deferred.items.filter(dfPickable); const m = S.dsel[t.dataset.key] = S.dsel[t.dataset.key] || {}; const all = pk.every(i => m[i.id]); pk.forEach(i => { if (all) delete m[i.id]; else m[i.id] = true; }); render(); } }
    else if (act === 'fix-sel') fixSelected(t);
    else if (act === 'chat') sendChat(t);
    else if (act === 'dm-send') sendDm();
    else if (act === 'plan-item') planItem(t);
    else if (act === 'df-drop') { fetch('/api/runs/' + enc(t.dataset.repo) + '/' + enc(t.dataset.id) + '/deferred-drop', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ item: t.dataset.item, drop: t.dataset.drop === '1' }) }).catch(() => {}).finally(() => load(['runs'])); }
    else if (act === 'stub-confirm') { S.ctl[t.dataset.key] = { confirm: true }; render(); }
    else if (act === 'stub-cancel') { S.ctl[t.dataset.key] = {}; render(); }
    else if (act === 'stub-ctl') stubCtl(t);
    else if (act === 'docprs-merge' || act === 'docprs-refresh') docPrsAct(t, act);
    else if (act === 'aw-act') autoWorkAct(t);
    else if (act === 'fl-act') flowsAct(t);
    else if (act === 'merge-plan' || act === 'merge-go' || act === 'merge-cancel' || act === 'merge-unqueue') mergeAct(t, act);
    else if (act === 'summary') writeSummary(t);
    else if (act === 'blk') ruleBlock(t);
    else if (act === 'rmrule') removeRule(t);
    else if (act === 'pane-go') paneGo(t.dataset.pane);
    else if (act === 'fb-verdict') { const st = fbState(t.dataset.item), cur = fbNote(fbSavedOf(), st, t.dataset.anchor); fbSetNote(st, t.dataset.anchor, cur.verdict === t.dataset.verdict ? { verdict: '', comment: '', blocking: false } : { verdict: t.dataset.verdict, comment: cur.comment, blocking: cur.blocking }); if (st.viewed[t.dataset.anchor] === undefined) fbSetViewed(st, t.dataset.anchor, true); fbKeep(t.dataset.item); render(); }
    else if (act === 'fb-send') fbSend(t.dataset.item);
    else if (act === 'fb-discard') { S.fb[t.dataset.item] = fbEmpty(); fbKeep(t.dataset.item); render(); }
    else if (act === 'usage-refresh') { S.usageBusy = true; render(); fetch('/api/accounts/refresh', { method: 'POST' }).catch(() => {}).finally(() => load(['runs'])); }
    else if (act === 'toggle-done') { S.showAll[t.dataset.repo] = !S.showAll[t.dataset.repo]; render(); }
    return;
  }
  const a = e.target.closest ? e.target.closest('a[href]') : null;
  // A stub card opens its stub page from anywhere but its own links and buttons.
  const card = !a && e.target.closest ? e.target.closest('.scard[data-href]') : null;
  if (card && !e.target.closest('button,textarea,input,details>summary')) { e.preventDefault(); navigate(card.dataset.href); return; }
  if (!a || a.target === '_blank' || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
  const h2 = a.getAttribute('href');
  if (h2 && (h2 === '/' || h2.indexOf('/questions') === 0 || h2.indexOf('/chat') === 0 || h2.indexOf('/repo/') === 0)) { e.preventDefault(); navigate(h2); }
});
document.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && e.target.dataset && e.target.dataset.dm) { e.preventDefault(); sendDm(); return; } if ((e.key === ' ' || e.key === 'Enter') && e.target.classList && e.target.classList.contains('dfc') && e.target.dataset.act === 'dsel') { e.preventDefault(); e.target.click(); } });
/* The viewed tick, the blocking tick and the comment of a feedback bar: kept in the local draft on change (not on every keystroke). */
document.addEventListener('change', (e) => {
  const t = e.target; const k = t && t.dataset && t.dataset.fb; if (!k) return;
  const st = fbState(t.dataset.item), a = t.dataset.anchor;
  if (k === 'viewed') fbSetViewed(st, a, t.checked); else if (k === 'blocking') fbSetNote(st, a, { blocking: t.checked }); else if (k === 'comment') fbSetNote(st, a, { comment: t.value });
  fbKeep(t.dataset.item); render();
});
/* Opening or closing a pane (the summary click is also Enter and Space) is remembered for this viewer; the closed body is dropped and the open one built. */
document.addEventListener('click', (e) => {
  const sm = e.target.closest ? e.target.closest('details.pane>summary') : null; if (!sm) return;
  const dt = sm.parentNode; if (!dt.dataset.pane) return;
  paneSet(dt.dataset.pane, dt.dataset.key, !dt.open); setTimeout(render, 0);
});
/* A rail button or a "Open ..." button: open that pane, scroll to it and move focus to its summary. */
function paneGo(key) {
  const idOf = (k) => k === 'needs' ? 'needs' : 'pane-' + k.replace(/[^A-Za-z0-9_-]/g, '_');
  if (key !== 'needs' && R.kind === 'item') { paneSet(R.repo + '/' + R.id, key, true); render(); }
  const el = document.getElementById(idOf(key)); if (!el) return;
  el.scrollIntoView({ block: 'start' });
  const f = key === 'needs' ? el.querySelector('button,textarea,a[href]') : el.querySelector('summary'); if (f && f.focus) f.focus({ preventScroll: true });
}
window.addEventListener('popstate', onRoute);
window.addEventListener('hashchange', () => { if (R.kind === 'questions') { scrollTarget = hashQ(); render(); } else onRoute(); });
setInterval(() => { if (!S.live) load(null); }, 60000);   // fallback while the event stream is down
setInterval(() => { if (!document.hidden) render(); }, 30000);
onRoute(); connect();
</script></body></html>`
