'use strict';
const {verify, createHash} = require('node:crypto');
const semver = require('semver');
const RELEASES = 'https://github.com/Stahl-G/briefloop/releases';
const FEED = `${RELEASES}/download/backend-stable/backend-update.json`;
const fail = message => { throw new Error(message); };
function parseEnvelope(envelope, keys, {now = Date.now(), installed = false} = {}) {
  if (!envelope || envelope.schema !== 1 || typeof envelope.payload !== 'string' || envelope.payload.length > 65536
      || typeof envelope.signature !== 'string' || !keys[envelope.key_id]) fail('研究组件更新签名无效。');
  const bytes = Buffer.from(envelope.payload, 'base64');
  if (bytes.toString('base64') !== envelope.payload || !verify(null, bytes, keys[envelope.key_id], Buffer.from(envelope.signature, 'base64'))) {
    fail('研究组件更新签名无效。');
  }
  const release = JSON.parse(bytes.toString('utf8'));
  if (release.schema !== 1 || semver.valid(release.version) !== release.version || semver.prerelease(release.version)
      || !Number.isSafeInteger(release.sequence) || release.sequence < 1
      || !Number.isSafeInteger(release.expires) || (!installed && release.expires <= now)
      || !/^[a-f0-9]{40}$/.test(release.source_commit || '')
      || !/^[a-f0-9]{64}$/.test(release.wheel_sha256 || '')
      || !/^[a-f0-9]{64}$/.test(release.requirements_sha256 || '')
      || !Number.isSafeInteger(release.wheel_bytes) || release.wheel_bytes < 1 || release.wheel_bytes > 32 * 1024 ** 2
      || release.wheel !== `briefloop-${release.version}-py3-none-any.whl`
      || !semver.validRange(release.shell_range) || release.desktop_protocol !== 1 || release.data_epoch !== 1) {
    fail('研究组件更新清单无效或已过期。');
  }
  return release;
}
function compatible(release, {shellVersion, bundledLock}) {
  return semver.satisfies(shellVersion, release.shell_range, {includePrerelease: false})
    && release.requirements_sha256 === bundledLock;
}
function wheelURL(release) {
  return `${RELEASES}/download/v${release.version}/${release.wheel}`;
}
function wheelMatches(bytes, release) {
  return bytes.length === release.wheel_bytes && createHash('sha256').update(bytes).digest('hex') === release.wheel_sha256;
}
module.exports = {parseEnvelope, compatible, wheelURL, wheelMatches, FEED, RELEASES};
