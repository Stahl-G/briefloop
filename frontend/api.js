// Session token + upload limits live with the transport, not page components.
import {createSourceUploads} from './source-upload.js';

const session={token:'',limits:null};
let sourceUploadHost=()=>null,sourceAccepted=()=>{};

export function getToken(){return session.token}
export function setToken(token){session.token=token||''}
export function getUploadLimits(){return session.limits}
export function setSourceUploadHost(host,onAccepted=()=>{}){sourceUploadHost=host;sourceAccepted=onAccepted}

export async function api(path,data,retried=false){
 const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-BriefLoop-Token':session.token},body:JSON.stringify(data)});
 const b=await r.json();
 if(r.status===403&&data!==undefined&&!retried){session.token=(await api('session')).token;return api(path,data,true)}
 if(!r.ok)throw Error(b.error||'操作失败');
 if(path==='session'){session.limits=b.upload_limits;session.token=b.token||session.token}
 return b;
}

const sourceUploads=createSourceUploads({api,getToken,getUploadLimits,progressHost:()=>sourceUploadHost(),onAccepted:source=>sourceAccepted(source)});
export const uploadSource=sourceUploads.uploadSource;
