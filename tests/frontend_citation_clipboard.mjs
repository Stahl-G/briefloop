import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {build} from 'esbuild';

test('actual Electron clipboard paste excludes inherited citation marks but preserves intentional links', {skip:process.platform!=='win32',timeout:20000}, async t=>{
 const root=fileURLToPath(new URL('../',import.meta.url));
 let electron;
 try{electron=createRequire(new URL('../desktop/electron/package.json',import.meta.url))('electron');await fs.access(electron)}
 catch{t.skip('Install desktop Electron dependencies for the native clipboard regression');return}
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-clipboard-'));
 t.after(()=>fs.rm(directory,{recursive:true,force:true}));
 const resultFile=path.join(directory,'result.json');
 await build({stdin:{resolveDir:root,contents:`
import {getSchema} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {EditorState,TextSelection} from '@tiptap/pm/state';
import {EditorView} from '@tiptap/pm/view';
import {citationBoundaryPlugin} from './frontend/rich-document.js';
window.runClipboardTests=()=>{
 const schema=getSchema([StarterKit]),results=[];
 const paste=(href,position,html)=>{
  const doc=schema.node('doc',null,[schema.node('paragraph',null,[schema.text('12',[schema.marks.link.create({href})])])]);
  const holder=document.body.appendChild(document.createElement('div'));
  const view=new EditorView(holder,{state:EditorState.create({doc,plugins:[citationBoundaryPlugin()]})});
  view.focus();view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,position)));
  const data=new DataTransfer();data.setData('text/plain',' 粘贴中文 67890');
  if(html)data.setData('text/html',html);
  const event=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});
  view.dom.dispatchEvent(event);
  if(!event.defaultPrevented)throw Error('Real editor paste handler did not consume the event');
  const output=view.state.doc.toJSON();view.destroy();holder.remove();return output.content[0].content;
 };
 const plain=paste('#source-src_test',3);
 if(plain.at(-1).text!==' 粘贴中文 67890'||plain.at(-1).marks?.length)throw Error('Plain paste inherited citation');
 results.push('plain citation boundary');
 const ordinary=paste('https://example.com',3);
 if(!ordinary.at(-1).marks.some(m=>m.attrs.href==='https://example.com'))throw Error('Ordinary link changed');
 results.push('ordinary link');
 const inside=paste('#source-src_test',2);
 if(!inside[0].text.includes('粘贴中文')||!inside[0].marks.some(m=>m.attrs.href==='#source-src_test'))throw Error('Interior citation edit changed');
 results.push('interior citation');
 const rich=paste('#source-src_test',3,'<a href="#source-src_intentional">主动引用链接</a>');
 if(!rich.at(-1).marks.some(m=>m.attrs.href==='#source-src_intentional')||rich.at(-1).text!=='主动引用链接')throw Error('Rich clipboard link changed');
 results.push('intentional rich link');return results;
};
`},bundle:true,format:'iife',outfile:path.join(directory,'browser.js')});
 await fs.writeFile(path.join(directory,'index.html'),'<meta charset="utf-8"><script src="browser.js"></script>','utf8');
 await fs.writeFile(path.join(directory,'main.cjs'),`
const {app,BrowserWindow}=require('electron'),fs=require('node:fs'),path=require('node:path');
app.setPath('userData',path.join(__dirname,'profile'));app.disableHardwareAcceleration();
app.whenReady().then(async()=>{
 const window=new BrowserWindow({show:false,webPreferences:{contextIsolation:true,sandbox:true}});
 await window.loadFile(path.join(__dirname,'index.html'));
 const results=await window.webContents.executeJavaScript('window.runClipboardTests()');
 fs.writeFileSync(${JSON.stringify(resultFile)},JSON.stringify(results),'utf8');window.destroy();app.quit();
}).catch(error=>{fs.writeFileSync(${JSON.stringify(resultFile)},JSON.stringify({error:error.message}),'utf8');app.exit(1)});
`,'utf8');
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const child=spawn(electron,[path.join(directory,'main.cjs')],{env,windowsHide:true,stdio:'ignore'});
 const timer=setTimeout(()=>child.kill(),15000);
 const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',resolve)}).finally(()=>clearTimeout(timer));
 const result=JSON.parse(await fs.readFile(resultFile,'utf8'));
 assert.equal(code,0,JSON.stringify(result));
 assert.deepEqual(result,['plain citation boundary','ordinary link','interior citation','intentional rich link']);
});
