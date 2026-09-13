'use strict';
const fs = require('node:fs/promises');
const {createReadStream} = require('node:fs');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const path = require('node:path');
const MAX_MAP = 16 * 1024 * 1024;

function validateMap(map, size) {
  const file = map?.files?.[0];
  if (map?.version !== '2' || map.files.length !== 1 || file.name !== 'file' || file.offset !== 0 ||
      !Array.isArray(file.sizes) || !file.sizes.length || file.sizes.length > 524288 ||
      !Array.isArray(file.checksums) || file.checksums.length !== file.sizes.length ||
      !file.sizes.every(n => Number.isSafeInteger(n) && n > 0 && n <= 1024 * 1024) ||
      !file.checksums.every(s => typeof s === 'string' && /^[A-Za-z0-9+/]{24}$/.test(s)) ||
      file.sizes.reduce((a, b) => a + b, 0) !== size) throw new Error('invalid_blockmap');
  return map;
}
async function readMap(response, size) {
  const chunks = []; let length = 0;
  for await (const chunk of response.body) {
    length += chunk.length;
    if (length > MAX_MAP) throw new Error('blockmap_too_large');
    chunks.push(Buffer.from(chunk));
  }
  return validateMap(JSON.parse(zlib.gunzipSync(Buffer.concat(chunks), {maxOutputLength: MAX_MAP}).toString('utf8')), size);
}
async function hashFile(file, algorithm = 'sha256') {
  const hash = crypto.createHash(algorithm);
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}
async function readCache(directory) {
  try {
    const index = path.join(directory, 'differential-cache.json');
    const stat = await fs.lstat(index);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_MAP) return null;
    const cache = JSON.parse(await fs.readFile(index, 'utf8'));
    if (!/^download-[A-Za-z0-9]+\/BriefLoop-\d+\.\d+\.\d+-arm64(?:\.dmg|-mac\.zip)$/.test(cache.file) ||
        !/^[a-f0-9]{64}$/.test(cache.sha256) || !Number.isSafeInteger(cache.size)) return null;
    const file = path.join(directory, cache.file);
    const parent = await fs.lstat(path.dirname(file));
    const payload = await fs.lstat(file);
    if (!parent.isDirectory() || parent.isSymbolicLink() || !payload.isFile() || payload.isSymbolicLink() || payload.size !== cache.size) return null;
    validateMap(cache.map, cache.size);
    if (await hashFile(file) !== cache.sha256) return null;
    return {...cache, file};
  } catch {return null;}
}
async function saveCache(directory, file, size, sha256, map, previous = null) {
  // Only store an index after complete payload verification. Unique temporary file
  // plus rename makes interrupted writes leave the previous baseline usable.
  const temporary = path.join(directory, `cache-${crypto.randomUUID()}.part`);
  try {
    await fs.writeFile(temporary, JSON.stringify({file: path.relative(directory, file), size, sha256, map}), {flag: 'wx', mode: 0o600});
    await fs.rename(temporary, path.join(directory, 'differential-cache.json'));
    if (previous && previous.file !== file) {
      // Remove only the verified previous baseline, never scan unrelated downloads.
      // Only our prepared application folder is disposable; leave unrelated files alone.
      await fs.rm(path.join(path.dirname(previous.file), 'prepared'), {recursive: true, force: true}).catch(() => {});
      await fs.unlink(previous.file).catch(() => {});
      await fs.rmdir(path.dirname(previous.file)).catch(() => {});
    }
  } finally {await fs.rm(temporary, {force: true});}
}
async function reconstruct({cache, map, size, destination, request, progress}) {
  const {computeOperations, OperationKind} = require('electron-updater/out/differentialDownloader/downloadPlanBuilder');
  const plan = computeOperations(cache.map, map, {info() {}, warn() {}, debug() {}});
  const downloads = plan.filter(p => p.kind === OperationKind.DOWNLOAD);
  const total = downloads.reduce((n, p) => n + p.end - p.start, 0);
  if (total >= size * 0.9 || downloads.length > 512) throw new Error('little_reuse');
  let transferred = 0, completed = 0;
  const reused = size - total;
  const output = await fs.open(destination, 'wx', 0o600);
  try {
    for (const part of plan) {
      const length = part.end - part.start;
      let stream;
      if (part.kind === OperationKind.COPY) stream = createReadStream(cache.file, {start: part.start, end: part.end - 1});
      else {
        const response = await request(part.start, part.end - 1);
        if (response.status !== 206 || response.headers.get('content-range') !== `bytes ${part.start}-${part.end - 1}/${size}` ||
            (response.headers.get('content-encoding') && response.headers.get('content-encoding') !== 'identity')) {
          await response.body?.cancel(); throw new Error('range_unavailable');
        }
        stream = response.body;
      }
      let written = 0;
      for await (const chunk of stream) {
        written += chunk.length;
        if (written > length) throw new Error('range_size');
        await output.writeFile(chunk);
        completed += chunk.length;
        if (part.kind === OperationKind.DOWNLOAD) transferred += chunk.length;
        progress({percent: completed / size * 100, transferred, total, reused, mode: 'differential'});
      }
      if (written !== length) throw new Error('range_size');
    }
    if (completed !== size) throw new Error('reconstruction_size');
    await output.sync();
    return {percent: 100, transferred, total, reused, mode: 'differential'};
  } finally {await output.close();}
}
module.exports = {readMap, validateMap, hashFile, readCache, saveCache, reconstruct};
