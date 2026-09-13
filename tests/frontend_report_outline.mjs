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
 assessment:()=>{},citations:()=>{},renderBriefLength:()=>{},renderWordExports:()=>{},renderReportStatus:()=>{},renderAssistantSummary:()=>{},editor:null,
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

// Editing the outline textarea must survive switching edit <-> outline.
const outlineBox=el('outline-text');
outlineBox.value='## Edited heading\n## Another';
outlineBox.oninput();
vm.runInContext("setReportView('edit')",c);
vm.runInContext("setReportView('outline')",c);
assert.ok(el('report-outline').innerHTML.includes('Edited heading'));

// Reset clears the edit and restores the current document headings.
el('outline-reset').onclick();
assert.equal(outlineBox.value,'## New heading');
vm.runInContext("setReportView('edit')",c);
vm.runInContext("setReportView('outline')",c);
assert.ok(el('report-outline').innerHTML.includes('New heading'));
assert.ok(!el('report-outline').innerHTML.includes('Edited heading'));
console.log('PASS: opening a report leaves outline view and shows the new report in the editor');
console.log('PASS: outline edits persist across view switches and reset restores document headings');

// applyOutlineToSetup must only change manual_sections_text, never unrelated fields.
{
 const applyCode=source.slice(source.indexOf('function applyOutlineToSetup'),source.indexOf('function expandReportPanel'));
 const calls=[];
 const outlineEl={value:'## Alpha\n\n## Beta\n123\n## Gamma'};
 const form={elements:{manual_sections_text:{value:''},objective:{value:'ORIGINAL'},report_profile:{value:'p'}}};
 const a=vm.createContext({console,Promise,
  $:id=>id==='outline-text'?outlineEl:id==='requirements'?form:null,
  applyRequirements:x=>calls.push(x),notice:()=>{}});
 vm.runInContext(applyCode,a);
 vm.runInContext('applyOutlineToSetup()',a);
 assert.deepEqual(calls,[JSON.stringify({manual_sections:['Alpha','Beta','123','Gamma']})]);
 assert.equal(form.elements.objective.value,'ORIGINAL');
 assert.equal(form.elements.report_profile.value,'p');
 outlineEl.value='   ';
 vm.runInContext('applyOutlineToSetup()',a);
 assert.equal(calls.length,1);
 console.log('PASS: applying the outline touches only manual_sections_text and rejects an empty outline');
}
