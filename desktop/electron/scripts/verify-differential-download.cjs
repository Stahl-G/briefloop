'use strict';
// Offline acceptance with two real Mac payloads. Serves only the explicitly supplied files
// on loopback; never opens an installer or changes an installed application.
const fs = require('node:fs/promises');
const {createReadStream} = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {buildBlockMap} = require('app-builder-lib/out/targets/blockmap/blockmap');
const {hashFile} = require('../differential-download.cjs');
const {createUpdater} = require('../updater.cjs');
async function main() {
  const [oldFile, newFile] = process.argv.slice(2).map(f => path.resolve(f));
  if (!oldFile || !newFile) throw Error('Supply old and new Mac ZIPs or DMGs');
  const zip = oldFile.endsWith('-mac.zip') && newFile.endsWith('-mac.zip');
  if (!zip && !(oldFile.endsWith('.dmg') && newFile.endsWith('.dmg'))) throw Error('Payload formats must match');
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-real-delta-'));
  let server;
  try {
    const records = [];
    for (const [i, file] of [oldFile,newFile].entries()) {
      const map = path.join(temporary, `${i}.blockmap`);
      await buildBlockMap(file, 'gzip', map);
      records.push({file, map, size: (await fs.stat(file)).size, sha256: await hashFile(file)});
    }
    let selected = 0, networkBytes = 0, requests = 0;
    server = http.createServer(async (req,res) => {
      try {
        const record = records[selected], version = selected ? '1.0.1' : '1.0.0';
        const name = `BriefLoop-${version}-arm64${zip?'-mac.zip':'.dmg'}`;
        const url = `${origin}/Stahl-G/briefloop/releases/download/v${version}/${name}`;
        if (req.url === '/release') return res.end(JSON.stringify({tag_name:'v'+version,html_url:origin+'/notes',assets:[
          {name,size:record.size,digest:'sha256:'+record.sha256,browser_download_url:url},
          {name:name+'.blockmap',size:(await fs.stat(record.map)).size,browser_download_url:url+'.blockmap'}]}));
        if (req.url.endsWith('.blockmap')) {createReadStream(record.map).pipe(res);return;}
        requests++;
        const range = /^bytes=(\d+)-(\d+)$/.exec(req.headers.range || '');
        const start = range ? +range[1] : 0, end = range ? +range[2] : record.size-1;
        res.writeHead(range ? 206 : 200, {'Content-Length':end-start+1,...(range?{'Content-Range':`bytes ${start}-${end}/${record.size}`}:{})});
        const stream = createReadStream(record.file,{start,end});
        stream.on('data',b=>{networkBytes+=b.length;}); stream.pipe(res);
      } catch {res.destroy();}
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const config = {app:{getVersion:()=> '0.9.0',getPath:()=>temporary},shell:{},platform:'darwin',arch:'arm64',installMode:zip?'zip':'dmg',testFeed:origin+'/release'};
    const first = createUpdater(config); await first.check();
    const firstResult = await first.download();
    if (firstResult.state !== 'downloaded') throw Error(JSON.stringify(firstResult));
    selected=1;networkBytes=requests=0;
    const second=createUpdater(config);await second.check();
    const result=await second.download();
    if (result.state !== 'downloaded') throw Error(JSON.stringify(result));
    const index=JSON.parse(await fs.readFile(path.join(temporary,'updates/differential-cache.json'),'utf8'));
    const exact=await hashFile(path.join(temporary,'updates',index.file)) === records[1].sha256;
    console.log(JSON.stringify({old_sha256:records[0].sha256,new_sha256:records[1].sha256,installer_bytes:records[1].size,downloaded_payload_bytes:networkBytes,requests,saved_percent:100*(1-networkBytes/records[1].size),exact,progress:result.progress},null,2));
    if (!exact) throw Error('Reconstructed installer differs');
  } finally {
    if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
    await fs.rm(temporary,{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
