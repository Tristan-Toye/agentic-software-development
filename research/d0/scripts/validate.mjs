// usage: bun validate.mjs SCHEMA CH_CLONE fixture.json...
import Ajv from 'ajv/dist/2020.js'; import fs from 'fs';
const [schemaPath,repo,...files]=process.argv.slice(2);
const ajv=new Ajv({allErrors:true,strict:false}); const validate=ajv.compile(JSON.parse(fs.readFileSync(schemaPath)));
let bad=0;
for(const f of files){const g=JSON.parse(fs.readFileSync(f)); const issues=[];
  if(!validate(g)) for(const e of validate.errors) issues.push('schema '+e.instancePath+' '+e.message);
  const ids=new Set(); const dup=x=>{if(ids.has(x.id))issues.push('duplicate_id '+x.id);ids.add(x.id);};
  (g.groups||[]).forEach(dup); g.nodes.forEach(dup);
  const gids=new Set((g.groups||[]).map(x=>x.id)), lanes=new Set((g.lanes||[]).map(x=>x.id));
  for(const n of g.nodes){ if(n.group&&!gids.has(n.group))issues.push('unknown_group_id '+n.id); if(n.lane&&!lanes.has(n.lane))issues.push('unknown_lane '+n.id);}
  for(const e of g.edges){ if(!ids.has(e.source))issues.push('unknown_edge_source '+e.id); if(!ids.has(e.target))issues.push('unknown_edge_target '+e.id); if(!e.id||ids.has(e.id)&&false){} }
  const all=[...(g.groups||[]),...g.nodes,...g.edges]; let src=0,inf=0;
  for(const x of all) for(const ev of x.evidence||[]){ if(ev.tier==='inferred'){inf++;continue;} src++;
    const p=repo+'/'+ev.path; if(!fs.existsSync(p)){issues.push('missing_evidence_path '+x.id+' '+ev.path);continue;}
    const n=fs.readFileSync(p,'utf8').split('\n').length; if(ev.line&&ev.line>n)issues.push('line_range_outside_file '+x.id+' '+ev.path+':'+ev.line); }
  const c={nodes:g.nodes.length,groups:(g.groups||[]).length,edges:g.edges.length,src_anchors:src,inferred_anchors:inf,
    status:Object.fromEntries(['added','changed','removed','context'].map(s=>[s,all.filter(x=>x.status===s).length]))};
  console.log(f.split('/').pop(),issues.length?'FAIL':'OK',JSON.stringify(c)); issues.forEach(i=>console.log('  ',i)); bad+=issues.length;}
process.exit(bad?1:0);
