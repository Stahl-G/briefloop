'use strict';
// The publisher runs this only after verifying the frozen shared wheel. Private
// signing keys are external files and must never enter Git or release assets.
const fs = require('node:fs');
const path = require('node:path');
const {createHash, sign} = require('node:crypto');
function main(args) {
  if (args.length !== 7) throw Error('Usage: sign-backend-update.cjs BACKEND_DIRECTORY PRIVATE_KEY KEY_ID SEQUENCE EXPIRES_ISO SHELL_RANGE OUTPUT');
  const [directory, privateKey, keyID, sequence, expiry, shellRange, output] = args;
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'manifest.json')));
  const wheel = fs.readFileSync(path.join(directory, manifest.wheel));
  const sha256 = createHash('sha256').update(wheel).digest('hex');
  if (sha256 !== manifest.sha256 || manifest.channel !== 'release') throw Error('Expected a frozen shared release wheel.');
  const release = {schema: 1, version: manifest.version, source_commit: manifest.source_commit,
    sequence: Number(sequence), expires: Date.parse(expiry), shell_range: shellRange,
    desktop_protocol: 1, data_epoch: 1, requirements_sha256: manifest.requirements_sha256,
    wheel: manifest.wheel, wheel_bytes: wheel.length, wheel_sha256: sha256};
  const payload = Buffer.from(JSON.stringify(release));
  const envelope = {schema: 1, key_id: keyID, payload: payload.toString('base64'),
    signature: sign(null, payload, fs.readFileSync(privateKey)).toString('base64')};
  const {parseEnvelope} = require('../backend-update-manifest.cjs');
  const {createPublicKey} = require('node:crypto');
  parseEnvelope(envelope, {[keyID]: createPublicKey(fs.readFileSync(privateKey))});
  fs.writeFileSync(output, JSON.stringify(envelope, null, 2) + '\n', {flag: 'wx'});
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (error) { console.error(error.message); process.exitCode = 1; } }
module.exports = {main};
