// Presentation-only colors: never write inferred colors into the saved draft.
import {Extension} from '@tiptap/core';
import {Plugin,PluginKey} from '@tiptap/pm/state';
import {Decoration,DecorationSet} from '@tiptap/pm/view';
import {semanticDeltaSpans} from './market-convention.js';

function columnContext(doc,pos){
 const resolved=doc.resolve(pos+1);
 for(let depth=resolved.depth;depth>1;depth--){
  const cell=resolved.node(depth);if(!['tableCell','tableHeader'].includes(cell.type.name))continue;
  const row=resolved.node(depth-1),table=resolved.node(depth-2),rowIndex=resolved.index(depth-2);
  if(table.type.name!=='table'||rowIndex===0)return '';
  // Without a physical grid map, merged cells make row-local column indexes
  // ambiguous. Explicit delta phrases still work; skip header inference.
  let merged=false;table.forEach(row=>row.forEach(cell=>{if((cell.attrs.rowspan||1)>1||(cell.attrs.colspan||1)>1)merged=true}));if(merged)return '';
  const cellIndex=resolved.index(depth-1);let column=0;for(let index=0;index<cellIndex;index++)column+=row.child(index).attrs.colspan||1;
  let headerColumn=0;for(const header of table.firstChild.content.content){const end=headerColumn+(header.attrs.colspan||1);if(column<end)return header.type.name==='tableHeader'?header.textContent:'';headerColumn=end}
 }
 return '';
}
export function marketDecorations(doc){
 const decorations=[];
 doc.descendants((node,pos)=>{
  if(!node.isTextblock||node.type.name==='codeBlock')return;
  const text=node.textBetween(0,node.content.size,'','\uFFFC'),spans=semanticDeltaSpans(text,{context:columnContext(doc,pos)});
  node.forEach((child,offset)=>{
   if(!child.isText||child.marks.some(mark=>mark.type.name==='code'||mark.type.name==='textStyle'&&mark.attrs.color))return;
   for(const [start,end,direction] of spans){const from=Math.max(start,offset),to=Math.min(end,offset+child.nodeSize);if(from<to)decorations.push(Decoration.inline(pos+1+from,pos+1+to,{class:`data-delta data-${direction}`,'data-direction':direction}))}
  });
 });
 return DecorationSet.create(doc,decorations);
}
export const MarketDataColors=Extension.create({name:'marketDataColors',addProseMirrorPlugins(){return [new Plugin({key:new PluginKey('marketDataColors'),props:{decorations:state=>marketDecorations(state.doc)}})]}});
