'use strict';

const semver = require('semver');

const RELEASES = 'https://github.com/Stahl-G/briefloop/releases';
const MAX_ASSET = 4 * 1024 ** 3;

function invalid() {
  return Object.assign(new Error('官方更新清单无效，请稍后重试或查看官方发布。'), {
    code: 'invalid_release_manifest', retryable: false,
  });
}

function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

// The caller supplies the exact stable release tag from the trusted metadata
// redirect. Asset URLs are constructed here; the manifest cannot choose a host.
function parseReleaseManifest(value, {tag} = {}) {
  if (!record(value) || typeof value.version !== 'string'
      || semver.valid(value.version) !== value.version || semver.prerelease(value.version)
      || tag !== `v${value.version}` || typeof value.source_commit !== 'string'
      || !/^[a-f0-9]{40}$/i.test(value.source_commit) || !record(value.assets)) throw invalid();
  const entries = Object.entries(value.assets);
  if (!entries.length || entries.length > 100) throw invalid();
  const assets = entries.map(([name, item]) => {
    if (!/^[a-z0-9][a-z0-9._-]{0,254}$/i.test(name) || name.endsWith('.')
        || !record(item) || !Number.isSafeInteger(item.bytes) || item.bytes <= 0 || item.bytes > MAX_ASSET
        || typeof item.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(item.sha256)) throw invalid();
    return {name, size: item.bytes, digest: `sha256:${item.sha256.toLowerCase()}`,
      browser_download_url: `${RELEASES}/download/${encodeURIComponent(tag)}/${encodeURIComponent(name)}`};
  });
  return {tag_name: tag, draft: false, prerelease: false, html_url: `${RELEASES}/tag/${encodeURIComponent(tag)}`,
    body: '', assets};
}

module.exports = {parseReleaseManifest};
