'use strict';
// Build-time entry point. The installed app never invokes npm or downloads a runtime.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const desktop = path.resolve(__dirname, '..');
const repo = path.resolve(desktop, '..', '..');
const args = process.argv.slice(2);
if (process.platform !== 'win32' || process.arch !== 'x64') throw Error('Build on native Windows x64.');
if (args.some(arg => arg !== '--dir')) throw Error('Usage: node scripts/build-windows.cjs [--dir]');
const runtime = path.join(desktop, 'runtime', 'windows-x64');
for (const name of ['python/python.exe', 'node/node.exe', 'manifest.json', 'relocation-proof.json']) {
  if (!fs.statSync(path.join(runtime, name)).isFile()) throw Error(`Missing bundled runtime file: ${name}`);
}
if (JSON.parse(fs.readFileSync(path.join(runtime, 'relocation-proof.json'), 'utf8')).status !== 'passed') {
  throw Error('Run prepare-runtime-windows.py and its relocation verification before packaging.');
}
if (!fs.existsSync(path.join(desktop, 'assets', 'Win.ico'))) {
  throw Error('The approved shared assets/Win.ico must be present before building.');
}
const builder = path.join(desktop, 'node_modules', 'electron-builder', 'cli.js');
const buildEnv = {...process.env, CSC_IDENTITY_AUTO_DISCOVERY: 'false'};
for (const key of ['CSC_LINK', 'WIN_CSC_LINK', 'CSC_KEY_PASSWORD', 'WIN_CSC_KEY_PASSWORD']) delete buildEnv[key];
const built = spawnSync(process.execPath, [builder, '--config', 'electron-builder.windows.cjs',
  '--win', ...(args.includes('--dir') ? ['--dir'] : ['nsis']), '--x64', '--publish', 'never'], {
  cwd: desktop, stdio: 'inherit', windowsHide: true,
  env: buildEnv,
});
if (built.error) throw built.error;
if (built.status !== 0) process.exit(built.status || 1);
const version = require('../package.json').version;
const artifact = path.join(desktop, 'dist', args.includes('--dir') ? 'win-unpacked/BriefLoop.exe' : `BriefLoop-Setup-${version}-x64.exe`);
const data = fs.readFileSync(artifact);
const git = spawnSync('git', ['-c', `safe.directory=${repo.replaceAll('\\', '/')}`, 'rev-parse', 'HEAD'],
  {cwd: repo, encoding: 'utf8', windowsHide: true});
if (git.status !== 0) throw Error('Cannot record the source commit for this artifact.');
const status = spawnSync('git', ['-c', `safe.directory=${repo.replaceAll('\\', '/')}`, 'status', '--porcelain'],
  {cwd: repo, encoding: 'utf8', windowsHide: true});
if (status.status !== 0) throw Error('Cannot record source worktree state.');
const record = {artifact, sha256: createHash('sha256').update(data).digest('hex'), bytes: data.length,
  git_head: git.stdout.trim(), dirty: Boolean(status.stdout.trim()),
  target: 'windows-x64', signed: false, published: false, built_at: new Date().toISOString()};
fs.writeFileSync(path.join(desktop, 'dist', 'windows-artifact.json'), JSON.stringify(record, null, 2)+'\n', 'utf8');
console.log(JSON.stringify(record, null, 2));
