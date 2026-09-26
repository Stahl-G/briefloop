import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {section,allFrontendSources} from './source_section.mjs';
import {excelExportUI} from '../frontend/excel-export.js';

// The module reaches the DOM only through $/document at call time, so the
// click flow runs against plain stubs: a button, the layout select, an anchor.
function stubDocument({layout='sheets'}={}){
 const button={disabled:false,textContent:'生成 Excel',onclick:null};
 const select=layout===null?undefined:{value:layout};
 const clicked=[];
 const stub={getElementById:id=>id==='download-xlsx'?button:id==='export-xlsx-layout'?select:undefined,
  createElement:()=>{const a={href:'',download:'',click(){clicked.push(a)}};return a}};
 return {button,clicked,stub};
}

function installDocument(stub){
 const previous=globalThis.document,previousTimeout=globalThis.setTimeout;
 globalThis.document=stub;globalThis.setTimeout=fn=>fn();
 return ()=>{globalThis.document=previous;globalThis.setTimeout=previousTimeout};
}

test('xlsx export posts the saved version and chosen layout, then polls to a download link',async()=>{
 const {button,clicked,stub}=stubDocument({layout:'single'});
 const routes=[];let polls=0,lastRequestBody=null;const disabledTrace=[];
 const api=async(route,body)=>{
  routes.push(route);
  if(route==='export-xlsx'){lastRequestBody=body;return {id:'job_1',status:'queued'}}
  polls++;disabledTrace.push(button.disabled);
  return polls===1?{id:'job_1',status:'running'}:{id:'job_1',status:'complete'};
 };
 const notices=[],refreshes=[];
 const ui=excelExportUI({api,notice:(...args)=>notices.push(args),refresh:async()=>refreshes.push(1),
  savedVersion:async()=>'ver_1',parse:s=>JSON.parse(s||'{}'),getState:()=>({workspace_id:'w1'})});
 const restore=installDocument(stub);
 try{await ui.downloadXlsx()}finally{restore()}
 assert.deepEqual(routes,['export-xlsx','export-status?job=job_1','export-status?job=job_1'],'queued once, then polled to complete');
 assert.deepEqual(lastRequestBody,{version_id:'ver_1',layout:'single'},'the request carries the version id and the menu layout');
 assert.equal(clicked.length,1);
 assert.equal(clicked[0].href,'/api/export-file?job=job_1&workspace_id=w1');
 assert.deepEqual(notices,[['Excel 已生成，正在下载']]);
 assert.equal(refreshes.length,1);
 assert.deepEqual(disabledTrace,[true,true],'the button stays disabled while production is pending');
 assert.equal(button.disabled,false);
 assert.equal(button.textContent,'生成 Excel','the label is restored after the download');
});

test('a tableless report surfaces the backend refusal as an error notice',async()=>{
 const {button,stub}=stubDocument();
 const notices=[];
 const api=async route=>{if(route==='export-xlsx')throw Error('报告没有可导出的表格，无需生成 Excel');throw Error('unexpected '+route)};
 const ui=excelExportUI({api,notice:(...args)=>notices.push(args),refresh:async()=>{},
  savedVersion:async()=>'ver_1',parse:s=>JSON.parse(s||'{}'),getState:()=>({workspace_id:'w1'})});
 const restore=installDocument(stub);
 try{await ui.downloadXlsx()}finally{restore()}
 assert.deepEqual(notices,[['Excel 下载未完成：报告没有可导出的表格，无需生成 Excel',true]]);
 assert.equal(button.disabled,false);
 assert.equal(button.textContent,'生成 Excel');
});

test('queued and running states show production wording on the button',async()=>{
 const {stub}=stubDocument({layout:'sheets'});
 const wording=[];
 const api=async route=>{
  if(route==='export-xlsx')return {id:'job_1',status:'queued'};
  wording.push(document.getElementById('download-xlsx').textContent);
  return wording.length===1?{id:'job_1',status:'running'}:{id:'job_1',status:'complete'};
 };
 const ui=excelExportUI({api,notice:()=>{},refresh:async()=>{},
  savedVersion:async()=>'ver_1',parse:s=>JSON.parse(s||'{}'),getState:()=>({workspace_id:'w1'})});
 const restore=installDocument(stub);
 try{await ui.downloadXlsx()}finally{restore()}
 // Each poll round has just set the label for the state it observed.
 assert.deepEqual(wording,['等待制作…','正在制作…']);
});

test('a workspace switch during Excel production stops the download',async()=>{
 const {stub}=stubDocument();
 let workspace='w1',polls=0;const notices=[];
 const api=async route=>{
  if(route==='export-xlsx')return {id:'job_1',status:'queued'};
  polls++;if(polls===1)workspace='w2';
  return {id:'job_1',status:'running'};
 };
 const ui=excelExportUI({api,notice:(...args)=>notices.push(args),refresh:async()=>{},
  savedVersion:async()=>'ver_1',parse:s=>JSON.parse(s||'{}'),getState:()=>({workspace_id:workspace})});
 const restore=installDocument(stub);
 try{await ui.downloadXlsx()}finally{restore()}
 assert.ok(polls>=1,'the guard must run while production is still pending');
 assert.equal(notices.length,1);
 assert.match(notices[0][0],/工作区已切换，请在原工作区下载/);
 assert.equal(notices[0][1],true,'the cancellation is an error notice');
});

test('app.js wires excelExportUI beside reportExport and lists the xlsx file kind',()=>{
 const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 assert.ok(allFrontendSources().includes("import {excelExportUI} from './excel-export.js';"),
  'the factory module is imported');
 const wiring=section(app,'const reportExport=reportExportUI',"for(const id of ['download','download-docx','download-bundle'])");
 assert.match(wiring,/const excelExport=excelExportUI\(\{api,notice,refresh,savedVersion,parse,getState:\(\)=>state\}\);/);
 assert.match(wiring,/excelExport\.init\(\);/);
 assert.match(section(app,'function renderWordExports(){','const jobs=state.jobs.filter'),/export_xlsx:'工作稿 Excel'/);
});

test('the export menu offers the Excel entry with both layouts',()=>{
 const html=fs.readFileSync(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
 assert.match(html,/<button id="download-xlsx" type="button" role="menuitem">生成 Excel<\/button>/);
 assert.match(html,/<select id="export-xlsx-layout"><option value="sheets">每表一工作表<\/option><option value="single">单表连续<\/option><\/select>/);
 assert.match(html,/<label class="export-template-row"><span>Excel 版式<\/span>/,'the layout select keeps the popover open like the Word one');
});
