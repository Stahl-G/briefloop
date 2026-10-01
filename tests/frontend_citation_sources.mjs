import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {getSchema} from '@tiptap/core';
import {MarkdownManager} from '@tiptap/markdown';
import StarterKit from '@tiptap/starter-kit';
import {EditorState} from '@tiptap/pm/state';
import {Citation,editorDocument,savedDocument} from '../frontend/rich-document.js';
import {citationNumbers,CitationPresentation,citationSourceCardHTML,sourceCardData,createCitationSources} from '../frontend/citation-sources.js';
import {createReportSources} from '../frontend/report-sources.js';
import {section} from './source_section.mjs';

const markdown='甲[@src_b]乙[@src_a]丙[@src_b]';
test('citation numbers belong to document order and remain identical for repeated sources regardless of library order',()=>{
 assert.deepEqual([...citationNumbers(markdown)],[['src_b',1],['src_a',2]]);
 const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const mapping=section(source,'// BEGIN_FIGURE_EDITOR_MAPPING','// END_FIGURE_EDITOR_MAPPING','frontend/app.js').split('\n').slice(1).join('\n');
 const context=vm.createContext({URL,window:{location:{origin:'http://localhost'}},state:{sources:[{id:'src_other'},{id:'src_a'},{id:'src_b'}]}});
 vm.runInContext(mapping+'\nthis.convert=toEditor;',context);
 const output=context.convert(markdown,'version');
 assert.equal(output,'甲[1](#source-src_b)乙[2](#source-src_a)丙[1](#source-src_b)');
 context.state.sources.reverse();assert.equal(context.convert(markdown,'version'),output);
});
test('actual Markdown parser converts citations to atoms, preserves ordinary links and edited label prose, and roundtrips canonically',()=>{
 const manager=new MarkdownManager({extensions:[StarterKit,Citation]});
 const doc=manager.parse('[1](#source-src_b) [2](#source-src_a) [1](#source-src_b) [正常链接](https://example.com) [新增文字](#source-src_a)');
 const schema=getSchema([StarterKit,Citation]);schema.nodeFromJSON(doc).check();
 assert.equal(doc.content[0].content.filter(node=>node.type==='citation').length,4);
 assert.match(manager.serialize(doc),/\[正常链接\]\(https:\/\/example.com\)/);
 assert.match(manager.serialize(doc),/新增文字\[@src_a\]/);
 assert.deepEqual([...citationNumbers('',doc)],[['src_b',1],['src_a',2]]);
});
test('legacy saved citation links normalize without losing prose, formatting, or introducing saved presentation metadata',()=>{
 const doc=editorDocument({type:'doc',content:[{type:'paragraph',content:[
  {type:'text',text:'8',marks:[{type:'link',attrs:{href:'#source-src_b'}}]},
  {type:'text',text:'新增中文',marks:[{type:'bold'},{type:'link',attrs:{href:'#source-src_a'}}]},
  {type:'citation',attrs:{sourceId:'src_b'}}]}]},'version');
 assert.deepEqual(doc.content[0].content.filter(node=>node.type==='citation').map(node=>node.attrs.label),[1,2,1]);
 assert.deepEqual(doc.content[0].content[1],{type:'text',text:'新增中文',marks:[{type:'bold'}]});
 assert.ok(savedDocument(doc).content[0].content.filter(node=>node.type==='citation').every(node=>node.attrs.label===undefined));
});
test('source decorations carry keyboard/source labels and never change the report document',()=>{
 const schema=getSchema([StarterKit,Citation]);
 const doc=schema.nodeFromJSON(editorDocument({type:'doc',content:[{type:'paragraph',content:[{type:'citation',attrs:{sourceId:'src_a'}},{type:'citation',attrs:{sourceId:'src_a'}}]}]},'version'));
 const plugins=CitationPresentation.config.addProseMirrorPlugins.call({options:{getSources:()=>[{id:'src_a',name:'原始公告'}]}});
 const state=EditorState.create({doc,plugins});
 const decorations=plugins[0].props.decorations(state).find();
 assert.equal(decorations.length,2);
 for(const decoration of decorations){assert.equal(decoration.type.attrs['aria-label'],'来源 1:原始公告');assert.equal(decoration.type.attrs.tabindex,'0');assert.equal(decoration.type.attrs['data-citation-number'],'1')}
 assert.equal(state.doc,doc);
});
test('source cards escape names, distinguish collection date, and disallow dangerous original URLs',()=>{
 const html=citationSourceCardHTML({id:'src_a',name:'<script>公告</script>',url:'https://example.com/a',created:'2026-10-01T00:00:00Z'},1,{locator:'第 3 页'});
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);assert.match(html,/收录 2026-10-01/);assert.match(html,/第 3 页/);assert.match(html,/rel="noopener noreferrer"/);
 assert.equal(sourceCardData({url:'javascript:alert(1)'}).href,'');
 assert.match(citationSourceCardHTML({url:'javascript:alert(1)'},2),/<button[^>]+data-citation-original>定位原文/);
});
test('clicking a citation reveals and focuses its numbered rail source beyond the eight-item preview and active filters',()=>{
 let html='',buttons=[],revealed=0,focused='',scrolled='';const filters=[];
 const box={get innerHTML(){return html},set innerHTML(value){html=value;buttons=[...value.matchAll(/data-rail-source="([^"]+)"/g)].map(match=>({dataset:{railSource:match[1]},focus(){focused=match[1]},scrollIntoView(){scrolled=match[1]}}))},querySelectorAll(selector){if(selector==='[data-rail-source-filter]')return filters;if(selector==='[data-rail-source]')return buttons;return []},querySelector(){return null}};
 const sources=Array.from({length:10},(_,index)=>({id:'src_'+index,name:'公告 '+index,status:index===9?'failed':'ready'}));
 const current={run_id:'r',markdown:'末项[@src_9] 首项[@src_0]'};
 const previous=globalThis.document;globalThis.document={querySelector:()=>null};
 try{
  filters.push({dataset:{railSourceFilter:'ready'}});
  const view=createReportSources({$:()=>box,esc:String,getState:()=>({sources,runs:[{id:'r'}]}),getCurrent:()=>current,runSourceIds:()=>sources.map(source=>source.id),sourceState:s=>s.status,sourceStatusChip:()=>'',sourceTitle:s=>s.name,sourceHost:()=>'',openSource(){},reveal(){revealed++}});
  view.render();assert.equal(buttons.length,8);filters[0].onclick();assert.doesNotMatch(html,/data-rail-source="src_9"/);
  assert.equal(view.focus('src_9'),true);assert.equal(revealed,1);assert.equal(focused,'src_9');assert.equal(scrolled,'src_9');
  assert.match(html,/aria-label="来源 1:公告 9"/);assert.match(html,/is-citation-target/);
 }finally{globalThis.document=previous}
});
test('hover/focus cards support keyboard entry, dismissal and source navigation without modifying editor nodes',()=>{
 const hostEvents={},windowEvents={},docEvents={};let card,focused='',opened='',jumped='';
 const doc={activeElement:null,body:{append(node){card=node}},addEventListener(name,handler){docEvents[name]=handler},createElement(){
  const listeners={},controls={};
  const node={dataset:{},hidden:true,style:{},innerHTML:'',setAttribute(){},addEventListener(name,handler){listeners[name]=handler},contains(target){return Object.values(controls).includes(target)},getBoundingClientRect(){return {width:300,height:150}},querySelector(selector){
   if(!controls[selector])controls[selector]={listeners:{},addEventListener(name,handler){this.listeners[name]=handler},focus(){focused=selector;doc.activeElement=this}};
   return controls[selector];
  },listeners};
  return node;
 }};
 const win={innerWidth:1000,innerHeight:800,addEventListener(name,handler){windowEvents[name]=handler}};
 const anchor={dataset:{citationSource:'src_a',citationNumber:'1'},textContent:'1',isConnected:true,getAttribute:()=>null,closest(){return this},getBoundingClientRect(){return {left:80,top:100,bottom:128}},focus(){focused='citation';doc.activeElement=this;hostEvents.focusin({target:this})}};
 const original=JSON.stringify(anchor.dataset);
 const view=createCitationSources({element:{addEventListener(name,handler){hostEvents[name]=handler}},getSources:()=>[{id:'src_a',name:'本地公告',created:'2026-10-01'}],getCurrent:()=>({detail:'{}'}),focusSource:id=>{jumped=id},openSource:id=>{opened=id},document:doc,window:win});
 hostEvents.pointerover({target:anchor});assert.equal(card.hidden,false);assert.match(card.innerHTML,/本地公告/);
 let prevented=false;hostEvents.keydown({target:anchor,key:'Tab',shiftKey:false,preventDefault(){prevented=true}});
 assert.equal(prevented,true);assert.equal(focused,'[data-citation-original]');
 card.listeners.keydown({key:'Escape',preventDefault(){}});assert.equal(card.hidden,true);assert.equal(focused,'citation');
 hostEvents.focusin({target:anchor});card.querySelector('[data-citation-original]').listeners.click();assert.equal(opened,'src_a');assert.equal(card.hidden,true);
 hostEvents.focusin({target:anchor});hostEvents.keydown({target:anchor,key:'Enter',preventDefault(){}});assert.equal(jumped,'src_a');assert.equal(card.hidden,true);
 hostEvents.pointerover({target:anchor});view.hide();assert.equal(card.hidden,true);
 assert.equal(JSON.stringify(anchor.dataset),original,'popover interaction does not edit the ProseMirror DOM');
});
