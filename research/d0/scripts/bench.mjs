// usage: bench.mjs N STRATEGY REPS MODE(bundled|worker)
import {makeSpec,toElk} from './gen.mjs';
const n=+process.argv[2], strategy=process.argv[3], reps=+process.argv[4], mode=process.argv[5]||'bundled';
let elk;
if(mode==='worker'){const ELK=(await import('elkjs/lib/elk-api.js')).default;
  elk=new ELK({workerUrl:new URL('./node_modules/elkjs/lib/elk-worker.min.js',import.meta.url).pathname});}
else {const ELK=(await import('elkjs/lib/elk.bundled.js')).default; elk=new ELK();}
const spec=makeSpec(n); const times=[]; const rss0=process.memoryUsage().rss;
for(let i=0;i<reps;i++){const g=toElk(spec,{strategy});const t=performance.now();await elk.layout(g);times.push(performance.now()-t);}
const rss1=process.memoryUsage().rss; const s=[...times].sort((a,b)=>a-b);
console.log(JSON.stringify({rt:typeof Bun!=='undefined'?'bun '+Bun.version:'node '+process.version,mode,n,edges:spec.edges.length,strategy,cold_ms:Math.round(times[0]),warm_median_ms:Math.round(s[Math.floor(s.length/2)]),rss_before_mb:Math.round(rss0/1e6),rss_after_mb:Math.round(rss1/1e6)}));
if(elk.terminateWorker&&mode==='worker')elk.terminateWorker(); process.exit(0);
