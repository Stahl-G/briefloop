import {Extension, Mark, Node, mergeAttributes} from '@tiptap/core';
import Image from '@tiptap/extension-image';
import {Plugin} from '@tiptap/pm/state';

function color(value){
 if(/^#[a-f\d]{6}$/i.test(value||''))return value.toLowerCase();
 const rgb=/^rgb\(\s*(\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)\s*\)$/.exec(value||'');
 return rgb?'#'+rgb.slice(1).map(n=>Math.min(255,Number(n)).toString(16).padStart(2,'0')).join(''):null;
}
export const TextStyle=Mark.create({name:'textStyle',
 addAttributes(){return {color:{default:null,parseHTML:el=>color(el.style.color),renderHTML:a=>a.color?{style:'color:'+a.color}:{}}}},
 parseHTML(){return [{tag:'span',getAttrs:el=>el.style.color?{}:false}]},
 renderHTML({HTMLAttributes}){return ['span',mergeAttributes(HTMLAttributes),0]},
 renderMarkdown(node,helpers){return helpers.renderChildren(node)},
});
export const Layout=Extension.create({name:'reportLayout',
 addGlobalAttributes(){return [
  {types:['paragraph','heading','image','table','tableCell','tableHeader'],attributes:{
   textAlign:{default:null,parseHTML:el=>el.style.textAlign||null,renderHTML:a=>a.textAlign?{style:'text-align:'+a.textAlign}:{}},
   blockId:{default:null,parseHTML:el=>el.getAttribute('data-block-id'),renderHTML:a=>a.blockId?{'data-block-id':a.blockId}:{}}
  }},
  {types:['tableCell','tableHeader'],attributes:{
   backgroundColor:{default:null,parseHTML:el=>color(el.style.backgroundColor),renderHTML:a=>a.backgroundColor?{style:'background-color:'+a.backgroundColor}:{}},
   textAlign:{default:null,parseHTML:el=>el.style.textAlign||null,renderHTML:a=>a.textAlign?{style:'text-align:'+a.textAlign}:{}}
  }}
 ]},
});
export const ReportImage=Image.extend({
 addAttributes(){return {...this.parent?.(),width:{default:null,parseHTML:el=>Number(el.getAttribute('width'))||null},height:{default:null,parseHTML:el=>Number(el.getAttribute('height'))||null},caption:{default:null,parseHTML:el=>el.getAttribute('data-caption'),renderHTML:a=>a.caption?{'data-caption':a.caption}:{}}}},
 renderHTML({node,HTMLAttributes}){return ['figure',{class:'report-image'},['img',mergeAttributes(this.options.HTMLAttributes,HTMLAttributes)],...(node.attrs.caption?[['figcaption',{},node.attrs.caption]]:[])]},
 renderMarkdown(node){const a=node.attrs;return '!['+(a.alt||'').replaceAll(']','\\]')+']('+a.src+')'+(a.caption?'\n\n'+a.caption:'')},
});
export const Citation=Node.create({name:'citation',group:'inline',inline:true,atom:true,
 addAttributes(){return {sourceId:{default:null},label:{default:null}}},
 parseHTML(){return [{tag:'a[data-citation]',getAttrs:el=>({sourceId:el.getAttribute('data-citation')})}]},
 renderHTML({node}){return ['a',{'data-citation':node.attrs.sourceId,href:'#source-'+node.attrs.sourceId,class:'citation'},'['+(node.attrs.label||1)+']']},
 renderMarkdown(node){return '[@'+node.attrs.sourceId+']'},
 addProseMirrorPlugins(){return [new Plugin({appendTransaction(transactions,oldState,state){
  if(!transactions.some(t=>t.docChanged)||transactions.some(t=>t.getMeta('citationNumbering')))return null;
  const ids=[],tr=state.tr;let changed=false;
  state.doc.descendants((node,pos)=>{if(node.type.name!=='citation')return;const sid=node.attrs.sourceId;if(!ids.includes(sid))ids.push(sid);const label=ids.indexOf(sid)+1;if(node.attrs.label!==label){tr.setNodeMarkup(pos,undefined,{...node.attrs,label});changed=true}});
  return changed?tr.setMeta('citationNumbering',true).setMeta('addToHistory',false):null;
 }})]},
});

function mapImages(document,convert){
 const out=structuredClone(document);
 function walk(node){if(node.type==='image')node.attrs={...node.attrs,src:convert(node.attrs.src)};for(const child of node.content||[])walk(child)}
 walk(out);return out;
}
export function editorDocument(document,version){
 const output=mapImages(document,src=>src.startsWith('briefloop-figure:')?'/api/figure?id='+encodeURIComponent(src.split(':')[1])+'&version='+encodeURIComponent(version):src);
 const ids=[];function number(node){if(node.type==='citation'){const sid=node.attrs.sourceId;if(!ids.includes(sid))ids.push(sid);node.attrs.label=ids.indexOf(sid)+1}for(const child of node.content||[])number(child)}number(output);return output;
}
export function savedDocument(document){
 const output=mapImages(document,src=>{
  const url=new URL(src,window.location.origin);
  return url.origin===window.location.origin&&url.pathname==='/api/figure'?'briefloop-figure:'+url.searchParams.get('id'):src;
 });
 function clear(node){if(node.type==='citation')delete node.attrs.label;for(const child of node.content||[])clear(child)}clear(output);return output;
}
