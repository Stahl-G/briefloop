import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

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
