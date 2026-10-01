'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {CHECK_LOCK}=require('../dependency-lock-check.cjs');

test('real Python lock check handles hashes, markers, mismatches and invalid pins without installation',async t=>{
 const python=process.env.BRIEFLOOP_TEST_PYTHON||(process.platform==='win32'?'python':'python3');
 const version=execFileSync(python,['-I','-c','import importlib.metadata; print(importlib.metadata.version("pip"))'],{encoding:'utf8'}).trim();
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-lock-check-'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const file=path.join(root,'requirements.txt');
 const check=async text=>{await fs.writeFile(file,text);return JSON.parse(execFileSync(python,['-I','-c',CHECK_LOCK,file],{encoding:'utf8'})).matches;};
 assert.equal(await check(`pip==${version} \\\n    --hash=sha256:${'0'.repeat(64)}\n# comment\nnot-installed==1 ; sys_platform == "nonexistent-platform"\n`),true);
 assert.equal(await check('pip==0.0.0\n'),false);
 assert.equal(await check('pip>=1\n'),false);
 assert.equal(await check('pip @ https://invalid.example/package.whl\n'),false);
 assert.equal(await check('# no packages\n'),false);
});
