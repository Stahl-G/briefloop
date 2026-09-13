'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');

test('actual Electron launches the unpacked supervisor while preserving an ordinary app.asar directory', {skip:process.platform!=='win32',timeout:15000}, async t=>{
  let electron,asar;
  try{electron=require('electron');asar=require('@electron/asar');await fs.access(electron)}
  catch{t.skip('Install desktop Electron development dependencies for the native ASAR regression');return}
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-asar-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const directory=path.join(root,'中文 空格'),source=path.join(directory,'source');
  await fs.mkdir(source,{recursive:true});
  for(const name of ['environment.cjs','windows-process.ps1','windows-process.cs'])
    await fs.copyFile(path.join(__dirname,'..',name),path.join(source,name));
  const archive=path.join(directory,'app.asar');
  await asar.createPackageWithOptions(source,archive,{unpack:'{windows-process.ps1,windows-process.cs}'});
  const ordinary=path.join(directory,'ordinary','app.asar');
  await fs.cp(source,ordinary,{recursive:true});
  const runner=path.join(root,'probe.cjs');
  await fs.writeFile(runner,`
const fs=require('node:fs/promises'),physical=require('original-fs').promises,path=require('node:path');
(async()=>{
  const archive=${JSON.stringify(archive)},ordinary=${JSON.stringify(ordinary)};
  const rows=[];
  for(const target of [archive,ordinary]){
    const result=await require(path.join(target,'environment.cjs')).runOwnedProcess(${JSON.stringify(process.execPath)},['-e','console.log(JSON.stringify({text:"中文 space"}))']);
    rows.push({virtualDirectory:(await fs.stat(target)).isDirectory(),physicalFile:(await physical.stat(target)).isFile(),output:JSON.parse(result.stdout)});
  }
  console.log(JSON.stringify({electron:process.versions.electron,noAsar:process.noAsar===true,rows}));
})().catch(error=>{console.error(error.code,error.message);process.exitCode=1});
`,'utf8');
  const child=spawn(electron,[runner],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,ELECTRON_RUN_AS_NODE:'1'}});
  let stdout='',stderr='';child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
  child.stdout.on('data',chunk=>{stdout+=chunk});child.stderr.on('data',chunk=>{stderr+=chunk});
  const timer=setTimeout(()=>child.kill(),12000);
  const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',resolve)}).finally(()=>clearTimeout(timer));
  assert.equal(code,0,stderr);
  const result=JSON.parse(stdout.trim());
  assert.ok(result.electron);assert.equal(result.noAsar,false);
  assert.deepEqual(result.rows,[
    {virtualDirectory:true,physicalFile:true,output:{text:'中文 space'}},
    {virtualDirectory:true,physicalFile:false,output:{text:'中文 space'}},
  ]);
});
