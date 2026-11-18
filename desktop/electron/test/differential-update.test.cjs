'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const {createUpdater} = require('../updater.cjs');
const {validateMap} = require('../differential-download.cjs');
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
async function scenario(t, failure) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-delta-'));
  const blocks = Array.from({length: 12}, () => crypto.randomBytes(8192));
  const old = Buffer.concat(blocks);
  const nextBlocks = [...blocks]; nextBlocks[5] = crypto.randomBytes(8192);
  const next = Buffer.concat(nextBlocks);
  const map = pieces => ({version: '2', files: [{name: 'file', offset: 0, sizes: pieces.map(b => b.length), checksums: pieces.map(b => crypto.createHash('sha256').update(b).digest().subarray(0,18).toString('base64'))}]});
  let version = '1.0.0', transferred = 0, ranges = 0, full = 0;
  const server = http.createServer((req, res) => {
    const data = version === '1.0.0' ? old : next;
    const url = `${origin}/Stahl-G/briefloop/releases/download/v${version}/BriefLoop-${version}-arm64-mac.zip`;
    if (req.url === '/release') return res.end(JSON.stringify({tag_name: 'v'+version, html_url: origin+'/notes', assets: [
      {name: `BriefLoop-${version}-arm64-mac.zip`, size: data.length, digest: 'sha256:'+sha(data), browser_download_url: url},
      {name: `BriefLoop-${version}-arm64-mac.zip.blockmap`, size: 1000, browser_download_url: url+'.blockmap'}]}));
    if (req.url.endsWith('.blockmap')) return res.end(zlib.gzipSync(JSON.stringify(map(version === '1.0.0' ? blocks : nextBlocks))));
    if (req.headers.range) {
      ranges++;
      if (failure === 'no-range') {res.writeHead(200); res.end(data); return;}
      const [, a, b] = /bytes=(\d+)-(\d+)/.exec(req.headers.range);
      let bytes = data.subarray(+a, +b+1);
      if (failure === 'bad-bytes') bytes = Buffer.alloc(bytes.length);
      transferred += bytes.length;
      res.writeHead(206, {'Content-Range': `bytes ${a}-${b}/${data.length}`}); res.end(bytes); return;
    }
    full++; transferred += data.length; res.end(data);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const config = {app: {getVersion: () => '0.9.0', getPath: () => directory}, shell: {openPath: async file => {assert.equal(sha(await fs.readFile(file)), sha(next));return '';}}, platform: 'darwin', arch: 'arm64', testFeed: origin+'/release'};
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await fs.rm(directory, {recursive: true, force: true});});
  const first = createUpdater(config); await first.check(); assert.equal((await first.download()).state,'downloaded');
  assert.equal(first.status().progress.mode,'full');
  if (failure === 'corrupt-cache') {
    const index = JSON.parse(await fs.readFile(path.join(directory,'updates/differential-cache.json'),'utf8'));
    await fs.writeFile(path.join(directory,'updates',index.file), Buffer.alloc(old.length));
  }
  version = '1.0.1'; transferred = ranges = full = 0;
  const second = createUpdater(config); await second.check();
  const result = await second.download();
  assert.equal(result.state,'downloaded');
  const index = JSON.parse(await fs.readFile(path.join(directory,'updates/differential-cache.json'),'utf8'));
  assert.equal(sha(await fs.readFile(path.join(directory,'updates',index.file))), sha(next));
  assert.equal(result.installMode,'zip');
  if (!failure) {
    assert.equal(result.progress.mode,'differential', JSON.stringify(result.progress)); assert.equal(transferred,8192); assert.equal(full,0); assert.equal(ranges,1);
    assert.equal(result.progress.reused,11*8192);
  } else {assert.equal(result.progress.mode,'full'); assert.equal(full,1);}
}
test('second update reuses verified disk cache across restart and downloads only changed bytes', t => scenario(t));
for (const failure of ['no-range','bad-bytes','corrupt-cache']) test(`${failure} falls back to a verified full download`,t => scenario(t,failure));
test('malformed blockmaps fail closed', () => {
  for (const map of [null,{}, {version:'2',files:[]}, {version:'2',files:[{name:'file',offset:0,sizes:[-1],checksums:['a'.repeat(24)]}]}]) assert.throws(()=>validateMap(map,8192));
});

async function fragmentedTransfer(t, fail) {
  const {reconstruct} = require('../differential-download.cjs');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-parallel-'));
  t.after(() => fs.rm(directory, {recursive:true,force:true}));
  const blocks = Array.from({length:100}, () => crypto.randomBytes(65536));
  const pieces = [...blocks.slice(50), ...blocks.slice(0,50)];
  // Tiny reusable gaps should be merged; larger gaps keep independent requests.
  pieces.splice(3, 0, crypto.randomBytes(4096), crypto.randomBytes(1024), crypto.randomBytes(4096));
  const oldGap = pieces[4];
  const oldPieces = [...blocks, oldGap];
  await fs.writeFile(path.join(directory,'old.zip'),Buffer.concat(oldPieces));
  pieces[3] = crypto.randomBytes(4096);
  pieces[5] = crypto.randomBytes(4096);
  for (const i of [15,30,45,60,75,90]) pieces[i] = crypto.randomBytes(pieces[i].length);
  const next = Buffer.concat(pieces);
  const map = chunks => ({version:'2',files:[{name:'file',offset:0,sizes:chunks.map(c=>c.length),
    checksums:chunks.map(c=>crypto.createHash('sha256').update(c).digest().subarray(0,18).toString('base64'))}]});
  let active = 0, peak = 0, requests = 0, cancelled = 0;
  const request = async (start,end,signal) => {
    const index = requests++;
    active++;
    peak = Math.max(peak,active);
    return new Promise((resolve,reject) => {
      const aborted = () => {clearTimeout(timer); active--; cancelled++; reject(signal.reason);};
      signal.addEventListener('abort',aborted,{once:true});
      const timer = setTimeout(() => {
        signal.removeEventListener('abort',aborted);
        active--;
        const status = fail && index === 0 ? 200 : 206;
        resolve(new Response(next.subarray(start,end+1),{status,headers:{'content-range':`bytes ${start}-${end}/${next.length}`}}));
      },index === 0 ? 5 : 35 - index);
    });
  };
  const target=path.join(directory,'new.zip');
  const task=reconstruct({cache:{file:path.join(directory,'old.zip'),map:map(oldPieces)},map:map(pieces),size:next.length,
    destination:target,request,progress:()=>{}});
  if (fail) {
    await assert.rejects(task,/range_unavailable/);
    assert.equal(active,0);
    assert(cancelled>0,'unfinished siblings must be cancelled before fallback can overwrite the file');
    // A later full fallback can safely replace this file once reconstruct rejects.
    await fs.writeFile(target,next);
    await new Promise(resolve=>setTimeout(resolve,45));
  } else {
    const result=await task;
    assert(requests<8,'nearby changed blocks should use one combined request');
    assert(peak>1 && peak<=4,'requests must overlap within a bounded pool');
    assert(result.transferred < next.length/4);
    assert.equal(result.reused+result.transferred,next.length);
  }
  assert.deepEqual(await fs.readFile(target),next,'shifted old-file offsets and out-of-order downloads must reconstruct exact bytes');
}
test('fragmented updates merge close ranges and reconstruct shifted copies with bounded parallel downloads',t=>fragmentedTransfer(t,false));
test('parallel range failure cancels and drains siblings before a fallback can write',t=>fragmentedTransfer(t,true));
