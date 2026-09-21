// Session token + upload limits live with the transport, not page components.
import {preflightSources} from './uploads.js';

const session={token:'',limits:null};

export function getToken(){return session.token}
export function setToken(token){session.token=token||''}
export function getUploadLimits(){return session.limits}

export async function api(path,data,retried=false){
 const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-BriefLoop-Token':session.token},body:JSON.stringify(data)});
 const b=await r.json();
 if(r.status===403&&data!==undefined&&!retried){session.token=(await api('session')).token;return api(path,data,true)}
 if(!r.ok)throw Error(b.error||'操作失败');
 if(path==='session')session.limits=b.upload_limits;
 return b;
}

export async function uploadSource(file,retried=false){
 preflightSources([file],session.limits);
 const r=await fetch('/api/upload-file?name='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-BriefLoop-Token':session.token},body:file});
 const b=await r.json();
 if(r.status===403&&!retried){session.token=(await api('session')).token;return uploadSource(file,true)}
 if(!r.ok)throw Error(b.error||'上传失败');
 return b;
}
