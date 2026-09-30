'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {verifyBackend} = require('../scripts/verify-backend.cjs');

test('packaging rejects an unbound stable backend and tampered candidate bytes', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'backend-identity-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  fs.mkdirSync(path.join(dir, 'backend'));
  const wheel = 'briefloop-1.2.3rc1-py3-none-any.whl';
  const payload = Buffer.from('frozen');
  const requirements = Buffer.from('example==1.0 --hash=sha256:' + 'a'.repeat(64) + '\n');
  const manifest = {version: '1.2.3', wheel, sha256: createHash('sha256').update(payload).digest('hex'),
    requirements: 'requirements.txt', requirements_sha256: createHash('sha256').update(requirements).digest('hex')};
  const write = (version, value) => {
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({version}));
    fs.writeFileSync(path.join(dir, 'backend/manifest.json'), JSON.stringify(value));
  };
  fs.writeFileSync(path.join(dir, 'backend', wheel), payload);
  const lock = path.join(dir, 'backend/requirements.txt');
  fs.writeFileSync(lock, requirements);
  write('1.2.3', manifest);
  assert.throws(() => verifyBackend(dir), /frozen release backend/);
  write('1.2.3-rc.1', {...manifest, version: '1.2.3rc1', channel: 'prerelease'});
  assert.equal(verifyBackend(dir).channel, 'prerelease');
  fs.unlinkSync(lock);
  assert.throws(() => verifyBackend(dir), /dependency lock is missing/);
  fs.writeFileSync(lock, 'tampered lock');
  assert.throws(() => verifyBackend(dir), /dependency lock is missing or differs/);
  fs.writeFileSync(lock, requirements);
  assert.equal(verifyBackend(dir).channel, 'prerelease');
  fs.writeFileSync(path.join(dir, 'backend', wheel), 'tampered');
  assert.throws(() => verifyBackend(dir), /SHA-256 mismatch/);
});
