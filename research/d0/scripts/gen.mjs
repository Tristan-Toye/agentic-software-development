// Deterministic synthetic graph generator for the D0 ELK benchmark.
export function rng(seed){let s=seed>>>0;return()=>{s=(s*1664525+1013904223)>>>0;return s/4294967296;};}
// n nodes in groups of ~10, each node has an "in" (WEST) and an "out" (EAST) port.
export function makeSpec(n,seed=7){
  const r=rng(seed); const per=10; const groups=Math.ceil(n/per);
  const nodes=[]; for(let i=0;i<n;i++) nodes.push({id:`n${i}`,g:Math.floor(i/per)});
  const edges=[]; let e=0;
  for(let i=1;i<n;i++){ // chain-like DAG, plus extra forward edges, ~1.6 edges per node
    const back=1+Math.floor(r()*4); const s=Math.max(0,i-back); edges.push([`n${s}`,`n${i}`]);
    if(r()<0.6){const s2=Math.floor(r()*i); if(s2!==s) edges.push([`n${s2}`,`n${i}`]);}
  }
  return {nodes,edges,groups};
}
export function toElk(spec,{strategy,extra=null}={}){
  const opts={'elk.algorithm':'layered','elk.direction':'RIGHT','elk.hierarchyHandling':'INCLUDE_CHILDREN',
    'elk.layered.considerModelOrder.strategy':strategy,'elk.spacing.nodeNode':'24','elk.layered.spacing.nodeNodeBetweenLayers':'40'};
  const nodes=spec.nodes.concat(extra?extra.nodes:[]); const edges=spec.edges.concat(extra?extra.edges:[]);
  const gid=i=>`grp${i}`; const ngroups=Math.max(spec.groups,...nodes.map(x=>x.g+1));
  const children=[]; for(let g=0;g<ngroups;g++) children.push({id:gid(g),layoutOptions:{'elk.padding':'[top=28,left=12,bottom=12,right=12]'},children:[]});
  for(const nd of nodes){ children[nd.g].children.push({id:nd.id,width:140,height:44,
    layoutOptions:{'elk.portConstraints':'FIXED_SIDE'},
    ports:[{id:nd.id+':in',layoutOptions:{'elk.port.side':'WEST'},width:6,height:6},{id:nd.id+':out',layoutOptions:{'elk.port.side':'EAST'},width:6,height:6}]}); }
  return {id:'root',layoutOptions:opts,children,edges:edges.map(([a,b],i)=>({id:`e${i}`,sources:[a+':out'],targets:[b+':in']}))};
}
// absolute position of every leaf node
export function abs(layout){const m={};(function walk(n,ox,oy){for(const c of n.children||[]){const x=ox+(c.x||0),y=oy+(c.y||0);if(c.ports)m[c.id]=[x,y];else{} walk(c,x,y);}})(layout,0,0);return m;}
