// The server remains the authority for admitting a Reviewer. The composer uses
// the same published capability list so a disabled switch explains itself.
export function factCheckAvailability({allowWeb, backend, reviewRuntime, reviewMode='standard', capability, pendingReview=false, backendLabel=id=>id}){
 if(pendingReview)return {enabled:false,reason:'审阅设置尚未保存，请先完成设置。',action:'完成审阅设置',target:'review'};
 if(!allowWeb)return {enabled:false,reason:'本轮未允许联网；事实核查需要补查公开来源。',action:'打开联网设置',target:'network'};
 const reviewer=reviewRuntime?.backend||backend;
 const available=capability?.[reviewMode==='strict'?'strict_review':'standard_review'];
 if(Array.isArray(available)&&!available.some(item=>item.id===reviewer)){
  const label=reviewRuntime?.backend?backendLabel(reviewer):`${backendLabel(reviewer)}（跟随主执行端）`;
  const alternatives=available.map(item=>item.label).join('、');
  return {enabled:false,reason:`${label} 尚未接入${reviewMode==='strict'?'严格':'普通'}独立审阅；事实核查需要独立审阅。${alternatives?`可选择 ${alternatives} 作为审阅端。`:''}`,action:'设置审阅端',target:'review'};
 }
 return {enabled:true,reason:'',action:'',target:''};
}
