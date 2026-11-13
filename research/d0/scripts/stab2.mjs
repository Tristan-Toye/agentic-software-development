import ELK from 'elkjs/lib/elk.bundled.js';
import {makeSpec,toElk,rng} from './gen.mjs';
const elk=new ELK();
function absPos(l){const m={};(function w(n,ox,oy){for(const c of n.children||[]){const x=ox+(c.x||0),y=oy+(c.y||0);if(c.ports)m[c.id]=[x,y];w(c,x,y);}})(l,0,0);return m;}
const variants={
 NONE:{'elk.layered.considerModelOrder.strategy':'NONE'},
 NODES_AND_EDGES:{'elk.layered.considerModelOrder.strategy':'NODES_AND_EDGES'},
 NAE_forceNodeOrder:{'elk.layered.considerModelOrder.strategy':'NODES_AND_EDGES','elk.layered.crossingMinimization.forceNodeModelOrder':'true'},
};
function build(spec,extra,v,insertMid){
  const g=toElk(spec,{strategy:'NONE',extra:insertMid?null:extra});
  Object.assign(g.layoutOptions,variants[v]);
  if(insertMid&&extra){ // insert new nodes in the middle of their group's child list (model order differs)
    for(const nd of extra.nodes){const grp=g.children[nd.g];const mid=Math.floor(grp.children.length/2);
      grp.children.splice(mid,0,{id:nd.id,width:140,height:44,layoutOptions:{'elk.portConstraints':'FIXED_SIDE'},ports:[{id:nd.id+':in',layoutOptions:{'elk.port.side':'WEST'},width:6,height:6},{id:nd.id+':out',layoutOptions:{'elk.port.side':'EAST'},width:6,height:6}]});}
    extra.edges.forEach(([a,b],i)=>g.edges.push({id:`x${i}`,sources:[a+':out'],targets:[b+':in']}));
  }
  return g;
}
function addNodes(spec,k,seed,n){const r=rng(seed);const nodes=[],edges=[];
  for(let j=0;j<k;j++){const id=`new${j}`;const gi=Math.floor(r()*spec.groups);nodes.push({id,g:gi});
    const a=`n${Math.floor(r()*n)}`;edges.push([a,id]);const b=`n${Math.floor(r()*n)}`;if(b!==a)edges.push([id,b>a?b:a===b?b:b]);}
  // keep DAG simple: edge to a node with a larger index than the source only
  return {nodes,edges:edges.filter(([s,t])=>s.startsWith('new')?!t.startsWith('new'):true)};}
const rows=[];
for(const n of [20,50,100,200]){ const spec=makeSpec(n);
 for(const k of [1,3]) for(const place of ['append']) for(const v of Object.keys(variants)){
  const stats=[];
  for(let seed=1;seed<=5;seed++){
    const extra=addNodes(spec,k,seed,n);
    const b=absPos(await elk.layout(build(spec,null,v,false)));
    const a=absPos(await elk.layout(build(spec,extra,v,place==='middle')));
    const ids=Object.keys(b); const d=ids.map(i=>Math.hypot(a[i][0]-b[i][0],a[i][1]-b[i][1]));
    const dx=ids.map(i=>a[i][0]-b[i][0]).sort((p,q)=>p-q), dy=ids.map(i=>a[i][1]-b[i][1]).sort((p,q)=>p-q);
    const mx=dx[dx.length>>1],my=dy[dy.length>>1]; // remove global shift
    const d2=ids.map(i=>Math.hypot(a[i][0]-b[i][0]-mx,a[i][1]-b[i][1]-my));
    const pr=rng(99);let px=0,py=0,tot=0;const P=ids.length<=100?null:4000;
    const pairs=[];if(!P){for(let i=0;i<ids.length;i++)for(let j=i+1;j<ids.length;j++)pairs.push([ids[i],ids[j]]);}else for(let t=0;t<P;t++){const i=Math.floor(pr()*ids.length),j=Math.floor(pr()*ids.length);if(i!==j)pairs.push([ids[i],ids[j]]);}
    for(const [p,q] of pairs){tot++;if(Math.sign(b[p][0]-b[q][0])===Math.sign(a[p][0]-a[q][0]))px++;if(Math.sign(b[p][1]-b[q][1])===Math.sign(a[p][1]-a[q][1]))py++;}
    stats.push({ox:px/tot,oy:py/tot,moved:d.filter(x=>x>1).length/ids.length,mean:d.reduce((p,q)=>p+q,0)/d.length,max:Math.max(...d),moved2:d2.filter(x=>x>1).length/ids.length,mean2:d2.reduce((p,q)=>p+q,0)/d2.length,max2:Math.max(...d2)});
  }
  const avg=f=>stats.reduce((p,s)=>p+s[f],0)/stats.length;
  rows.push({n,added:k,place,strategy:v,moved_pct:+(100*avg('moved')).toFixed(0),mean_px:+avg('mean').toFixed(0),max_px:+Math.max(...stats.map(s=>s.max)).toFixed(0),order_x_pct:+(100*avg('ox')).toFixed(0),order_y_pct:+(100*avg('oy')).toFixed(0),moved_pct_after_shift:+(100*avg('moved2')).toFixed(0),mean_px_after_shift:+avg('mean2').toFixed(0)});
 }}
console.log(rows.map(r=>JSON.stringify(r)).join('\n'));
process.exit(0);
