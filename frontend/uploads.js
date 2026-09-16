// Limits come from the running backend, including JSON and base64 overhead.
export function preflightUploads(files, limits, extra={}) {
 if(!limits?.max_file_bytes || !limits?.max_request_bytes)throw Error('上传限制尚未读取，请刷新后重试');
 const list=[...files],total=list.reduce((sum,file)=>sum+file.size,0);
 for(const file of list){
  const bytes=new TextEncoder().encode(JSON.stringify({...extra,name:file.name,data:''})).length+4*Math.ceil(file.size/3);
  if(file.size>limits.max_file_bytes || bytes>=limits.max_request_bytes)
   throw Error(`${file.name}（${(file.size/1048576).toFixed(1)} MiB，${file.size.toLocaleString()} 字节）超过单文件 ${limits.max_file_bytes/1048576} MiB 或编码后请求限制。本次 ${list.length} 个文件合计 ${(total/1048576).toFixed(1)} MiB，尚未上传；请压缩、拆分文件或缩短文件名后重试。`);
 }
}
export async function uploadPayload(file, limits, extra={}) {
 preflightUploads([file],limits,extra);
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
 for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
 return {...extra,name:file.name,data:btoa(binary)};
}
// Source files are sent as raw bytes; PDFs have their own larger limit.
export function preflightSources(files, limits) {
 if(!limits?.max_file_bytes || !limits?.max_pdf_bytes)throw Error('上传限制尚未读取，请刷新后重试');
 const list=[...files],total=list.reduce((sum,file)=>sum+file.size,0);
 for(const file of list){
  const pdf=/\.pdf$/i.test(file.name||''),limit=pdf?limits.max_pdf_bytes:limits.max_file_bytes;
  if(file.size>limit)
   throw Error(`${file.name}（${(file.size/1048576).toFixed(1)} MiB，${file.size.toLocaleString()} 字节）超过${pdf?' PDF ':''}单文件 ${limit/1048576} MiB 限制。本次 ${list.length} 个文件合计 ${(total/1048576).toFixed(1)} MiB，尚未上传；请压缩或拆分文件后重试。`);
 }
}
