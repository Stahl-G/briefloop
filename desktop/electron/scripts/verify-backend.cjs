'use strict';

// Runs for both platforms, including direct electron-builder invocations.
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {execFileSync} = require('node:child_process');

function verifyBackend(desktop = path.resolve(__dirname, '..')) {
  const pkg = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8'));
  const backend = path.join(desktop, 'backend');
  const manifest = JSON.parse(fs.readFileSync(path.join(backend, 'manifest.json'), 'utf8'));
  const appVersion = manifest.version.replace(/(rc|a|b)(\d+)$/, (_, kind, n) => `-${{rc:'rc', a:'alpha', b:'beta'}[kind]}.${n}`).replace(/\.dev(\d+)$/, '-dev.$1');
  if (pkg.version !== appVersion || !/^briefloop-[a-zA-Z0-9_.-]+\.whl$/.test(manifest.wheel || '')) {
    throw Error('Backend identity differs from the App. Stage the shared release wheel first.');
  }
  const digest = createHash('sha256').update(fs.readFileSync(path.join(backend, manifest.wheel))).digest('hex');
  if (digest !== manifest.sha256) throw Error('Backend wheel SHA-256 mismatch.');
  // The App installs dependencies only from this hash-locked list (#851).
  const lock = path.join(backend, manifest.requirements || '');
  if (manifest.requirements !== 'requirements.txt' || !fs.existsSync(lock)
      || createHash('sha256').update(fs.readFileSync(lock)).digest('hex') !== manifest.requirements_sha256) {
    throw Error('Backend dependency lock is missing or differs from the manifest. Stage it with prepare-backend.py.');
  }
  if (/^\d+\.\d+\.\d+$/.test(pkg.version)) {
    if (manifest.channel !== 'release' || !/^[a-f0-9]{40}$/.test(manifest.source_commit || '')) {
      throw Error('Stable App requires a frozen release backend. Local candidates must use a prerelease version.');
    }
    const root = path.resolve(desktop, '../..');
    const git = args => execFileSync('git', args, {cwd: root, encoding: 'utf8', windowsHide: true}).trim();
    if (git(['rev-parse', 'HEAD']) !== manifest.source_commit || git(['status', '--porcelain', '--untracked-files=normal'])) {
      throw Error('Stable App source must be clean and match the shared backend frozen commit.');
    }
  } else if (manifest.channel !== 'prerelease') {
    throw Error('Candidate App requires a prerelease backend identity.');
  }
  return manifest;
}

module.exports = async context => verifyBackend(context?.packager?.projectDir);
module.exports.verifyBackend = verifyBackend;
if (require.main === module) { verifyBackend(); console.log('Frozen backend verified.'); }
