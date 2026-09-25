// OfficeCLI is an optional local enhancement. Detection only says the binary
// exists; the switch defaults to off and every entry point here hides itself
// when it is off, so the page behaves exactly as before without the tool.
// Wording keeps the honesty line of the runtime cards: never claim a check
// passed or the tool was "接入".

export function officeIssueLine(item){
 // officecli issue objects are stored as-is; no field is guaranteed.
 const text=[item.message,item.description,item.summary,item.title,item.text]
  .find(value=>typeof value==='string'&&value);
 return (text||item.type||'未提供摘要')+(typeof item.location==='string'&&item.location?' · '+item.location:'');
}

export function createOfficeTools(deps){
 const {api,action,$,esc,parse,getState,notice}=deps;

 function officeEnabled(){
  const officeState=getState()?.office;
  return !!(officeState&&officeState.installed&&officeState.enabled);
 }

 function syncSettingsToggle(){
  // Constant-shape state hint only; matches the card before discovery runs.
  const officeState=getState()?.office;
  const toggle=$('settings-officecli');if(!toggle)return;
  toggle.disabled=!officeState?.installed;
  toggle.checked=!!(officeState?.installed&&officeState.enabled);
 }

 function renderSettingsCapability(capability){
  const cap=capability||{};
  const toggle=$('settings-officecli');
  if(toggle){toggle.disabled=!cap.installed;toggle.checked=!!(cap.installed&&cap.enabled)}
  const status=$('settings-officecli-status');if(!status)return;
  if(!cap.installed){
   status.textContent=(typeof cap.diagnostic==='string'&&cap.diagnostic?cap.diagnostic:'未检测到 OfficeCLI；已查找 PATH 与常见安装目录')
    +' 开关保持关闭；安装后重新检测即可开启，未安装或关闭时导出与界面行为不变。';
   return;
  }
  status.textContent=`已检测到本机 OfficeCLI${cap.version?' · 版本 '+cap.version:''}；${cap.enabled
   ?'开关已开启：导出 Word 后自动运行结构校验与质检，Word/Excel/PowerPoint 文件可用本地渲染查看页面；质检失败只记录结果，不影响导出。'
   :'开关当前关闭：不运行质检，也不显示页面预览。'}检测只确认二进制存在，不代表质检已经运行或可用。`;
 }

 function parsePages(value){
  const raw=String(value??'').trim();
  if(!/^\d+(?:\s*[,，]\s*\d+)*$/.test(raw))throw Error('请输入页码，例如 1 或 1,3');
  const pages=[...new Set(raw.split(/[,，]/).map(Number))];
  if(pages.some(p=>p<1)||pages.length>4)throw Error('每次请选择 1–4 页，页码从 1 开始');
  return pages;
 }

 function pageFigure(page){
  const figure=document.createElement('figure');
  const caption=document.createElement('figcaption');caption.textContent='第 '+page.page+' 页';
  const img=document.createElement('img');img.className='source-preview-image';img.alt=caption.textContent;img.src=page.url;
  figure.append(caption,img);return figure;
 }

 function paintResult(result,images,note){
  images.replaceChildren();
  for(const page of (result.pages||[]))images.append(pageFigure(page));
  note.textContent=result.incomplete
   ?'部分页未渲染：请求预算用尽或渲染失败，仅显示已完成页面。'
   :'已用本机 OfficeCLI 完成本地渲染。';
 }

 function openPreview(target){
  if(!officeEnabled())return;
  const dialog=$('office-preview-dialog');if(!dialog)return;
  const images=$('office-preview-images'),note=$('office-preview-note'),render=$('office-preview-render'),pages=$('office-preview-pages');
  if(images)images.replaceChildren();
  if(pages)pages.value='1';
  if(note)note.textContent='输入 1–4 个页码；本地渲染，不调用模型，预览失败不影响文件本身。';
  if(!dialog.open)dialog.showModal();
  if(!render)return;
  render.onclick=()=>action(async()=>{
   const list=parsePages(pages?pages.value:'');
   render.disabled=true;
   try{const result=await api('office-preview',{...target,pages:list});if(images&&note)paintResult(result,images,note)}
   finally{render.disabled=false}
  });
 }

 async function previewSourceInto(id,view,trigger){
  if(!id||!view)throw Error('请先选择要查看的来源');
  const pages=parsePages(view.pages?view.pages.value:'');
  if(trigger)trigger.disabled=true;
  try{
   const result=await api('office-preview',{source_id:id,pages});
   if(view.images)paintResult(result,view.images,view.note||{textContent:''});
  }finally{if(trigger)trigger.disabled=false}
 }

 function officeCheckSummary(record){
  if(!record)return '';
  const validate=record.validate||{},issues=record.issues||{};
  if(validate.status==='error'||issues.status==='error')return 'OfficeCLI 质检未完成：'+(validate.reason||issues.reason||'原因未记录')+'（不影响导出）';
  const found=Number(issues.count)||0;
  if(validate.status!=='ok')return 'OfficeCLI 校验未通过：'+(validate.summary||validate.reason||'无摘要');
  return found?'OfficeCLI 质检发现 '+found+' 项':'OfficeCLI 质检通过';
 }

 return {officeEnabled,syncSettingsToggle,renderSettingsCapability,openPreview,previewSourceInto,officeCheckSummary};
}
