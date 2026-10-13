// A report drawer targets the visible saved version. Ordinary chat has no target.
export function createReportChatContext({getCurrent,isOpen,getNextReport,clearNextReport,savedVersion}){
 function activate(){if(getNextReport())clearNextReport()}
 async function prepare(){
  if(!isOpen()||getNextReport())return null;
  const selected=getCurrent();
  if(!selected)throw Error('请先选择要讨论的报告');
  const version=await savedVersion();
  const current=getCurrent();
  if(!isOpen()||getNextReport()||!current||current.run_id!==selected.run_id||current.id!==version)
   throw Error('当前报告已切换，请重新发送');
  if(!current.hash)throw Error('报告尚未保存，请保存后重新发送');
  return {version_id:current.id,hash:current.hash};
 }
 return {activate,prepare};
}
