import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source=fs.readFileSync('frontend/app.js','utf8');
const code=source.slice(source.indexOf('async function uploadChatFiles('),source.indexOf("document.querySelectorAll('[data-prompt]')"));
const elements=new Map();
const el=id=>{
 if(!elements.has(id))elements.set(id,{handlers:{},classList:{add(){},remove(){}},addEventListener(name,handler){this.handlers[name]=handler}});
 return elements.get(id);
};
const chat={busy:false,uploading:0,session:{runtime:{backend:'codex'},lifecycle:'active'},attachments:new Set()};
const runtimeCatalog=[{id:'codex',capabilities:{images:true}}];
const uploads=[],errors=[];
const context=vm.createContext({$:el,chat,runtimeCatalog,state:{settings:{agent_backend:'codex'}},selected:new Set(),File,Uint8Array,String,Date,btoa,
 chatError(message){if(message)errors.push(message)},updateComposer(){},runtimeName:name=>name,
 async api(route,payload){assert.equal(route,'upload');uploads.push(payload);return {id:'source_'+uploads.length,status:'ready'}},
 async refresh(){},renderAttachments(){},rememberDraft(){}});
vm.runInContext(code,context);
const file=new File(['Synthetic source'],'source.txt',{type:'text/plain'});
const drop=()=>el('chat-form').handlers.drop({dataTransfer:{files:[file]},preventDefault(){}});

// Read-only textarea/form events still fire: the upload entry point owns this
// guard, otherwise sendChat can clear a file absent from its frozen payload.
chat.busy=true;
drop();
await vm.runInContext('uploadChatFiles([])',context);
assert.equal(uploads.length,0,'sending must reject file drops before any upload');
assert.equal(chat.attachments.size,0);
assert.ok(errors.length,'rejected attachment has a visible explanation');

chat.busy=false;chat.session.lifecycle='archived';errors.length=0;
await vm.runInContext('uploadChatFiles([new File(["text"],"source.txt")])',context);
assert.equal(uploads.length,0,'archived sessions cannot accept new attachments');
assert.ok(errors.length);

chat.session.lifecycle='active';
el('chat-input').handlers.paste({clipboardData:{items:[{kind:'string'}]},preventDefault(){throw Error('ordinary text paste was consumed')}});
await vm.runInContext('uploadChatFiles([new File(["text"],"source.txt")])',context);
assert.equal(uploads.length,1);
assert.equal(chat.attachments.size,1);

runtimeCatalog[0].capabilities.images=false;errors.length=0;
await vm.runInContext('uploadChatFiles([new File(["pixels"],"image.png",{type:"image/png"})])',context);
assert.equal(uploads.length,1,'image capability restriction also protects new upload entry points');
assert.ok(errors.length);
assert.equal(chat.uploading,0);
console.log('PASS: busy/archived attachment guards, active file upload, native text paste and image capability restriction');
