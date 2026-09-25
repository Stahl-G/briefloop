import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {allFrontendSources} from './source_section.mjs';
import {exportFileName,printHtml,reportExportUI} from '../frontend/report-export.js';

// Windows checkouts may use CRLF; the renumbering loop below matches LF boundaries.
const source=fs.readFileSync(new URL('../frontend/report-export.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');

test('report exports never depend on opening a new window',()=>{
 assert.ok(!allFrontendSources().includes('window.open('),'the desktop shell denies new windows');
 assert.match(source,/a\.download=exportFileName\(title\)\+'\.html'/);
 assert.match(source,/if\(kind==='pdf'\)await exportPdf\(html,title\);/);
});

test('export file names follow the server Word naming rules',()=>{
 assert.equal(exportFileName(' 季度/报告:终稿?. '),'季度_报告_终稿_');
 assert.equal(exportFileName('...'),'报告');
 assert.equal(exportFileName('a\u0001b'),'a_b');
 assert.equal(exportFileName('长'.repeat(200)).length,120);
});

test('browser PDF export prints a sandboxed frame once its document has loaded',async()=>{
 const calls=[],frames=[];
 function makeFrame(){
  // decode() never settles in a hidden frame; printing must not wait for it.
  const images=[{decode:()=>new Promise(()=>{})}];
  const view={location:{href:'about:blank'},document:{images},listeners:{},
   addEventListener(name,fn,options){this.listeners[name]={fn,options}},focus(){calls.push('focus')},print(){calls.push('print')}};
  const frame={attributes:{},style:{},contentWindow:view,
   setAttribute(key,value){this.attributes[key]=value},remove(){calls.push('remove');this.removed=true}};
  frames.push(frame);return frame;
 }
 const previousDocument=globalThis.document;
 // dom-free module helpers resolve `document` at call time.
 globalThis.document={createElement:tag=>{assert.equal(tag,'iframe');return makeFrame()},body:{append:frame=>calls.push(['append',frame.srcdoc])}};
 const html='<!doctype html><title>报告</title><img src="data:image/png;base64,AAAA">';
 const printed=printHtml(html);
 const [frame]=frames;
 assert.equal(frame.attributes.sandbox,'allow-same-origin allow-modals');
 assert.ok(!frame.attributes.sandbox.includes('allow-scripts'));
 assert.equal(frame.attributes['aria-hidden'],'true');
 assert.deepEqual(calls,[['append',html]]);
 frame.onload();
 assert.deepEqual(calls,[['append',html]],'the initial blank document is not printed');
 frame.contentWindow.location.href='about:srcdoc';
 frame.onload();await printed;
 assert.deepEqual(calls.slice(1),['focus','print']);
 assert.equal(frame.removed,undefined,'the frame stays until printing finishes');
 assert.deepEqual({...frame.contentWindow.listeners.afterprint.options},{once:true});
 const second=printHtml(html);
 assert.equal(frame.removed,true,'a new export replaces an unfinished frame');
 frames[1].contentWindow.location.href='about:srcdoc';frames[1].onload();await second;
 frame.contentWindow.listeners.afterprint.fn();
 assert.equal(frames[1].removed,undefined,'a stale afterprint does not remove the new frame');
 frames[1].contentWindow.listeners.afterprint.fn();
 assert.equal(frames[1].removed,true);
 globalThis.document=previousDocument;
});

test('desktop PDF export uses the fixed shell channel instead of the frame',async()=>{
 const notices=[],requests=[];
 const desktop={exportPdf:async request=>{requests.push(request);return {status:'saved',name:'季度报告.pdf'}}};
 const previousWindow=globalThis.window,previousDocument=globalThis.document;
 globalThis.window={briefloopDesktop:desktop};
 const exporter=reportExportUI({notice:(...args)=>notices.push(args),api:async()=>{},refresh:async()=>{},
  savedVersion:async()=>'v',toEditor:x=>x,parse:s=>JSON.parse(s||'{}'),getState:()=>({}),getCurrent:()=>null,getEditor:()=>null});
 await exporter.exportPdf('<p>x</p>','季度报告');
 assert.equal(JSON.stringify(requests),JSON.stringify([{html:'<p>x</p>',title:'季度报告'}]));
 assert.deepEqual(notices,[['PDF 已保存：季度报告.pdf']]);
 desktop.exportPdf=async()=>({status:'cancelled'});
 await exporter.exportPdf('<p>x</p>','季度报告');
 assert.equal(notices.length,1,'cancelling the save dialog is silent');
 desktop.exportPdf=async()=>{throw Error("Error invoking remote method 'report:export-pdf': Error: 正在导出另一份 PDF，请稍候。")};
 await assert.rejects(exporter.exportPdf('<p>x</p>','季度报告'),{message:'正在导出另一份 PDF，请稍候。'});
 // Without the desktop channel the export falls back to the shared print frame.
 const calls=[],frames=[];
 function makeFrame(){
  const view={location:{href:'about:blank'},listeners:{},addEventListener(name,fn){this.listeners[name]=fn},focus(){calls.push('focus')},print(){calls.push('print')}};
  const frame={attributes:{},style:{},contentWindow:view,srcdoc:'',
   setAttribute(key,value){this.attributes[key]=value},remove(){calls.push('remove');this.removed=true}};
  frames.push(frame);return frame;
 }
 globalThis.document={createElement:tag=>{assert.equal(tag,'iframe');return makeFrame()},body:{append:()=>{}}};
 desktop.exportPdf=undefined;
 const fallback=exporter.exportPdf('<p>browser</p>','x');
 const frame=frames.at(-1);
 assert.equal(frame.attributes.sandbox,'allow-same-origin allow-modals');
 frame.contentWindow.location.href='about:srcdoc';frame.onload();await fallback;
 assert.equal(frame.srcdoc,'<p>browser</p>');
 assert.deepEqual(calls.slice(-2),['focus','print']);
 globalThis.window=previousWindow;globalThis.document=previousDocument;
});

test('a workspace switch during Word production stops the download from the original workspace',async()=>{
 const notices=[];let workspace='w1',polls=0;
 const exporter=reportExportUI({api:async route=>{
   if(route==='export')return {id:'job',status:'queued'};
   polls++;if(polls===1)workspace='w2';
   return {id:'job',status:'running'};
  },notice:(...args)=>notices.push(args),refresh:async()=>{},savedVersion:async()=>'v1',
  toEditor:x=>x,parse:s=>JSON.parse(s||'{}'),getState:()=>({workspace_id:workspace}),getCurrent:()=>null,getEditor:()=>null});
 // The production poll waits a second per round; resolve it immediately here.
 const previousTimeout=globalThis.setTimeout,previousDocument=globalThis.document;
 globalThis.setTimeout=fn=>fn();
 globalThis.document={getElementById:()=>({}),createElement:()=>({click(){}})};
 await exporter.downloadWord();
 globalThis.setTimeout=previousTimeout;globalThis.document=previousDocument;
 assert.ok(polls>=1,'the guard must run while production is still pending');
 assert.equal(notices.length,1);
 assert.match(notices[0][0],/工作区已切换，请在原工作区下载/);
 assert.equal(notices[0][1],true,'the cancellation is an error notice');
});

test('numeric citations are renumbered with the reference list, named links are kept',()=>{
 const start=source.indexOf("for(const a of body.querySelectorAll('a[href^=\"#source-\"]')){");
 assert.ok(start>0);
 const loop=source.slice(start,source.indexOf('\n    }\n',start)+6);
 const anchor=(href,text)=>({href,textContent:text,attributes:{href},
  getAttribute(name){return this.attributes[name]},setAttribute(name,value){this.attributes[name]=value}});
 // An older draft numbered its citations by workspace source order.
 const links=[anchor('#source-s5','5'),anchor('#source-s2','[2]'),anchor('#source-s5','5'),
  anchor('#source-s9','（9）'),anchor('#source-s2','年度报告'),anchor('#source-s2','2 号材料')];
 const cited=[];
 vm.runInNewContext(loop,{body:{querySelectorAll:()=>links},cited});
 assert.deepEqual(cited,['s5','s2','s9']);
 assert.deepEqual(links.map(a=>[a.attributes.href,a.textContent]),[
  ['#reference-1','1'],['#reference-2','[2]'],['#reference-1','1'],
  ['#reference-3','（3）'],['#reference-2','年度报告'],['#reference-2','2 号材料']]);
});
