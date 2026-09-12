// Node 20 on Windows does not expand CLI globs; pass explicit test files.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root=fileURLToPath(new URL('../',import.meta.url));
const tests=fs.readdirSync(path.join(root,'tests'))
  .filter(name=>/^frontend_.*\.mjs$/.test(name)).sort()
  .map(name=>path.join(root,'tests',name));
if(!tests.length)throw Error('No frontend tests found');
const result=spawnSync(process.execPath,['--test',...process.argv.slice(2),...tests],{cwd:root,stdio:'inherit'});
if(result.error)throw result.error;
process.exitCode=result.status??1;
