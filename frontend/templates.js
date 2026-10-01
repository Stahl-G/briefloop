// The templates page: one builtin card per genre, theme dots and the apply bar.
import {$,esc} from './dom.js';
export const GENRE_ORDER=['商业报告','券商研报','学术论文','会议纪要','合同','上市公司年报','政府公文','通用报告','英文通用报告','英文研报'];
// Categories name a colour from the token palette; the tiles take it from
// the .cat-* class, so the hex lives in tokens.css only.
export const GENRE_META={
 '商业报告':{desc:'适用于商业分析、市场研究等。',icon:'briefcase',cat:'cat-business'},
 '券商研报':{desc:'适用于证券研究、行业分析。',icon:'chart',cat:'cat-markets'},
 '学术论文':{desc:'适用于学术研究、论文写作。',icon:'book',cat:'cat-academic'},
 '会议纪要':{desc:'适用于会议记录、讨论要点。',icon:'users',cat:'cat-collab'},
 '合同':{desc:'适用于各类合同、协议。',icon:'file',cat:'cat-business'},
 '上市公司年报':{desc:'适用于上市公司年度报告。',icon:'bars',cat:'cat-business'},
 '政府公文':{desc:'适用于政府机关公文、政策文件；红头与字体按 GB/T 9704 固定。',icon:'landmark',cat:'cat-markets'},
 '通用报告':{desc:'适用于各类通用型报告。',icon:'layers',cat:'cat-neutral'},
 '英文通用报告':{desc:'英文正文与章节标题，适用于面向海外读者的报告；选用后报告语言设为英文。',icon:'layers',cat:'cat-neutral'},
 '英文研报':{desc:'英文证券研究版式，适用于面向海外投资者的公司与行业研究；选用后报告语言设为英文。',icon:'chart',cat:'cat-markets'},
};
export const ICONS={
 briefcase:'<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
 // Filled rounded bars matching home suggestion tiles (DESIGN §5 / settings icon set)
 chart:'<rect x="4" y="12" width="4" height="8" rx="1.2"/><rect x="10" y="6" width="4" height="14" rx="1.2"/><rect x="16" y="9" width="4" height="11" rx="1.2"/>',
 book:'<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
 users:'<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
 file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
 bars:'<rect x="4" y="12" width="4" height="8" rx="1.2"/><rect x="10" y="6" width="4" height="14" rx="1.2"/><rect x="16" y="9" width="4" height="11" rx="1.2"/>',
 // Comparison: two column pairs for competitor benchmarking
 compare:'<rect x="3" y="10" width="3.5" height="10" rx="1"/><rect x="7.5" y="6" width="3.5" height="14" rx="1"/><rect x="13" y="13" width="3.5" height="7" rx="1"/><rect x="17.5" y="8" width="3.5" height="12" rx="1"/>',
 landmark:'<line x1="3" y1="22" x2="21" y2="22"/><line x1="5" y1="22" x2="5" y2="11"/><line x1="9" y1="22" x2="9" y2="11"/><line x1="15" y1="22" x2="15" y2="11"/><line x1="19" y1="22" x2="19" y2="11"/><path d="M2 11L12 3l10 8z"/>',
 layers:'<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
 paperclip:'<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
 // Two buildings separated by a slash — competitor / dual-entity compare (Downloads icon ref)
 buildingsSlash:'<path d="M4 21V9.5L9 6v15"/><path d="M4 21h7"/><line x1="6.2" y1="11" x2="7.8" y2="11"/><line x1="6.2" y1="14" x2="7.8" y2="14"/><line x1="6.2" y1="17" x2="7.8" y2="17"/><line x1="13.2" y1="5.5" x2="17.8" y2="18.5"/><path d="M14 21v-9.5L18.5 8.5V21"/><path d="M14 21h7"/><line x1="16" y1="14" x2="17.5" y2="14"/><line x1="16" y1="17" x2="17.5" y2="17"/>',
};
const THEME_COLORS={'品牌黛蓝':'#2448B8','极简蓝':'#2563EB','珊瑚红':'#C62828','石墨黑':'#1E2320','典雅灰':'#8A9089'};
const THEME_ORDER=Object.keys(THEME_COLORS);
export function splitTemplateName(name){const i=name.lastIndexOf('·');return i<0?{genre:name,theme:''}:{genre:name.slice(0,i),theme:name.slice(i+1)}}
// Reinstalled builtins can retain earlier hashes. Show one ready, newest
// choice for each named style without deleting any saved template records.
export function visibleBuiltinTemplates(templates){
 const groups=new Map();
 for(const template of templates.filter(t=>t.origin==='builtin'&&t.name.includes('·'))){
  const {genre,theme}=splitTemplateName(template.name),key=genre+'\0'+theme,old=groups.get(key);
  const compare=(a,b)=>Number(a.status==='ready')-Number(b.status==='ready')
   ||Number(a.revision||0)-Number(b.revision||0)
   ||String(a.created||'').localeCompare(String(b.created||''))
   ||String(a.id).localeCompare(String(b.id));
  if(!old||compare(template,old)>0)groups.set(key,template);
 }
 return [...groups.values()].map(t=>({...t,...splitTemplateName(t.name)}));
}
export function templatesUI({notice,action,page,renderWorkflowChoices,templateSections,getState,syncTemplateLanguage,openTemplateOutput}){
 let templatePick=null,workspace=null;
 function renderTemplatesPage(){
  const state=getState(),box=$('templates-page-list');if(!box||!state)return;
  if(workspace!==state.workspace_id){workspace=state.workspace_id;templatePick=null;renderTemplatesPage.sig=''}
  const list=state.templates||[],builtins=visibleBuiltinTemplates(list),mine=list.filter(t=>t.origin!=='builtin'),choices=[...builtins,...mine];
  const sig=JSON.stringify([workspace,state.settings?.default_template_id,templatePick,...list.map(t=>[t.id,t.status,t.revision,t.created,t.name,t.origin,t.error])]);
  if(renderTemplatesPage.sig===sig)return;renderTemplatesPage.sig=sig;
  if(!choices.some(t=>t.id===templatePick&&t.status==='ready')){
   const saved=list.find(t=>t.id===state.settings?.default_template_id),sameStyle=saved&&builtins.find(t=>t.name===saved.name&&t.status==='ready');
   templatePick=(choices.find(t=>t.id===saved?.id&&t.status==='ready')||sameStyle||builtins.find(t=>t.genre==='商业报告'&&t.theme==='品牌黛蓝'&&t.status==='ready')||choices.find(t=>t.status==='ready'))?.id||null;
  }
  const genres={};for(const item of builtins)(genres[item.genre]??=[]).push(item);
  for(const items of Object.values(genres))items.sort((a,b)=>THEME_ORDER.indexOf(a.theme)-THEME_ORDER.indexOf(b.theme));
  const order=[...GENRE_ORDER,...Object.keys(genres).filter(g=>!GENRE_ORDER.includes(g))];
  const cards=order.filter(g=>genres[g]).map(genre=>{
   const meta=GENRE_META[genre]||{desc:'',icon:'file',cat:'cat-neutral'},items=genres[genre],selected=items.some(t=>t.id===templatePick);
   const preferred=items.find(t=>t.id===templatePick)||items.find(t=>t.status==='ready')||items[0];
   const dots=items.map(it=>`<button type="button" class="color-dot${preferred.id===it.id?' selected':''}" style="--swatch-color:${THEME_COLORS[it.theme]||'var(--color-text-muted)'}" data-genre="${esc(genre)}" data-theme="${esc(it.theme)}" data-id="${esc(it.id)}" data-testid="template-theme" title="${esc(genre+' · '+it.theme)}" aria-label="${esc(genre+' '+it.theme)}" aria-pressed="${it.id===templatePick}" ${it.status==='ready'?'':'disabled'}></button>`).join('');
   return `<div class="tpl-card${selected?' selected':''}" data-genre="${esc(genre)}"><span class="tpl-check" aria-hidden="true">✓</span>`
    +`<span class="tpl-icon ${meta.cat}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[meta.icon]||''}</svg></span>`
    +`<button type="button" class="tpl-name tpl-select" data-template-pick="${esc(preferred.id)}" aria-pressed="${selected}" ${items.some(t=>t.status==='ready')?'':'disabled'}>${esc(genre)}</button><span class="tpl-desc" title="${esc(meta.desc)}">${esc(meta.desc)}</span>`
    +`<span class="tpl-dots"><span class="label">配色</span>${dots}</span></div>`;
  }).join('');
  const picked=choices.find(t=>t.id===templatePick),pickedLabel=picked?`已选：<strong>${esc(picked.name.replace('·',' · '))}</strong>`:'尚无可用模板';
  const mineRows=mine.length?`<div class="tpl-custom"><h2>我的版式模板</h2>`+mine.map(t=>`<div class="source-row tpl-custom-row${templatePick===t.id?' selected':''}"><span class="name">${esc(t.name)} · v${esc(t.revision)}</span><span class="tag ${t.status!=='ready'?'error':''}">${t.status==='ready'?'可用':esc(t.error||'准备中')}</span><button type="button" class="outline" data-template-pick="${esc(t.id)}" data-testid="custom-template-select" aria-pressed="${templatePick===t.id}" ${t.status==='ready'?'':'disabled'}>${templatePick===t.id?'已选择':'选择模板'}</button></div>`).join('')+'</div>':'';
  box.innerHTML=(cards?`<div class="tpl-grid">${cards}</div>`:'')+mineRows+(list.length?`<div class="tpl-bar tpl-output-bar"><span class="picked">${pickedLabel}</span><div class="tpl-output-actions"><button type="button" id="template-use-upload" data-testid="template-use-upload" class="primary" ${picked?'':'disabled'}>上传原稿套用</button><button type="button" id="template-use-report" data-testid="template-use-report" class="outline" ${picked?'':'disabled'}>从已有报告套用</button><button type="button" id="template-new-report" data-testid="template-new-report" class="ghost" ${picked?'':'disabled'}>按模板新建报告</button></div><p class="help">上传原稿或选择已有报告，按所选版式生成 Word；新建报告会进入写作设置。</p></div>`:'<p class="help">还没有模板。添加一个 Word 作为版式模板。</p>');
  const pick=id=>{if(!choices.some(t=>t.id===id&&t.status==='ready'))return;templatePick=id;renderTemplatesPage.sig='';renderTemplatesPage()};
  box.querySelectorAll('.tpl-card').forEach(card=>card.onclick=event=>{if(event.target.closest('button'))return;const items=genres[card.dataset.genre]||[];pick((items.find(t=>t.id===templatePick)||items.find(t=>t.status==='ready'))?.id)});
  box.querySelectorAll('[data-template-pick]').forEach(button=>button.onclick=()=>pick(button.dataset.templatePick));
  box.querySelectorAll('.color-dot').forEach(dot=>dot.onclick=event=>{event.stopPropagation();pick(dot.dataset.id)});
  for(const [id,mode] of [['template-use-upload','upload'],['template-use-report','report']]){const button=$(id);if(button)button.onclick=()=>{if(templatePick)openTemplateOutput?.(templatePick,mode)}}
  const create=$('template-new-report');if(create)create.onclick=()=>action(async()=>{
   const template=(getState().templates||[]).find(t=>t.id===templatePick&&t.status==='ready');if(!template)return;
   $('template-select').value=template.id;renderWorkflowChoices();
   templateSections();syncTemplateLanguage?.(template);
   notice(`新建报告已选择 ${template.name}`);page('setup');
  });
 }
 return {render:renderTemplatesPage};
}
