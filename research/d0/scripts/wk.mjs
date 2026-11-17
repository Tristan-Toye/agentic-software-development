import ELK from 'elkjs/lib/elk-api.js';
import {makeSpec,toElk} from './gen.mjs';
const elk=new ELK({workerUrl:new URL('./node_modules/elkjs/lib/elk-worker.min.js',import.meta.url).pathname});
const spec=makeSpec(+process.argv[2]||100); const ts=[];
for(let i=0;i<4;i++){const t=performance.now();await elk.layout(toElk(spec,{strategy:'NODES_AND_EDGES'}));ts.push(Math.round(performance.now()-t));}
console.log('worker mode',typeof Bun!=='undefined'?'bun':'node',ts.join(','));
elk.terminateWorker();
