import {Extension} from '@tiptap/core';
import {Plugin,PluginKey} from '@tiptap/pm/state';
import {Decoration,DecorationSet} from '@tiptap/pm/view';
import {esc} from './dom.js';

const sourcePattern=/^#source-(src_[A-Za-z0-9_-]+)$/;
export function citationNumbers(markdown='',document=null){
 const ids=[],add=id=>{if(id&&!ids.includes(id))ids.push(id)};
 if(document){
  const walk=node=>{if(node.type==='citation')add(node.attrs?.sourceId);for(const mark of node.marks||[])add(sourcePattern.exec(mark.attrs?.href||'')?.[1]);for(const child of node.content||[])walk(child)};
  walk(document);
 }
 if(!document)for(const match of String(markdown).matchAll(/\\?\[@(src\\?_[A-Za-z0-9_-]+)\\?\]|\]\(#source-(src_[A-Za-z0-9_-]+)\)/g))add((match[1]||match[2]).replaceAll('\\',''));
 return new Map(ids.map((id,index)=>[id,index+1]));
}
export function sourceCardData(source={},fallback={}){
 const url=source.url||'';let host='本地资料',href='';
 try{const parsed=new URL(url);if(['https:','http:'].includes(parsed.protocol)){host=parsed.hostname.replace(/^www\./,'');href=parsed.href}}catch{}
 const published=source.published_at||source.publication_date||fallback.published_at;
 const date=published||source.created||'';
 return {title:source.name||fallback.source_title||source.id||'来源信息待补',name:source.publisher||host,
  date:date?`${published?'发布':'收录'} ${String(date).slice(0,10)}`:'日期未记录',href};
}
export function citationSourceCardHTML(source,number,fallback={}){
 const data=sourceCardData(source,fallback);
 return `<p class="citation-card-meta">来源 ${esc(number)} · ${esc(data.name)} · ${esc(data.date)}</p><p class="citation-card-title">${esc(data.title)}</p>${fallback.locator?`<p class="citation-card-locator">${esc(fallback.locator)}</p>`:''}<div class="citation-card-actions">${data.href?`<a href="${esc(data.href)}" target="_blank" rel="noopener noreferrer" data-citation-original>定位原文 ↗</a>`:'<button type="button" class="link" data-citation-original>定位原文</button>'}<button type="button" class="ghost" data-citation-rail>来源与数据</button><button type="button" class="ghost" data-citation-close aria-label="关闭来源卡">关闭</button></div>`;
}

// Accessibility/presentation only: decorations never enter editor JSON, saved
// Markdown, undo history, or exports, and they cannot make a report dirty.
export const CitationPresentation=Extension.create({name:'citationPresentation',
 addOptions(){return {getSources:()=>[]}},
 addProseMirrorPlugins(){
  const key=new PluginKey('citationPresentation'),getSources=this.options.getSources;
  const build=doc=>{
   const sources=new Map(getSources().map(source=>[source.id,source])),ids=[],decorations=[];
   doc.descendants((node,pos)=>{
    if(node.type.name!=='citation')return;
    const id=node.attrs.sourceId;if(!ids.includes(id))ids.push(id);const number=ids.indexOf(id)+1;
    decorations.push(Decoration.node(pos,pos+node.nodeSize,{'data-citation-source':id,'data-citation-number':String(number),'aria-label':`来源 ${number}:${sources.get(id)?.name||id}`,tabindex:'0',role:'button','aria-haspopup':'dialog'}));
   });
   return DecorationSet.create(doc,decorations);
  };
  return [new Plugin({key,state:{init:(_,state)=>build(state.doc),apply:(tr,previous)=>tr.docChanged?build(tr.doc):previous},props:{decorations:state=>key.getState(state)}})];
 }
});

export function createCitationSources({element,getSources,getCurrent,focusSource,openSource,document:doc=globalThis.document,window:win=globalThis.window}){
 let card=null,anchor=null,hideTimer=null,returnFocus=null,suppressFocus=false;
 const sourceOf=id=>getSources().find(source=>source.id===id)||{id};
 const citation=target=>target?.closest?.('[data-citation-source],a[data-citation],a[href^="#source-"]');
 const idOf=node=>node?.dataset.citationSource||node?.dataset.citation||sourcePattern.exec(node?.getAttribute('href')||'')?.[1];
 const cancelHide=()=>{clearTimeout(hideTimer);hideTimer=null};
 function hide({restore=false}={}){cancelHide();if(card)card.hidden=true;const target=returnFocus;anchor=null;returnFocus=null;if(restore&&target?.isConnected){suppressFocus=true;target.focus();suppressFocus=false}}
 function scheduleHide(){cancelHide();hideTimer=setTimeout(()=>{if(card?.contains(doc.activeElement)||anchor===doc.activeElement)return;hide()},180)}
 function place(){
  if(!anchor||!card||card.hidden)return;
  const rect=anchor.getBoundingClientRect(),bounds=card.getBoundingClientRect(),space=8;
  card.style.left=Math.max(space,Math.min(rect.left,win.innerWidth-bounds.width-space))+'px';
  card.style.top=Math.max(space,rect.bottom+bounds.height+space<=win.innerHeight?rect.bottom+space:rect.top-bounds.height-space)+'px';
 }
 function show(node){
  const id=idOf(node);if(!id)return;cancelHide();anchor=node;returnFocus=node;
  if(!card){card=doc.createElement('div');card.className='citation-source-card';card.dataset.testid='citation-source-card';card.setAttribute('role','dialog');card.setAttribute('aria-label','引用来源');card.addEventListener('pointerenter',cancelHide);card.addEventListener('pointerleave',scheduleHide);card.addEventListener('focusout',scheduleHide);card.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();hide({restore:true})}});doc.body.append(card)}
  const current=getCurrent();let detail={};try{detail=typeof current?.detail==='string'?JSON.parse(current.detail):current?.detail||{}}catch{}
  const ref=(detail.citations||[]).find(item=>item.source_id===id)||{};
  const number=node.dataset.citationNumber||node.textContent.replace(/[^\d]/g,'')||'?';
  card.innerHTML=citationSourceCardHTML(sourceOf(id),number,ref);card.hidden=false;
  card.querySelector('[data-citation-original]').addEventListener('click',()=>{if(!sourceCardData(sourceOf(id)).href)openSource(id);hide()});
  card.querySelector('[data-citation-rail]').addEventListener('click',()=>{hide();focusSource(id)});
  card.querySelector('[data-citation-close]').addEventListener('click',()=>hide({restore:true}));place();
 }
 element.addEventListener('pointerover',event=>{const node=citation(event.target);if(node&&node!==anchor)show(node)});
 element.addEventListener('pointerout',event=>{if(citation(event.target)&&!card?.contains(event.relatedTarget))scheduleHide()});
 element.addEventListener('focusin',event=>{if(suppressFocus)return;const node=citation(event.target);if(node)show(node)});
 element.addEventListener('focusout',event=>{if(!card?.contains(event.relatedTarget))scheduleHide()});
 element.addEventListener('click',event=>{const node=citation(event.target);if(node){event.preventDefault();hide();focusSource(idOf(node))}});
 element.addEventListener('keydown',event=>{
  const node=citation(event.target);if(!node)return;
  if(event.key==='Escape'){event.preventDefault();hide()}
  if(event.key===' '||event.key==='Enter'){event.preventDefault();hide();focusSource(idOf(node))}
  if(event.key==='ArrowDown'||(event.key==='Tab'&&!event.shiftKey&&card&&!card.hidden)){event.preventDefault();card.querySelector('[data-citation-original]')?.focus()}
 });
 doc.addEventListener('pointerdown',event=>{if(card&&!card.hidden&&!card.contains(event.target)&&!citation(event.target))hide()});
 win.addEventListener('resize',()=>hide());win.addEventListener('scroll',event=>{if(!card?.contains(event.target))hide()},true);
 return {hide};
}
