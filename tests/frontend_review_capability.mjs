import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section} from './source_section.mjs';

// Windows checkouts may use CRLF; function extraction below matches LF boundaries.
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const line=name=>source.split('\n').find(l=>l.startsWith(`function ${name}(`));
const sync=section(source,'function syncFactCheckControl(){','\n}\n','frontend/app.js')+'\n}\n';

function form({backend='codex',label='Codex CLI',allowWeb=true,factCheck=true,mode='internal_report',listed=[{id:'opencode',label:'Opencode CLI'}],reviewer=null}={}){
 const note={hidden:true,textContent:''};
 const elements={fact_check:{checked:factCheck,disabled:false},allow_web:{checked:allowWeb},writing_mode:{value:mode}};
 const nodes={requirements:{elements},'review-capability-note':note,'agent-backend':{value:backend,selectedOptions:[{textContent:label}]}};
 const ctx=vm.createContext({$:id=>nodes[id],state:listed===null?undefined:{review_capability:{restricted_review:listed},settings:{review_runtime:reviewer}},backendValue:()=>backend});
 vm.runInContext(line('reviewBackends')+'\n'+line('reviewRuntime')+'\n'+sync,ctx);
 ctx.syncFactCheckControl();
 return {elements,note};
}

test('fact check cannot be chosen on a backend without the restricted Reviewer',()=>{
 const {elements,note}=form();
 assert.equal(elements.fact_check.disabled,true);
 assert.equal(elements.fact_check.checked,false);
 assert.equal(note.hidden,false);
 assert.match(note.textContent,/Codex CLI 尚未验证受限独立审阅，不能开启事实核查/);
 assert.match(note.textContent,/企业内部报告仍可生成，评分为普通评分（不是独立审阅）/);
 assert.match(note.textContent,/改用 Opencode CLI/);
 const general=form({mode:'general'});
 assert.doesNotMatch(general.note.textContent,/企业内部报告/);
});

test('a supported backend keeps the switch and hides the note',()=>{
 const {elements,note}=form({backend:'opencode',label:'Opencode CLI'});
 assert.equal(elements.fact_check.disabled,false);
 assert.equal(elements.fact_check.checked,true);
 assert.equal(note.hidden,true);
 assert.equal(form({backend:'opencode',allowWeb:false}).elements.fact_check.disabled,true,'offline still disables it');
});

test('a separately chosen Reviewer keeps fact check available on any main backend',()=>{
 const {elements,note}=form({reviewer:{backend:'briefloop-native',model:'opencode-go/deepseek-v4.1-flash'}});
 assert.equal(elements.fact_check.disabled,false);
 assert.equal(note.hidden,true);
 assert.match(form().note.textContent,/独立审阅执行后端/);
});

test('before state loads the page does not guess; the server still enforces',()=>{
 const {elements,note}=form({listed:null});
 assert.equal(elements.fact_check.disabled,false);
 assert.equal(note.hidden,true);
});

test('the ordinary-assessment label comes from the controller-set basis',()=>{
 const panelSource=fs.readFileSync(new URL('../frontend/assessment-panel.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
 assert.match(panelSource,/d\.basis==='assessment_without_review'\?'<p class="help review-basis">普通评分，不是独立受限审阅/);
});
