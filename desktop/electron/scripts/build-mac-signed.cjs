'use strict';
// Explicit release path: missing signing/notary credentials must never fall back to unsigned.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const desktop = path.resolve(__dirname, '..');
function run(command, args) {
  const result = spawnSync(command, args, {cwd: desktop, encoding: 'utf8'});
  if (result.error || result.status !== 0) throw Error(`${command} failed: ${result.error?.message || result.stderr || result.stdout}`);
  return result.stdout;
}
function configuration(env, pkg) {
  const identity = env.BRIEFLOOP_APPLE_SIGNING_IDENTITY?.trim();
  const profile = env.APPLE_KEYCHAIN_PROFILE?.trim();
  if (!identity || identity === '-' || identity.startsWith('Developer ID')) throw Error('Set BRIEFLOOP_APPLE_SIGNING_IDENTITY to the certificate name without the Developer ID Application: prefix.');
  if (!profile) throw Error('Set APPLE_KEYCHAIN_PROFILE to an existing notarytool Keychain profile.');
  // One authentication mechanism per build; no ambiguous inherited Apple credentials.
  if (['APPLE_ID','APPLE_APP_SPECIFIC_PASSWORD','APPLE_API_KEY','APPLE_API_KEY_ID','APPLE_API_ISSUER'].some(k=>env[k])) throw Error('Use only the Keychain profile for this build; unset other Apple authentication variables.');
  return {...pkg.build, forceCodeSigning: true,
    mac: {...pkg.build.mac, identity, hardenedRuntime: true, notarize: true},
    dmg: {...pkg.build.dmg, sign: true}, publish: null};
}
async function main(args = process.argv.slice(2)) {
  if (process.platform !== 'darwin') throw Error('Run this release command on macOS.');
  if (args.some(arg => arg !== '--check')) throw Error('Usage: node scripts/build-mac-signed.cjs [--check]');
  const pkg = require('../package.json');
  const config = configuration(process.env, pkg);
  const identities = run('security', ['find-identity', '-v', '-p', 'codesigning']);
  if (!identities.split('\n').some(line => line.includes('Developer ID Application: ' + config.mac.identity))) throw Error('No matching valid Developer ID Application certificate and private key in the local Keychain.');
  const auth = ['--keychain-profile', process.env.APPLE_KEYCHAIN_PROFILE];
  if (process.env.APPLE_KEYCHAIN) auth.push('--keychain', process.env.APPLE_KEYCHAIN);
  run('xcrun', ['notarytool', 'history', ...auth, '--output-format', 'json']);
  if (args.includes('--check')) { console.log('Signing identity and notary authentication verified. No artifact built or submitted.'); return; }
  const manifest = JSON.parse(fs.readFileSync(path.join(desktop,'backend/manifest.json'),'utf8'));
  if (!/^briefloop-[a-zA-Z0-9_.-]+\.whl$/.test(manifest.wheel || '') || manifest.version !== pkg.version) throw Error('Prepare the frozen matching backend wheel first.');
  const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  if (hash(path.join(desktop,'backend',manifest.wheel)) !== manifest.sha256) throw Error('Backend wheel hash mismatch.');
  const {build, Platform, Arch} = require('electron-builder');
  const artifacts = await build({projectDir: desktop, targets: Platform.MAC.createTarget(['dmg','zip'],Arch.arm64), config, publish: 'never'});
  const app = path.join(desktop, 'dist/mac-arm64/BriefLoop.app');
  run('codesign', ['--verify','--deep','--strict',app]);
  run('xcrun', ['stapler','validate',app]);
  run('spctl', ['--assess','--type','execute','--verbose=2',app]);
  const records = [];
  for (const file of artifacts.filter(f=>f.endsWith('.dmg'))) {
    run('codesign', ['--verify','--strict',file]);
    const submission = JSON.parse(run('xcrun', ['notarytool','submit',file,...auth,'--wait','--output-format','json']));
    if (submission.status !== 'Accepted') throw Error(`DMG notarization not accepted: ${submission.id} / ${submission.status}`);
    run('xcrun', ['stapler','staple',file]);
    run('xcrun', ['stapler','validate',file]);
    run('spctl', ['--assess','--type','open','--context','context:primary-signature','--verbose=2',file]);
    records.push({file:path.basename(file),sha256:hash(file),submission_id:submission.id,status:submission.status});
  }
  if (!records.length) throw Error('No DMG produced; release verification incomplete.');
  const record = {version:pkg.version,source_commit:run('git',['rev-parse','HEAD']).trim(),wheel_sha256:manifest.sha256,artifacts:records};
  fs.writeFileSync(path.join(desktop,'dist/mac-signing.json'),JSON.stringify(record,null,2)+'\n');
  console.log('Signed and notarized artifacts verified. Publication and native install acceptance remain separate.');
}
if (require.main === module) main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports = {configuration};
