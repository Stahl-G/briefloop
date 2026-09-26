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
