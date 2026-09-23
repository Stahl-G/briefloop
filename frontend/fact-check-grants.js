// One explicit click is one authorization. Retain its identity until the
// server acknowledges it, including after a lost response or a tab reload.
export const FACT_CHECK_GRANT_LIMITS={search_requests:6,candidate_urls:30,source_pages:12};

export function createFactCheckGrants({api,storage=()=>globalThis.sessionStorage,newId=()=>crypto.randomUUID()}){
 const flights=new Map();
 const key=version=>'briefloop-fact-check-grant:'+version;
 function pending(version){
  let value;
  try{value=storage().getItem(key(version))}catch{throw Error('无法读取上次追加记录，请恢复浏览器会话存储后重试')}
  if(!value)return null;
  let request;try{request=JSON.parse(value)}catch{}
  if(request?.version_id!==version||typeof request.request_id!=='string'||!request.limits)
   throw Error('上次追加记录无法识别，请保留页面并检查浏览器会话存储');
  return request;
 }
 function submit(version){
  if(flights.has(version))return flights.get(version);
  const request=pending(version)||{version_id:version,limits:{...FACT_CHECK_GRANT_LIMITS},request_id:newId()};
  // Do not send if we cannot retain the operation for a reload/retry.
  try{storage().setItem(key(version),JSON.stringify(request))}catch{throw Error('无法保存追加记录，本次尚未发送；请恢复浏览器会话存储后重试')}
  const flight=(async()=>{
   const result=await api('fact-check-grant',request);
   if(result.request_id!==request.request_id)throw Error('追加回执身份不符，已保留上次记录；请重试确认');
   // Keep the receipt unresolved if clearing fails; replay is still safe.
   try{storage().removeItem(key(version))}catch{throw Error('追加已获确认，但本地记录未能清理；重试只会确认同一笔额度')}
   return result;
  })().finally(()=>flights.delete(version));
  flights.set(version,flight);
  return flight;
 }
 return {pending,submit,busy:version=>flights.has(version)};
}
