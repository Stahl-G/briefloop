import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
test('export template interaction stays open; action and outside clicks dismiss',()=>{
 const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const code=source.split('\n').find(line=>line.startsWith("document.addEventListener('click',")&&line.includes("querySelectorAll('.popover')"));
 let listener;const pop={hidden:false};const toggle={setAttribute:(key,value)=>toggle[key]=value};
 vm.runInNewContext(code,{document:{addEventListener:(_,fn)=>listener=fn,querySelectorAll:selector=>selector==='.popover'?[pop]:[toggle]}});
 listener({target:{closest:()=>({})}});assert.equal(pop.hidden,false);
 for(const kind of ['export action','outside']){pop.hidden=false;listener({target:{closest:()=>null}});assert.equal(pop.hidden,true,kind);assert.equal(toggle['aria-expanded'],'false');}
});
