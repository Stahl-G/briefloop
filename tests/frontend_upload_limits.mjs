import test from 'node:test';
import assert from 'node:assert/strict';
import {preflightUploads,uploadPayload} from '../frontend/uploads.js';

test('batch preflight rejects before reading files and accounts for JSON/base64 overhead',async()=>{
 const limits={max_file_bytes:18*1048576,max_request_bytes:25*1048576};
 const good={name:'材料.txt',size:3,arrayBuffer:async()=>new Uint8Array([1,2,3]).buffer};
 preflightUploads([good,{name:'边界.pdf',size:limits.max_file_bytes}],limits);
 assert.throws(()=>preflightUploads([good,{name:'过大.pdf',size:limits.max_file_bytes+1}],limits),/2 个文件.*尚未上传/);
 assert.throws(()=>preflightUploads([{name:'长'.repeat(2400000),size:limits.max_file_bytes}],limits),/编码后请求限制/);
 let read=false;
 await assert.rejects(uploadPayload({name:'large.pdf',size:limits.max_file_bytes+1,arrayBuffer(){read=true}},limits),/large.pdf/);
 assert.equal(read,false);
 assert.deepEqual(await uploadPayload(good,limits,{base_version:'v1'}),{base_version:'v1',name:'材料.txt',data:'AQID'});
});
