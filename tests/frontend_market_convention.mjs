import assert from 'node:assert/strict';
import {getSchema} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {TableKit} from '@tiptap/extension-table';
import {TextStyle} from '../frontend/rich-document.js';
import {resolveMarket,semanticDeltaSpans,createMarketConvention} from '../frontend/market-convention.js';
import {marketDecorations} from '../frontend/market-decorations.js';

assert.equal(resolveMarket({language:'English'}),'intl');
assert.equal(resolveMarket({language:'zh'}),'cn');
assert.equal(resolveMarket({language:'en',market_convention:'cn'}),'cn');
for(const [text,context,expected] of [
 ['营收 100，利润 -20，利润率 -5%，存款 +25','',[]],
 ['revenue 100; net margin -5%; balance +25','',[]],
 ['+5%','',[]],
 ['同比 +5%，环比 −2.0%，增长 0%','',[['+5%','up'],['−2.0%','down'],['0%','flat']]],
 ['Revenue grew 5%; margin decreased by 1.2 percentage points','',[['5%','up'],['1.2 percentage points','down']]],
 ['↑ 4.5% / ▼2%','',[['↑ 4.5%','up'],['▼2%','down']]],
 ['-1,200','YoY change',[['-1,200','down']]],
 ['  +1.5%  ','环比',[['+1.5%','up']]],
 ['-25','余额',[]],['-5%','Profit margin',[]],
])assert.deepEqual(semanticDeltaSpans(text,{context}).map(([a,b,d])=>[text.slice(a,b),d]),expected);

const schema=getSchema([StarterKit,TableKit,TextStyle]);
const paragraph=(text,marks)=>({type:'paragraph',content:[{type:'text',text,...(marks?{marks}:{})}]});
const tableCell=(type,text)=>({type,content:[paragraph(text)]});
const document=schema.nodeFromJSON({type:'doc',content:[
 paragraph('收入 -20，利润率 -5%；增长 5%，下跌 2%'),
 paragraph('增长 7%',[{type:'textStyle',attrs:{color:'#123456'}}]),
 {type:'table',content:[{type:'tableRow',content:[tableCell('tableHeader','同比'),tableCell('tableHeader','余额')]},{type:'tableRow',content:[tableCell('tableCell','-3%'),tableCell('tableCell','-500')]}]},
]});
const before=JSON.stringify(document.toJSON());
assert.deepEqual(marketDecorations(document).find().map(item=>[document.textBetween(item.from,item.to),item.type.attrs['data-direction']]),[['5%','up'],['2%','down'],['-3%','down']]);
assert.equal(JSON.stringify(document.toJSON()),before,'market decorations must never mutate saved content');
const mergedDocument=schema.nodeFromJSON({type:'doc',content:[{type:'table',content:[
 {type:'tableRow',content:[tableCell('tableHeader','同比'),tableCell('tableHeader','余额')]},
 {type:'tableRow',content:[{...tableCell('tableCell','5%'),attrs:{rowspan:2}},tableCell('tableCell','100')]},
 {type:'tableRow',content:[tableCell('tableCell','-200')]},
 {type:'tableRow',content:[tableCell('tableCell','增长 3%'),tableCell('tableCell','-500')]},
]}]});
assert.deepEqual(marketDecorations(mergedDocument).find().map(item=>mergedDocument.textBetween(item.from,item.to)),['3%'],'merged grids must not infer balance cells as delta columns; explicit delta labels remain eligible');
console.log('PASS: editor and export semantic direction excludes arbitrary signed money/margins');

// Small UI harness exercises persistence failure and navigation while saving.
const nodes={};
class Element{
 constructor(){this.value='';this.dataset={};this.listeners={};this.options=[];this.disabled=false;this.textContent=''}
 set id(value){this._id=value;nodes[value]=this}get id(){return this._id}
 set innerHTML(value){this.html=value;this.options=[...value.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(match=>({value:match[1],textContent:match[2]}));if(value.includes('id="report-market-convention"')){node('report-market-convention');node('report-market-status')}}
 append(element){this.child=element}after(element){this.afterElement=element}closest(){return this}
 addEventListener(event,handler){this.listeners[event]=handler}remove(){delete nodes[this.id]}
}
const node=id=>nodes[id]||Object.assign(new Element(),{id});
globalThis.document={createElement:()=>new Element()};
node('report');node('assistant-summary');node('report-language').value='zh';
let state={workspace_id:'w',runs:[{id:'a',requirements:'{"language":"zh"}'},{id:'b',requirements:'{"language":"en"}'}]},current={run_id:'a'},deferred,calls=0;
const notices=[];
const ui=createMarketConvention({$:id=>nodes[id],getState:()=>state,getCurrent:()=>current,notice:text=>notices.push(text),api:async(path,body)=>{assert.equal(path,'report-settings');calls++;return new Promise((resolve,reject)=>{deferred={resolve,reject,body}})}});
ui.init();assert.equal(nodes['setup-market-convention'].name,'market_convention');assert.match(nodes['setup-market-convention'].options[0].textContent,/红涨绿跌/);
node('report-language').value='en';ui.syncLanguage();assert.match(nodes['setup-market-convention'].options[0].textContent,/绿涨红跌/);
const automatic={market_convention:''};ui.prepare(automatic);assert.deepEqual(automatic,{});
ui.restore('cn');assert.equal(nodes['setup-market-convention'].value,'cn');
ui.render();assert.equal(node('report').dataset.market,'cn');
let select=node('report-market-convention');select.value='intl';let saving=select.listeners.change();
assert.equal(select.disabled,true);await select.listeners.change();assert.equal(calls,1,'double-click cannot submit twice');
current={run_id:'b'};ui.render();assert.equal(node('report').dataset.market,'intl');assert.equal(select.disabled,false);
deferred.resolve({requirements:{language:'zh',market_convention:'intl'}});await saving;
assert.equal(node('report').dataset.market,'intl');assert.equal(notices.length,0,'stale report saves must not announce success on a different report');
current={run_id:'a'};ui.render();assert.equal(select.value,'intl');
select.value='cn';saving=select.listeners.change();deferred.reject(new Error('保存失败'));await saving;
assert.equal(select.value,'intl');assert.equal(select.disabled,false);assert.match(notices.at(-1),/保存失败/);
select.value='cn';saving=select.listeners.change();state={workspace_id:'next',runs:[]};current=null;ui.render();deferred.resolve({requirements:{market_convention:'cn'}});await saving;
assert.equal(nodes['report-market-settings'],undefined,'workspace navigation removes settings and ignores late responses');
state={workspace_id:'w',runs:[{id:'a',requirements:'{"language":"zh"}'},{id:'b',requirements:'{"language":"en"}'}]};current={run_id:'a'};ui.render();select=node('report-market-convention');select.value='intl';const saveA=select.listeners.change(),deferredA=deferred;
current={run_id:'b'};ui.render();select.value='cn';const saveB=select.listeners.change(),deferredB=deferred;
current={run_id:'a'};ui.render();assert.equal(select.disabled,true);const submitted=calls;await select.listeners.change();assert.equal(calls,submitted,'returning to A while B saves must retain A duplicate-submit protection');
deferredB.resolve({requirements:{language:'en',market_convention:'cn'}});await saveB;assert.equal(select.disabled,true);
deferredA.resolve({requirements:{language:'zh',market_convention:'intl'}});await saveA;assert.equal(select.disabled,false);assert.equal(node('report').dataset.market,'intl');
console.log('PASS: report settings save/reload, duplicate submissions, rollback and stale navigation');
