import test from 'node:test';
import assert from 'node:assert/strict';
import {popoverBounds,anchoredPopover} from '../frontend/anchored-popover.js';
import {createComposerOptions} from '../frontend/composer-options.js';

for(const [name,anchor,viewport,size] of [
 ['home screenshot expanded above top',{top:340,bottom:380,right:710},{left:0,top:0,width:1360,height:900},{width:360,height:700}],
 ['extreme short zoom',{top:30,bottom:70,right:180},{left:0,top:0,width:200,height:100},{width:360,height:700}],
 ['short window',{top:170,bottom:210,right:400},{left:0,top:0,width:500,height:300},{width:360,height:700}],
 ['narrow window',{top:300,bottom:330,right:160},{left:0,top:0,width:240,height:500},{width:360,height:700}],
 ['zoomed and panned visual viewport',{top:220,bottom:250,right:520},{left:150,top:110,width:320,height:380},{width:360,height:700}],
 ['bottom composer',{top:730,bottom:760,right:990},{left:0,top:0,width:1000,height:800},{width:360,height:350}],
])test(name+' keeps the complete scrollable box in view',()=>{
 const r=popoverBounds(anchor,size,viewport);
 assert.ok(r.left>=viewport.left+8);assert.ok(r.top>=viewport.top+8);assert.ok(r.left+r.width<=viewport.left+viewport.width-8);
 assert.ok(r.top+Math.min(size.height,r.maxHeight)<=viewport.top+viewport.height-8);assert.ok(r.maxHeight>0);
});
function fixture(){
 const listeners=new Map();const register=(prefix)=>(name,handler)=>{const key=prefix+name;if(!listeners.has(key))listeners.set(key,[]);listeners.get(key).push(handler)};
 let focus='',visible=true,observed;const observedNodes=[],form={},page={};
 const trigger={closest:selector=>selector==='form'?form:page,rect:{top:200,bottom:230,right:450},getBoundingClientRect(){return this.rect},getClientRects:()=>visible?[{}]:[],setAttribute(name,value){this[name]=value},contains:t=>t===trigger,focus(){focus='trigger'}};
 const first={focus(){focus='first'}};
 const panel={hidden:true,style:{},scrollHeight:650,clientHeight:300,scrollTop:55,classList:{remove(name){panel.removed=name}},setAttribute(){},contains:t=>t===panel||t===first,querySelector:()=>first,addEventListener:register('panel:'),getBoundingClientRect(){return {width:Math.min(360,parseFloat(this.style.maxWidth)||360),height:302}}};
 const view={offsetLeft:0,offsetTop:0,width:500,height:400,addEventListener:register('view:')};
 const win={innerWidth:500,innerHeight:400,visualViewport:view,addEventListener:register('win:')};
 const doc={body:{append(node){assert.equal(node,panel)}},querySelectorAll:()=>[],addEventListener:register('doc:')};
 class Observer{constructor(callback){observed=callback}observe(node){observedNodes.push(node)}}
 const ui=anchoredPopover({trigger,panel,document:doc,window:win,ResizeObserver:Observer});
 const emit=(key,event={})=>(listeners.get(key)||[]).forEach(fn=>fn(event));
 return {ui,trigger,panel,view,emit,observedNodes,form,page,focus:()=>focus,resize:()=>observed(),hideTrigger:()=>visible=false,listeners};
}
test('repositions for viewport zoom, details expansion and parent scrolling without scrolling away content',()=>{
 const h=fixture();assert.ok(h.observedNodes.includes(h.form));assert.ok(h.observedNodes.includes(h.page));h.ui.open();assert.equal(h.panel.removed,'popover');assert.equal(h.panel.scrollTop,0);assert.equal(h.focus(),'first');
 h.panel.scrollTop=80;h.view.width=280;h.view.height=300;h.emit('view:resize');assert.equal(h.panel.style.maxWidth,'264px');assert.equal(h.panel.scrollTop,80);
 h.panel.scrollHeight=900;h.emit('panel:toggle');h.resize();assert.ok(parseFloat(h.panel.style.maxHeight)<=284);
 h.trigger.rect={top:60,bottom:90,right:250};h.resize();assert.equal(h.panel.style.top,'98px');
 h.hideTrigger();h.resize();assert.equal(h.panel.hidden,true);assert.equal(h.trigger['aria-expanded'],'false');
});
test('inside clicks remain open, outside pointer/tab dismiss, Escape restores focus across reopens',()=>{
 const h=fixture(),count=h.listeners.size;h.ui.open();h.emit('doc:pointerdown',{target:h.panel});assert.equal(h.panel.hidden,false);
 let prevented=false;h.emit('doc:keydown',{key:'Escape',preventDefault(){prevented=true}});assert.equal(prevented,true);assert.equal(h.panel.hidden,true);assert.equal(h.focus(),'trigger');
 h.ui.open();h.emit('doc:focusin',{target:{}});assert.equal(h.panel.hidden,true);
 h.ui.open();h.emit('doc:pointerdown',{target:{}});assert.equal(h.panel.hidden,true);assert.equal(h.listeners.size,count);
 h.trigger.onclick({stopPropagation(){}});assert.equal(h.panel.hidden,false);h.trigger.onclick({stopPropagation(){}});assert.equal(h.panel.hidden,true);
});
test('research mount removes engine/effort carriers and permission action, retains sources and research choices',()=>{
 class Node{
  constructor(){this.children=[];this.dataset={};this.hidden=false;this.classList={add(){}};this.nodes=new Map();this.innerHTML=''}
  append(node){if(node.parentElement)node.parentElement.children=node.parentElement.children.filter(n=>n!==node);this.children.push(node);node.parentElement=this}
  before(node){const parent=this.parentElement;if(node.parentElement)node.parentElement.children=node.parentElement.children.filter(n=>n!==node);parent.children.splice(parent.children.indexOf(this),0,node);node.parentElement=parent}
  querySelector(selector){return this.nodes.get(selector)||null}querySelectorAll(selector){return this.nodes.get(selector)||[]}setAttribute(){}dispatchEvent(){}
 }
 const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new Node());return elements.get(id)};
 const panel=$('panel'),grid=new Node(),actions=new Node(),wrapper=new Node(),toolbar=new Node();toolbar.append(wrapper);wrapper.append($('composer-params'));panel.append(grid);panel.append(actions);
 panel.nodes.set('.composer-params-grid',grid);panel.nodes.set('.composer-params-row',actions);grid.append($('chat-backend'));grid.append($('chat-effort'));grid.append($('chat-service-tier'));actions.append($('chat-permissions-open'));actions.append($('attach-existing'));
 let root;const doc={createElement(tag){const n=new Node();if(tag==='section'){root=n;for(const selector of ['[data-completion]','[data-research-tier]','[data-research-strategy]','[data-report-fact]','[data-fact-link]','[data-report-mode-note]'])n.nodes.set(selector,new Node());n.nodes.set('[data-report-source]',[])}return n}};
 const ui=createComposerOptions({$,getChat:()=>({}),getState:()=>({settings:{}}),availability:()=>({enabled:true}),rememberDraft(){},openSettings(){},openReview(){},document:doc});ui.mount(panel);
 assert.equal(grid.parentElement.id,'chat-runtime-carriers');assert.equal(grid.parentElement.hidden,true);assert.equal(grid.parentElement.parentElement,$('chat-form'));assert.equal($('chat-permissions-open').parentElement,toolbar);
 assert.equal($('chat-service-tier').parentElement.parentElement,$('settings-model-block'));assert.equal($('chat-service-tier').parentElement.textContent,'当前对话速度');assert.equal($('attach-existing').parentElement,actions);assert.equal(actions.parentElement,root);
 assert.doesNotMatch(root.innerHTML,/模型与执行|data-learning-settings|chat-backend|chat-effort/);assert.match(root.innerHTML,/研究深度/);
});
