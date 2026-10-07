import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceViewerUI} from '../frontend/source-viewer.js';
import {createOfficeTools} from '../frontend/office-tools.js';

function fixture(t,{office=false}={}){
 const before=globalThis.document;
 const node=()=>({hidden:true,disabled:false,value:'1',children:[],querySelector:()=>null,
  replaceChildren(...children){this.children=children},append(...children){this.children.push(...children)}});
 globalThis.document={createElement:node};t.after(()=>globalThis.document=before);
 const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)};
 const replies=[];
 const api=(route,patch)=>new Promise(resolve=>replies.push({route,patch,resolve})),action=fn=>fn();
 const tools=office?createOfficeTools({$,api,action,getState:()=>({office:{installed:true,enabled:true}})}):undefined;
 const ui=sourceViewerUI({$,action,api,office:tools});
 const view=()=>({media:node(),images:node(),controls:node(),note:node(),pages:node(),render:node()});
 const pdf=id=>({source:{id,status:'ready',name:id+'.pdf'},attachment:{media_type:'application/pdf'}});
 return {ui,replies,view,pdf,$};
}

test('source preview buttons retain their own view and source',async t=>{
 const {ui,replies,view,pdf}=fixture(t),dialog=view(),drawer=view();
 ui.showSourceMedia(pdf('dialog-source'),dialog);ui.showSourceMedia(pdf('drawer-source'),drawer);
 const dialogLoad=dialog.render.onclick(),drawerLoad=drawer.render.onclick();
 assert.equal(replies[0].patch.source_id,'dialog-source');assert.equal(replies[1].patch.source_id,'drawer-source');
 replies[0].resolve({pages:[{page:1,url:'/dialog-page'}]});await dialogLoad;
 ui.resetSourceMedia(dialog);
 replies[1].resolve({pages:[{page:2,url:'/drawer-page'}]});await drawerLoad;
 assert.equal(dialog.images.children.length,0);assert.equal(drawer.images.children[0].children[1].src,'/drawer-page');
 assert.equal(drawer.render.disabled,false,'closing one view cannot invalidate the other');
});

test('a closed and reopened same-source preview ignores its earlier reply',async t=>{
 const {ui,replies,view,pdf}=fixture(t),panel=view();
 ui.showSourceMedia(pdf('same'),panel);const old=panel.render.onclick();
 await panel.render.onclick();assert.equal(replies.length,1,'repeated clicks do not duplicate a pending preview');
 ui.resetSourceMedia(panel);ui.showSourceMedia(pdf('same'),panel);
 panel.pages.value='3';const current=panel.render.onclick();
 replies[0].resolve({pages:[{page:1,url:'/old-page'}]});await old;
 assert.equal(panel.images.children.length,0);assert.equal(panel.render.disabled,true,'an obsolete request cannot enable the current loading button');
 replies[1].resolve({pages:[{page:3,url:'/current-page'}]});await current;
 assert.equal(panel.images.children[0].children[1].src,'/current-page');assert.equal(panel.render.disabled,false);
});

test('Office source previews also ignore replies from a closed selection',async t=>{
 const {ui,replies,view,$}=fixture(t,{office:true}),panel=view();
 const office=id=>({source:{id,status:'ready',name:id+'.docx'},attachment:{media_type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}});
 ui.showSourceMedia(office('old'),panel);const button=$('source-office-render'),old=button.onclick();
 ui.resetSourceMedia(panel);ui.showSourceMedia(office('current'),panel);const current=button.onclick();
 assert.equal(replies[0].patch.source_id,'old');assert.equal(replies[1].patch.source_id,'current');
 replies[0].resolve({pages:[{page:1,url:'/old-office-page'}]});await old;
 assert.equal(panel.images.children.length,0);assert.equal(button.disabled,true);
 replies[1].resolve({pages:[{page:1,url:'/current-office-page'}]});await current;
 assert.equal(panel.images.children[0].children[1].src,'/current-office-page');assert.equal(button.disabled,false);
});
