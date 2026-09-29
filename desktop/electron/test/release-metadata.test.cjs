'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parseReleaseManifest} = require('../release-metadata.cjs');

function manifest() {
  return {version: '0.26.1', source_commit: 'a'.repeat(40), assets: {
    'BriefLoop-0.26.1-arm64-mac.zip': {bytes: 133140010, sha256: 'b'.repeat(64)},
    'BriefLoop-0.26.1-arm64-mac.zip.blockmap': {bytes: 139158, sha256: 'c'.repeat(64)},
    'latest.yml': {bytes: 358, sha256: 'd'.repeat(64)},
  }};
}

const parse = value => parseReleaseManifest(value, {tag: 'v0.26.1'});
const rejects = value => assert.throws(() => parse(value), {
  code: 'invalid_release_manifest', retryable: false,
});

test('release manifest becomes a version-bound release with only fixed official URLs', () => {
  const input = manifest();
  input.html_url = 'https://attacker.invalid/release';
  input.assets['BriefLoop-0.26.1-arm64-mac.zip'].browser_download_url = 'https://attacker.invalid/app.zip';
  const before = structuredClone(input), release = parse(input);
  assert.deepEqual(input, before, 'parsing must not modify the input');
  assert.equal(release.tag_name, 'v0.26.1');
  assert.equal(release.draft, false); assert.equal(release.prerelease, false);
  assert.equal(release.html_url, 'https://github.com/Stahl-G/briefloop/releases/tag/v0.26.1');
  assert.deepEqual(release.assets[0], {name: 'BriefLoop-0.26.1-arm64-mac.zip', size: 133140010,
    digest: 'sha256:' + 'b'.repeat(64),
    browser_download_url: 'https://github.com/Stahl-G/briefloop/releases/download/v0.26.1/BriefLoop-0.26.1-arm64-mac.zip'});
  assert.equal(release.assets.length, 3);
});

test('release identity must match the trusted tag and identify a frozen stable source', () => {
  const input = manifest();
  assert.throws(() => parseReleaseManifest(input, {tag: 'v0.26.0'}), {code: 'invalid_release_manifest'});
  assert.throws(() => parseReleaseManifest(input), {code: 'invalid_release_manifest'});
  for (const version of ['0.26.2-beta.1', 'v0.26.1', 'not-a-version']) {
    assert.throws(() => parseReleaseManifest({...input, version}, {tag: `v${version}`}), {code: 'invalid_release_manifest'});
  }
  rejects({...input, source_commit: 'HEAD'});
});

test('asset paths cannot escape a basename and integrity records must be bounded', () => {
  const entry = {bytes: 10, sha256: 'b'.repeat(64)};
  for (const name of ['../app.zip', 'dir/app.zip', 'dir\\app.zip', 'app%2f.zip', '.hidden', 'app.zip?query']) {
    rejects({...manifest(), assets: {[name]: entry}});
  }
  for (const item of [{...entry, bytes: 0}, {...entry, bytes: 4 * 1024 ** 3 + 1},
    {...entry, bytes: 1.5}, {...entry, sha256: 'b'.repeat(63)}]) {
    rejects({...manifest(), assets: {'app.zip': item}});
  }
  assert.equal(parse({...manifest(), assets: {'app.zip': {...entry, bytes: 4 * 1024 ** 3}}}).assets[0].size, 4 * 1024 ** 3);
  rejects({...manifest(), assets: []});
  rejects({...manifest(), assets: Object.fromEntries(Array.from({length: 101}, (_, i) => [`asset-${i}.zip`, entry]))});
});
