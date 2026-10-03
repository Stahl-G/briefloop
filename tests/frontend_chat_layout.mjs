import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {section} from './source_section.mjs';

// Use an installed headless Chromium, without opening a window or a model/server.
const chromium=process.env.BRIEFLOOP_CHROMIUM_PATH;
test('long conversations keep their composer visible when the task rail appears', {skip:!chromium,timeout:30000},async t=>{
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-chat-layout-'));
 t.after(()=>fs.rm(directory,{recursive:true,force:true}));
 let html=await fs.readFile(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
 html=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
 for(const name of ['tokens.css','style.css']){
  await fs.copyFile(new URL('../src/briefloop/static/'+name,import.meta.url),path.join(directory,name));
  html=html.replace('href="/'+name+'"','href="'+name+'"');
 }
 const script=`
 const $=id=>document.getElementById(id),chat=$('chat'),scroll=$('chat-scroll'),composer=$('chat-form'),rail=$('home-rail');
 document.querySelectorAll('main>section').forEach(section=>section.hidden=section!==chat);
 chat.classList.remove('is-empty');
 $('chat-home-top').hidden=$('chat-home-bottom').hidden=true;
 $('chat-activity').hidden=true;
 $('chat-messages').innerHTML=Array.from({length:60},(_,i)=>'<article class="chat-message"><p>第 '+i+' 条：'+'用于验证长对话布局的合成消息。'.repeat(4)+'</p></article>').join('');
 rail.innerHTML='<section class="home-rail-block"><h3>运行中的任务</h3><p>合成任务：验证布局，不执行模型。</p></section>';
 const results=[];
 for(const drawer of [false,true])for(const visible of [false,true,false]){
  document.body.classList.toggle('report-chat-open',drawer);
  chat.classList.toggle('has-home-rail',visible);rail.hidden=!visible;
  scroll.scrollTop=0;
  const bounds=composer.getBoundingClientRect(),input=$('chat-input').getBoundingClientRect();
  scroll.scrollTop=scroll.scrollHeight;
  results.push({drawer,rail:visible,width:innerWidth,height:innerHeight,top:bounds.top,bottom:bounds.bottom,
   inputBottom:input.bottom,afterScroll:composer.getBoundingClientRect().bottom,
   scrollHeight:scroll.scrollHeight,clientHeight:scroll.clientHeight});
 }
 const output=document.createElement('pre');output.id='layout-result';output.textContent=JSON.stringify(results);document.body.append(output);
 `;
 await fs.writeFile(path.join(directory,'index.html'),html+'<script>'+script+'</script>');
 for(const [width,height] of [[1720,1000],[1000,760],[600,700]]){
  const {stdout}=await promisify(execFile)(chromium,['--headless','--no-sandbox','--disable-gpu','--no-first-run',
   '--user-data-dir='+path.join(directory,'profile-'+width),'--window-size='+width+','+height,'--dump-dom',
   pathToFileURL(path.join(directory,'index.html')).href],{timeout:8000,maxBuffer:2*1024*1024});
  const result=stdout.match(/<pre id="layout-result">([^<]+)<\/pre>/);
  assert.ok(result,'Chromium must return measured layout results');
  for(const row of JSON.parse(result[1])){
   const detail=JSON.stringify(row);
   assert.ok(row.top>=0&&row.bottom<=row.height+1,detail);
   assert.ok(row.inputBottom<=row.height+1,detail);
   assert.ok(row.clientHeight>0&&row.scrollHeight>row.clientHeight,detail);
   assert.equal(row.afterScroll,row.bottom,detail);
  }
 }
});

test('report drawers cover the sticky toolbar and keep their close controls reachable', {skip:!chromium,timeout:30000},async t=>{
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-drawer-layout-'));
 t.after(()=>fs.rm(directory,{recursive:true,force:true}));
 let html=await fs.readFile(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
 html=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
 for(const name of ['tokens.css','style.css']){
  await fs.copyFile(new URL('../src/briefloop/static/'+name,import.meta.url),path.join(directory,name));
  html=html.replace('href="/'+name+'"','href="'+name+'"');
 }
 const app=await fs.readFile(new URL('../frontend/app.js',import.meta.url),'utf8');
 const drawerCode=section(app,'function setReportChatOpen(','function openReportChat(','frontend/app.js');
 const script=`
 const $=id=>document.getElementById(id),chat=$('chat'),report=$('report'),bar=document.querySelector('.report-topbar');
 document.querySelectorAll('main>section').forEach(section=>section.hidden=section!==report);
 $('empty').hidden=true;$('document-area').hidden=false;
 $('report-title').textContent='合成报告：检查顶部工具栏与对话侧栏';
 $('editor').innerHTML='<h2>合成报告</h2>'+('<p>仅用于检查页面布局，不执行模型任务。</p>'.repeat(35));
 chat.classList.remove('is-empty');$('chat-home-top').hidden=$('chat-home-bottom').hidden=true;
 $('chat-title').textContent='报告对话';
 $('chat-messages').innerHTML='<article class="chat-message assistant"><p>对话侧栏的标题、关闭按钮和输入框应保持可用。</p></article>';
 ${drawerCode}
 const hit=id=>{const e=$(id),r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
 const results=[];
 for(const type of ['chat','source']){
  const drawer=type==='chat'?chat:$('source-drawer'),backdrop=$(type==='chat'?'report-chat-backdrop':'source-drawer-backdrop');
  setReportChatOpen(true);
  if(type==='source'){drawer.hidden=false;backdrop.hidden=false;}
  const close=$(type==='chat'?'report-chat-close':'source-drawer-close');
  close.onclick=()=>{if(type==='chat')setReportChatOpen(false);else{drawer.hidden=true;backdrop.hidden=true;}};
  const br=bar.getBoundingClientRect(),dr=drawer.getBoundingClientRect();
  const x=(Math.max(br.left,dr.left)+Math.min(br.right,dr.right))/2,y=(Math.max(br.top,dr.top)+Math.min(br.bottom,dr.bottom))/2;
  const under=document.elementFromPoint(x,y);
  const outsideX=(br.left+Math.min(br.right,dr.left))/2;
  const outside=dr.left>br.left?document.elementFromPoint(outsideX,y):null;
  const reachable=hit(close.id),cr=close.getBoundingClientRect();
  if(reachable)document.elementFromPoint(cr.x+cr.width/2,cr.y+cr.height/2).click();
  const closed=drawer.hidden&&backdrop.hidden;
  // A source opened over the conversation must return to that conversation.
  const parentRestored=type==='chat'||hit('report-chat-close');
  setReportChatOpen(false);
  results.push({type,width:innerWidth,drawerAboveToolbar:drawer.contains(under),outsideCovered:outside?outside===backdrop:true,
   reachable,closed,parentRestored,topbarRestored:bar.contains(document.elementFromPoint((br.left+br.right)/2,y))});
  setReportChatOpen(false);$('source-drawer').hidden=$('source-drawer-backdrop').hidden=true;
 }
 const output=document.createElement('pre');output.id='drawer-result';output.textContent=JSON.stringify(results);document.body.append(output);
 `;
 await fs.writeFile(path.join(directory,'index.html'),html+'<script>'+script+'</script>');
 for(const [width,height] of [[1720,1000],[1000,760],[520,700]]){
  const {stdout}=await promisify(execFile)(chromium,['--headless','--no-sandbox','--disable-gpu','--no-first-run',
   '--user-data-dir='+path.join(directory,'profile-'+width),'--window-size='+width+','+height,'--dump-dom',
   pathToFileURL(path.join(directory,'index.html')).href],{timeout:8000,maxBuffer:2*1024*1024});
  const result=stdout.match(/<pre id="drawer-result">([^<]+)<\/pre>/);
  assert.ok(result,'Chromium must return hit-test results');
  for(const row of JSON.parse(result[1])){
   assert.ok(row.drawerAboveToolbar&&row.outsideCovered&&row.reachable&&row.closed&&row.parentRestored&&row.topbarRestored,JSON.stringify(row));
  }
 }
});
