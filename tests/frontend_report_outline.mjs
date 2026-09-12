// Opening a different report while the outline view is open must return to the editor.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const outlineCode=source.slice(source.indexOf('function outlineHeadings'),source.indexOf('function applyOutlineToSetup'));
const openCode=source.slice(source.indexOf('function openBrief'),source.indexOf('async function renderDeliveryChecks'));
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',hidden:false,innerHTML:'',querySelectorAll:()=>[]});return elements.get(id)};
let editorContent='';
const report={id:'brief-new',run_id:'run-new',detail:'{}',markdown:'## New heading\nBody',author:'agent'};
const c=vm.createContext({console,Promise,$:el,dirty:false,saving:false,current:{id:'brief-old',run_id:'run-old',markdown:'## Old heading'},
 parse:s=>JSON.parse(s||'{}'),updateDownloads:()=>{},notice:()=>{},esc:s=>String(s),
 state:{briefs:[report]},report,
 Editor:class{constructor(options){editorContent=options.content}destroy(){}},
 StarterKit:{configure:()=>({})},TableKit:{},ReportImage:{configure:()=>({})},TextStyle:{},Layout:{},Citation:{},Markdown:{},MustFixHighlight:{},
 toEditor:x=>x,editorDocument:x=>x,changed:()=>{},updateFormattingTools:()=>{},
 assessment:()=>{},citations:()=>{},renderBriefLength:()=>{},editor:null,
 document:{querySelectorAll:()=>[]}});
el('report-grid').hidden=true;el('report-outline').hidden=false;
vm.runInContext(outlineCode+'\n'+openCode,c);
assert.equal(vm.runInContext('openBrief(report)',c),true);
assert.equal(c.current.id,'brief-new');
assert.equal(el('report-outline').hidden,true);
assert.equal(el('report-grid').hidden,false);
assert.equal(editorContent,'## New heading\nBody');
vm.runInContext("setReportView('outline')",c);
assert.ok(el('report-outline').innerHTML.includes('New heading'));
assert.ok(!el('report-outline').innerHTML.includes('Old heading'));
console.log('PASS: opening a report leaves outline view and shows the new report in the editor');
