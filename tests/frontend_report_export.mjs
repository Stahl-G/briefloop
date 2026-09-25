import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {allFrontendSources} from './source_section.mjs';

// Windows checkouts may use CRLF; function extraction below matches LF boundaries.
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
function extract(name){
 const start=source.indexOf(`function ${name}(`);assert.ok(start>=0,name);
 const prefix=source.lastIndexOf('\n',start)+1;
 const lineEnd=source.indexOf('\n',start);
 if(source.slice(start,lineEnd).trimEnd().endsWith('}'))return source.slice(prefix,lineEnd);
 return source.slice(prefix,source.indexOf('\n}\n',start)+2);
}

test('report exports never depend on opening a new window',()=>{
 assert.ok(!allFrontendSources().includes('window.open('),'the desktop shell denies new windows');
 assert.match(source,/a\.download=exportFileName\(title\)\+'\.html'/);
 assert.match(source,/if\(kind==='pdf'\)await exportPdf\(html,title\);/);
});

test('export file names follow the server Word naming rules',()=>{
 const ctx=vm.createContext({});vm.runInContext(extract('exportFileName'),ctx);
 assert.equal(ctx.exportFileName(' 季度/报告:终稿?. '),'季度_报告_终稿_');
 assert.equal(ctx.exportFileName('...'),'报告');
 assert.equal(ctx.exportFileName('a\u0001b'),'a_b');
 assert.equal(ctx.exportFileName('长'.repeat(200)).length,120);
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
 const document={createElement:tag=>{assert.equal(tag,'iframe');return makeFrame()},body:{append:frame=>calls.push(['append',frame.srcdoc])}};
 const ctx=vm.createContext({document});vm.runInContext(extract('printHtml'),ctx);
 const html='<!doctype html><title>报告</title><img src="data:image/png;base64,AAAA">';
 const printed=ctx.printHtml(html);
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
 const second=ctx.printHtml(html);
 assert.equal(frame.removed,true,'a new export replaces an unfinished frame');
 frames[1].contentWindow.location.href='about:srcdoc';frames[1].onload();await second;
 frame.contentWindow.listeners.afterprint.fn();
 assert.equal(frames[1].removed,undefined,'a stale afterprint does not remove the new frame');
 frames[1].contentWindow.listeners.afterprint.fn();
 assert.equal(frames[1].removed,true);
});

test('desktop PDF export uses the fixed shell channel instead of the frame',async()=>{
 const notices=[],requests=[],printed=[];
 const desktop={exportPdf:async request=>{requests.push(request);return {status:'saved',name:'季度报告.pdf'}}};
 const ctx=vm.createContext({window:{briefloopDesktop:desktop},notice:(...args)=>notices.push(args),printHtml:async html=>printed.push(html)});
 vm.runInContext(extract('exportPdf'),ctx);
 await ctx.exportPdf('<p>x</p>','季度报告');
 assert.equal(JSON.stringify(requests),JSON.stringify([{html:'<p>x</p>',title:'季度报告'}]));
 assert.deepEqual(notices,[['PDF 已保存：季度报告.pdf']]);
 assert.deepEqual(printed,[]);
 desktop.exportPdf=async()=>({status:'cancelled'});
 await ctx.exportPdf('<p>x</p>','季度报告');
 assert.equal(notices.length,1,'cancelling the save dialog is silent');
 desktop.exportPdf=async()=>{throw Error("Error invoking remote method 'report:export-pdf': Error: 正在导出另一份 PDF，请稍候。")};
 await assert.rejects(ctx.exportPdf('<p>x</p>','季度报告'),{message:'正在导出另一份 PDF，请稍候。'});
 ctx.window.briefloopDesktop={};
 await ctx.exportPdf('<p>browser</p>','x');
 assert.deepEqual(printed,['<p>browser</p>']);
});

test('numeric citations are renumbered with the reference list, named links are kept',()=>{
 const start=source.indexOf("for(const a of body.querySelectorAll('a[href^=\"#source-\"]')){");
 assert.ok(start>0);
 const loop=source.slice(start,source.indexOf('\n  }\n',start)+4);
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
