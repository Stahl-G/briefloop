import test from 'node:test';
import assert from 'node:assert/strict';
import {preflightSources,preflightUploads,uploadPayload} from '../frontend/uploads.js';

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

test('raw source preflight allows large PDFs and keeps other files at the base limit',()=>{
 const limits={max_file_bytes:18*1048576,max_request_bytes:25*1048576,max_pdf_bytes:100*1048576};
 preflightSources([{name:'年报.PDF',size:limits.max_pdf_bytes},{name:'表.xlsx',size:limits.max_file_bytes}],limits);
 assert.throws(()=>preflightSources([{name:'扫描.pdf',size:limits.max_pdf_bytes+1}],limits),/PDF 单文件 100 MiB/);
 assert.throws(()=>preflightSources([{name:'图.png',size:limits.max_file_bytes+1},{name:'a.pdf',size:1}],limits),/单文件 18 MiB.*2 个文件/);
 assert.throws(()=>preflightSources([{name:'a.pdf',size:1}],{max_file_bytes:1}),/上传限制尚未读取/);
});
