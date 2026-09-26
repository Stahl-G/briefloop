import test from 'node:test';
import assert from 'node:assert/strict';
import {createSourceUploads} from '../frontend/source-upload.js';
import {sendSourceFile,sourceStatusLabel} from '../frontend/uploads.js';

const limits={max_file_bytes:18*1048576,max_pdf_bytes:100*1048576};
test('receipt stays pending until real extraction state is ready',async()=>{
 const progress=[],accepted=[],calls=[];let reads=0;
 const uploads=createSourceUploads({getUploadLimits:()=>limits,getToken:()=> 'token',delay:async()=>{},
  onAccepted:s=>accepted.push(s.status),
  sendFile:async(file,options)=>{options.onProgress({phase:'uploading',bytes_sent:10,bytes_total:10});return {status:202,body:{id:'src1',status:'queued',extraction_job_id:'job1'}}},
  api:async(path,body)=>{calls.push([path,body]);reads++;return {source:{id:'src1',status:reads===1?'extracting':'ready'},job:{id:'job1'},progress:{phase:'extracting',pages_completed:reads,pages_total:2}}}
 });
 const source=await uploads.uploadSource({name:'report.pdf',size:10},{onProgress:p=>progress.push(p)});
 assert.equal(source.status,'ready');assert.deepEqual(accepted,['queued']);assert.equal(reads,2);
 assert.equal(progress.find(p=>p.phase==='queued').message,'原件已保存，等待读取');
 assert.equal(progress.at(-1).pages_completed,2);assert.match(calls[0][0],/^source-status/);
});

test('cancel after receipt stops the extraction job and returns cancelled source',async()=>{
 const controller=new AbortController(),calls=[];let reads=0;
 const uploads=createSourceUploads({getUploadLimits:()=>limits,getToken:()=> 'token',delay:async()=>{},
  onAccepted:()=>controller.abort(),sendFile:async()=>({status:202,body:{id:'src1',status:'queued',extraction_job_id:'job1'}}),
  api:async(path,body)=>{calls.push([path,body]);return path==='stop'?{ok:true}:{source:{id:'src1',status:++reads===3?'cancelled':'extracting'},job:{id:'job1'}}}
 });
 const source=await uploads.uploadSource({name:'report.pdf',size:10},{signal:controller.signal});
 assert.equal(source.status,'cancelled');assert.deepEqual(calls[0],['stop',{job_id:'job1'}]);
 assert.equal(calls.filter(([path])=>path==='stop').length,1);
 assert.equal(sourceStatusLabel(source),'读取已取消');
});

test('raw transport forwards browser byte progress without loading file into memory',async()=>{
 const progress=[];let sent;
 const xhr={upload:{},open(){},setRequestHeader(){},send(file){sent=file;this.upload.onprogress({loaded:3,total:10,lengthComputable:true});this.status=202;this.responseText='{"status":"queued"}';this.onload()}};
 const file={name:'report.pdf',size:10,arrayBuffer(){throw Error('must stream browser File')}};
 const response=await sendSourceFile(file,{token:'token',onProgress:p=>progress.push(p),xhrFactory:()=>xhr});
 assert.equal(sent,file);assert.equal(response.status,202);assert.equal(progress[0].bytes_sent,3);
});

test('a scanned PDF keeps the visual-reading and no-OCR notice on the finished card',async()=>{
 const node=()=>({children:[],append(...items){this.children.push(...items)},dataset:{},setAttribute(){},removeAttribute(){},remove(){}});
 const host=node();globalThis.document={createElement:node};
 const uploads=createSourceUploads({getUploadLimits:()=>limits,getToken:()=> 'token',progressHost:()=>host,delay:async()=>{},
  sendFile:async()=>({status:202,body:{id:'scan',status:'queued',extraction_job_id:'scan-job'}}),
  api:async()=>({source:{id:'scan',status:'ready',needs_visual:true,pages:3,media_type:'application/pdf'},job:{id:'scan-job'},progress:{phase:'extracting',pages_completed:3,pages_total:3}})
 });
 const source=await uploads.uploadSource({name:'scan.pdf',size:10});
 assert.equal(source.needs_visual,true);assert.equal(source.pages,3);
 assert.match(host.children[0].children[1].textContent,/视觉读取/);
 assert.match(host.children[0].children[1].textContent,/未执行 OCR/);
});
