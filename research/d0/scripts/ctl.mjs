import ELK from 'elkjs/lib/elk.bundled.js';
import {makeSpec,toElk} from './gen.mjs';
const elk=new ELK();
const flat=(g)=>{const kids=g.children.flatMap(c=>c.children);return {...g,children:kids};};
const pos=l=>Object.fromEntries(l.children.map(c=>[c.id,[c.x,c.y]]));
const out=[];
for(const n of [50,100]) for(const st of ['NONE','NODES_AND_EDGES']){
  const spec=makeSpec(n);
  const extra={nodes:[{id:'new0',g:0}],edges:[['n3','new0']]};
  const l1=await elk.layout(flat(toElk(spec,{strategy:st})));
  const l1b=await elk.layout(flat(toElk(spec,{strategy:st})));
  const l2=await elk.layout(flat(toElk(spec,{strategy:st,extra})));
  const a=pos(l1),b=pos(l1b),c=pos(l2);
  const det=Object.keys(a).every(k=>a[k][0]===b[k][0]&&a[k][1]===b[k][1]);
  const d=Object.keys(a).map(k=>Math.hypot(a[k][0]-c[k][0],a[k][1]-c[k][1]));
  out.push({flat:true,n,strategy:st,deterministic:det,moved_pct:Math.round(100*d.filter(x=>x>1).length/d.length),mean_px:Math.round(d.reduce((p,q)=>p+q,0)/d.length),max_px:Math.round(Math.max(...d)),layout_w:Math.round(l1.width),layout_h:Math.round(l1.height)});
}
// determinism of the grouped graph
for(const n of [50,200]){const spec=makeSpec(n);const A=JSON.stringify(await elk.layout(toElk(spec,{strategy:'NODES_AND_EDGES'})));const B=JSON.stringify(await elk.layout(toElk(spec,{strategy:'NODES_AND_EDGES'})));out.push({grouped:true,n,identical_json:A===B,json_kb:Math.round(A.length/1024)});}
console.log(out.map(o=>JSON.stringify(o)).join('\n'));process.exit(0);
