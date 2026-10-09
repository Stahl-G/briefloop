// Source viewer: the source dialog with its links and provenance, and the original-file
// media (PDF pages, images, OfficeCLI previews) shared with the source drawer.
import {$ as lookup,esc} from './dom.js';
import {moment} from './time.js';
export function sourceViewerUI({api,action,office,$=lookup}){
 function sourceOriginalLink(result){
  const provenance=result.provenance||{};
  return {label:provenance.original_kind==='provider_response'?'下载 Tavily 返回内容 ↗':'下载原件 ↗',href:result.original_url||''};
 }
 function applySourceLinks(result,els){
  const source=result.source||{};
  if(els.link){els.link.textContent=els.linkLabel||source.url||'';els.link.title=source.url||'';els.link.href=source.url||'#';els.link.hidden=!source.url}
  if(els.original){const original=sourceOriginalLink(result);els.original.textContent=original.label;els.original.hidden=!original.href;if(original.href){els.original.href=original.href;els.original.download=source.name||''}else els.original.removeAttribute('href')}
 }
 function sourceProvenanceRows(result){
  const provenance=result.provenance||{},rows=[];
  if(provenance.fetched_at)rows.push(['抓取时间',moment(provenance.fetched_at)||String(provenance.fetched_at)])
  if(provenance.content_type)rows.push(['内容类型',String(provenance.content_type)]);
  return rows;
 }
 function showSourceProvenance(result,el){if(!el)return;const rows=sourceProvenanceRows(result);el.innerHTML=rows.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');el.hidden=!rows.length}
 function showSource(result){
  applySourceLinks(result,{link:$('source-link'),original:$('source-original')});
  showSourceMedia(result);
  $('source-title').textContent=(result.source||{}).name||'来源';$('source-body').textContent=(result.source||{}).error||result.text||'';
  showSourceProvenance(result,$('source-provenance'));
  $('source-dialog').showModal();
 }

 const mediaSelections=new WeakMap();
 const dialogSourceMediaView=()=>({media:$('source-media'),images:$('source-images'),controls:$('source-page-controls'),note:$('source-media-note'),pages:$('source-pages'),render:$('source-pages-render')});
 const drawerSourceMediaView=()=>({media:$('source-drawer-media'),images:$('source-drawer-images'),controls:$('source-drawer-page-controls'),note:$('source-drawer-media-note'),pages:$('source-drawer-pages'),render:$('source-drawer-pages-render')});
 function resetSourceMedia(view){view=view||dialogSourceMediaView();if(!view.media)return;mediaSelections.delete(view.media);view.media.hidden=true;view.images.replaceChildren();if(view.render){view.render.disabled=false;view.render.hidden=false}const label=view.controls?.querySelector?.('label');if(label)label.textContent='查看 PDF 页码';const officeButton=officeMediaButton(view);if(officeButton){officeButton.hidden=true;officeButton.disabled=false}}
 function officeMediaButton(view){
  // The drawer has a static control; the source dialog gets an equivalent one lazily.
  if(!view.media)return null;
  if(view.media.id==='source-drawer-media')return $('source-drawer-office-render');
  let button=$('source-office-render');
  if(!button){button=document.createElement('button');button.type='button';button.className='outline';button.id='source-office-render';button.hidden=true;button.textContent='OfficeCLI 查看页面（本地渲染，不调用模型）';view.controls?.after?.(button)}
  return button;
 }
 function showSourceMedia(result,view){
  view=view||dialogSourceMediaView();
  const media=result.attachment;
  if(!media||result.source?.status!=='ready'){resetSourceMedia(view);return}
  const isPDF=media.media_type==='application/pdf',isImage=media.media_type?.startsWith('image/');
  // OfficeCLI page preview is an optional enhancement: without the switch an
  // Office original renders exactly like today, with no extra controls.
  const isOfficeFile=typeof office!=='undefined'&&office.officeEnabled()&&/\.(docx|xlsx|pptx)$/i.test(result.source?.name||'');
  if(!isPDF&&!isImage&&!isOfficeFile){resetSourceMedia(view);return}
  resetSourceMedia(view);const selection={id:result.source.id};mediaSelections.set(view.media,selection);
  const isCurrent=()=>mediaSelections.get(view.media)===selection&&!view.media.hidden;
  view.media.hidden=false;
  if(isOfficeFile){
   view.controls.hidden=false;view.pages.value='1';
   const label=view.controls?.querySelector?.('label');if(label)label.textContent='查看页码（1–4）';
   if(view.render)view.render.hidden=true;
   const officeButton=officeMediaButton(view);if(officeButton){officeButton.hidden=false;officeButton.onclick=()=>action(()=>office.previewSourceInto(selection.id,view,officeButton,isCurrent))}
   view.note.textContent='Office 原件可用 OfficeCLI 本地渲染页面查看；本地渲染，不调用模型，预览失败不影响文件本身。';
   return;
  }
  view.controls.hidden=!isPDF;view.pages.value='1';
  view.note.textContent=isPDF?`PDF 原件可用${media.pages?'，共 '+media.pages+' 页':''}。正文提取不覆盖所有图表，可按页查看。`:'原图已保存；发送给支持视觉的模型时会作为图片输入，不冒充 OCR 文本。';
  if(isImage&&result.image_url){const img=document.createElement('img');img.src=result.image_url;img.alt=result.source.name||'来源图片';img.className='source-preview-image';view.images.append(img)}
  if(view.render)view.render.onclick=()=>action(()=>renderSourcePages(selection.id,view,isCurrent));
 }
 async function renderSourcePages(id,view,isCurrent){
  if(!id||!view||!isCurrent()||view.render.disabled)return;const value=view.pages.value.trim();if(!/^\d+(?:\s*[,，]\s*\d+)*$/.test(value))throw Error('请输入页码，例如 1 或 1,3');
  const pages=[...new Set(value.split(/[,，]/).map(Number))];if(pages.some(p=>p<1)||pages.length>4)throw Error('每次请选择 1–4 页，页码从 1 开始');
  view.render.disabled=true;
  try{const result=await api('source-pages',{source_id:id,pages});if(!isCurrent())return;view.images.replaceChildren();
   for(const page of result.pages){const figure=document.createElement('figure');const caption=document.createElement('figcaption');caption.textContent='第 '+page.page+' 页';const img=document.createElement('img');img.className='source-preview-image';img.alt=caption.textContent;img.src=page.url;figure.append(caption,img);view.images.append(figure)}
  }finally{if(isCurrent())view.render.disabled=false}
 }
 function init(){
  $('source-dialog').addEventListener('close',()=>resetSourceMedia());
 }
 return {init,showSource,applySourceLinks,sourceProvenanceRows,resetSourceMedia,showSourceMedia,drawerSourceMediaView};
}
