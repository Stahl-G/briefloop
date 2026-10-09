// A next-period shortcut prepares an ordinary, unsent conversation. The server
// resolves the saved version; the browser never supplies historical facts.
export function nextReportChatUI({$,getChat,openChatHome,rememberDraft,updateComposer,notice,document:doc=globalThis.document}){
 let card=null;
 function sync(){
  const context=getChat().nextReportContext;
  if(!card){
   card=doc.createElement('section');card.className='panel next-report-context';
   card.setAttribute('aria-label','下一期报告约定');
   const title=doc.createElement('strong'),note=doc.createElement('p'),cancel=doc.createElement('button');
   note.className='help';cancel.type='button';cancel.className='ghost';cancel.textContent='取消关联';
   cancel.onclick=()=>{getChat().nextReportContext=null;rememberDraft();sync()};
   const agreements=doc.createElement('details');agreements.setAttribute('aria-label','本期写作约定');
   card.append(title,note,cancel,agreements);$('chat-input').before(card);
  }
  card.hidden=!context;
  if(context){card.children[0].textContent='下一期 · '+context.title;
   card.children[1].textContent='沿用已保存的读者和结构。补充本期时间、变化或材料；写作约定会在新任务开始时核对。';
   const box=card.children[3],entries=context.writing_agreements||[];box.replaceChildren();box.hidden=!entries.length;
   const summary=doc.createElement('summary');summary.textContent='本期写作约定 · '+entries.length;box.append(summary);
   for(const item of entries){
    const label=doc.createElement('label'),input=doc.createElement('input'),text=doc.createElement('span');label.className='check';input.type='checkbox';
    input.checked=!(context.writing_agreement_exclusions||[]).includes(item.id);text.textContent=item.text;
    input.onchange=()=>{const excluded=new Set(context.writing_agreement_exclusions||[]);input.checked?excluded.delete(item.id):excluded.add(item.id);context.writing_agreement_exclusions=[...excluded];rememberDraft()};
    label.append(input,text);box.append(label);
   }
   const help=doc.createElement('p');help.className='help';help.textContent='取消勾选只跳过本期，不撤销后续期的约定。';box.append(help);
  }
 }
 async function open(data){
  await openChatHome();
  getChat().nextReportContext={version_id:data.previous.version_id,hash:data.previous.hash,title:data.previous.title,writing_agreements:data.writing_agreements||[],writing_agreement_exclusions:[]};
  const input=$('chat-input');
  if(!input.value.trim())input.value='请基于这份报告制作下一期。';
  rememberDraft();updateComposer();sync();input.focus();
  notice('已带入上期约定；补充本期时间、变化或材料后发送');
 }
 return {open,sync};
}
