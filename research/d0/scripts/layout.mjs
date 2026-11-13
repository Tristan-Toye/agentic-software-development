// usage: bun layout.mjs fixture.json out.json   (ELK layered, model order on, ROOT coordinates)
import ELK from 'elkjs/lib/elk.bundled.js'; import fs from 'fs';
const [inp,outp]=process.argv.slice(2); const g=JSON.parse(fs.readFileSync(inp)); const elk=new ELK();
const size=n=>{const t=g.view==='L1'?Math.max(...n.label.split(' ').length?[n.label.length]:[0]):n.label.length; return {width:Math.max(130,Math.min(250,Math.round(t*6.6+28))),height:g.view==='L1'?62:50};};
const dir=g.view==='L1'?'DOWN':'RIGHT';
const opts={'elk.algorithm':'layered','elk.direction':dir,'elk.hierarchyHandling':'INCLUDE_CHILDREN','elk.layered.considerModelOrder.strategy':'NODES_AND_EDGES',
 'elk.spacing.nodeNode':'28','elk.layered.spacing.nodeNodeBetweenLayers':'56','elk.edgeRouting':'ORTHOGONAL','elk.json.shapeCoords':'ROOT','elk.json.edgeCoords':'ROOT'};
const mk=n=>({id:n.id,...size(n)});
const kidsOf=(gid)=>g.nodes.filter(n=>(g.view==='L1'?n.lane:n.group)===gid);
const groupsSrc=g.view==='L1'?g.lanes.map(l=>({id:l.id,label:l.label})):g.groups;
const root={id:'root',layoutOptions:opts,children:[],edges:g.edges.map(e=>({id:e.id,sources:[e.source],targets:[e.target]}))};
for(const gr of groupsSrc) root.children.push({id:gr.id,layoutOptions:{'elk.padding':'[top=30,left=14,bottom=14,right=14]'},children:kidsOf(gr.id).map(mk)});
for(const n of g.nodes) if(!(g.view==='L1'?n.lane:n.group)) root.children.push(mk(n));
const t=performance.now(); const out=await elk.layout(root); const ms=Math.round(performance.now()-t);
const res={schema_version:g.schema_version,view:g.view,layout:{engine:'elkjs@0.12.1',algorithm:'layered',direction:dir,considerModelOrder:'NODES_AND_EDGES',ms},width:Math.ceil(out.width),height:Math.ceil(out.height),boxes:{},edges:{}};
(function walk(n){for(const c of n.children||[]){res.boxes[c.id]={x:Math.round(c.x),y:Math.round(c.y),w:Math.round(c.width),h:Math.round(c.height)};walk(c);}})(out);
for(const e of out.edges||[]){const s=(e.sections||[])[0]; if(s)res.edges[e.id]=[s.startPoint,...(s.bendPoints||[]),s.endPoint].map(p=>[Math.round(p.x),Math.round(p.y)]);}
fs.writeFileSync(outp,JSON.stringify(res)); console.log(inp.split('/').pop(),'ms',ms,'size',res.width+'x'+res.height,'boxes',Object.keys(res.boxes).length,'edges',Object.keys(res.edges).length); process.exit(0);
