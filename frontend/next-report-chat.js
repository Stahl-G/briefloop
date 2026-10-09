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
   card.append(title,note,cancel);$('chat-input').before(card);
  }
  card.hidden=!context;
  if(context){card.children[0].textContent='下一期 · '+context.title;
   card.children[1].textContent='沿用已保存的读者、结构和写作约定。告诉我本期时间和变化，或添加本期材料。';}
 }
 async function open(data){
  await openChatHome();
  getChat().nextReportContext={version_id:data.previous.version_id,hash:data.previous.hash,title:data.previous.title};
  const input=$('chat-input');
  if(!input.value.trim())input.value='请基于这份报告制作下一期。';
  rememberDraft();updateComposer();sync();input.focus();
  notice('已带入上期约定；补充本期时间、变化或材料后发送');
 }
 return {open,sync};
}
