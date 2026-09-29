'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {prepareReleaseManifest, productVersion} = require('../scripts/prepare-release-manifest.cjs');

async function fixture(t) {
  const dist = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-manifest-'));
  t.after(() => fs.rm(dist, {recursive: true, force: true}));
  return {dist, version: await productVersion(), commit: 'a'.repeat(40)};
}

test('manifest hashes existing release bytes, excludes build extras, and never overwrites a different manifest', async t => {
  const config = await fixture(t), name = `BriefLoop-${config.version}-arm64-mac.zip`;
  const bytes = Buffer.from('Synthetic frozen ZIP bytes, never executed.');
  await fs.writeFile(path.join(config.dist, name), bytes);
  await fs.writeFile(path.join(config.dist, 'builder-debug.yml'), 'not a published artifact');
  await fs.mkdir(path.join(config.dist, 'mac-arm64'));
  const before = await fs.stat(path.join(config.dist, name));
  const result = await prepareReleaseManifest({...config, version: undefined});
  assert.equal(result.created, true);
  assert.equal(result.manifest.version, config.version);
  assert.deepEqual(result.manifest.assets, {[name]: {bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')}});
  assert.equal((await fs.stat(path.join(config.dist, name))).mtimeMs, before.mtimeMs);
  assert.deepEqual(await fs.readFile(path.join(config.dist, name)), bytes);
  const saved = await fs.readFile(result.file, 'utf8');
  assert.equal((await prepareReleaseManifest(config)).created, false);
  await assert.rejects(prepareReleaseManifest({...config, commit: 'b'.repeat(40)}), /Refusing to replace/);
  assert.equal(await fs.readFile(result.file, 'utf8'), saved);
});

test('generation requires the frozen commit, product version, and regular matching release packages', async t => {
  const config = await fixture(t);
  await assert.rejects(prepareReleaseManifest({...config, commit: 'HEAD'}), /explicit frozen/);
  await assert.rejects(prepareReleaseManifest({...config, version: '999.0.0'}), /pyproject/);
  await assert.rejects(prepareReleaseManifest(config), /No release package/);
  const name = `BriefLoop-${config.version}-arm64-mac.zip`;
  await fs.mkdir(path.join(config.dist, name));
  await assert.rejects(prepareReleaseManifest(config), /Invalid release artifact/);
  await fs.rmdir(path.join(config.dist, name));
  await fs.writeFile(path.join(config.dist, 'BriefLoop-999.0.0-arm64-mac.zip'), 'wrong version');
  await assert.rejects(prepareReleaseManifest(config), /does not match/);
  await assert.rejects(fs.access(path.join(config.dist, 'release-manifest.json')), {code: 'ENOENT'});
});
