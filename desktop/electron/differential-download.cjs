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
    // The cache index uses forward slashes on every host; readCache validates
    // this form, while path.relative emits backslashes on Windows.
    const relative = path.relative(directory, file).split(path.sep).join('/');
    await fs.writeFile(temporary, JSON.stringify({file: relative, size, sha256, map}), {flag: 'wx', mode: 0o600});
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
// Merge nearby changed regions with a small byte budget, then split large ranges
// so four requests can make progress without buffering whole responses in memory.
function transferPlan(operations, downloadKind, size) {
  const plan = [];
  let offset = 0, extra = 0;
  for (const part of operations) {
    const next = {...part, offset};
    offset += part.end - part.start;
    const gap = plan.at(-1), previous = plan.at(-2);
    const gapBytes = gap && gap.end - gap.start;
    if (part.kind === downloadKind && previous?.kind === downloadKind && gap.kind !== downloadKind &&
        gapBytes <= 64 * 1024 && extra + gapBytes <= Math.min(1024 * 1024, size * 0.01) &&
        part.end - previous.start <= 4 * 1024 * 1024) {
      extra += gapBytes;
      previous.end = part.end;
      plan.pop();
    } else plan.push(next);
  }
  return plan.flatMap(part => {
    if (part.kind !== downloadKind) return [part];
    const ranges = [];
    for (let start = part.start; start < part.end; start += 4 * 1024 * 1024) {
      ranges.push({...part, start, end: Math.min(part.end, start + 4 * 1024 * 1024), offset: start});
    }
    return ranges;
  });
}
async function reconstruct({cache, map, size, destination, request, progress}) {
  const {computeOperations, OperationKind} = require('electron-updater/out/differentialDownloader/downloadPlanBuilder');
  const operations = computeOperations(cache.map, map, {info() {}, warn() {}, debug() {}});
  const plan = transferPlan(operations, OperationKind.DOWNLOAD, size);
  const downloads = plan.filter(p => p.kind === OperationKind.DOWNLOAD);
  const total = downloads.reduce((n, p) => n + p.end - p.start, 0);
  if (total >= size * 0.9 || downloads.length > 512) throw new Error('little_reuse');
  let transferred = 0, completed = 0;
  const reused = size - total;
  const output = await fs.open(destination, 'wx', 0o600);
  const controller = new AbortController();
  let cursor = 0, failure;
  async function writePart(part, stream) {
    const length = part.end - part.start;
    let written = 0;
    for await (const chunk of stream) {
      if (controller.signal.aborted) throw controller.signal.reason;
      if (written + chunk.length > length) throw new Error('range_size');
      let consumed = 0;
      while (consumed < chunk.length) {
        const result = await output.write(chunk, consumed, chunk.length - consumed, part.offset + written + consumed);
        if (!result.bytesWritten) throw new Error('write_failed');
        consumed += result.bytesWritten;
      }
      written += chunk.length;
      completed += chunk.length;
      if (part.kind === OperationKind.DOWNLOAD) transferred += chunk.length;
      progress({percent: completed / size * 100, transferred, total, reused, mode: 'differential'});
    }
    if (written !== length) throw new Error('range_size');
  }
  async function worker() {
    try {
      while (!controller.signal.aborted && cursor < downloads.length) {
        const part = downloads[cursor++];
        const response = await request(part.start, part.end - 1, controller.signal);
        if (response.status !== 206 || response.headers.get('content-range') !== `bytes ${part.start}-${part.end - 1}/${size}` ||
            (response.headers.get('content-encoding') && response.headers.get('content-encoding') !== 'identity')) {
          await response.body?.cancel();
          throw new Error('range_unavailable');
        }
        await writePart(part, response.body);
      }
    } catch (error) {
      failure ||= error;
      controller.abort(failure);
    }
  }
  try {
    for (const part of plan.filter(p => p.kind === OperationKind.COPY)) {
      await writePart(part, createReadStream(cache.file, {start: part.start, end: part.end - 1}));
    }
    // Drain all workers before closing the file or allowing full-download fallback.
    await Promise.all(Array.from({length: Math.min(4, downloads.length)}, worker));
    if (failure) throw failure;
    if (completed !== size) throw new Error('reconstruction_size');
    await output.sync();
    return {percent: 100, transferred, total, reused, mode: 'differential'};
  } finally {controller.abort(); await output.close();}
}
module.exports = {readMap, validateMap, hashFile, readCache, saveCache, reconstruct};
