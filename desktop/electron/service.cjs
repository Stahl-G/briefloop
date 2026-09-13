'use strict';
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function validateMarker(marker, child, launchId) {
  const url = new URL(marker.url);
  if (marker.pid !== child.pid || marker.launch_id !== launchId || !marker.workspace_id ||
      url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.pathname !== '/' || url.username || url.password || url.search || url.hash) {
    throw Error('工作区服务身份不匹配。');
  }
  return url.origin;
}

class WorkspaceService {
  constructor(runtime, onExit = () => {}) { this.runtime = runtime; this.onExit = onExit; this.child = null; this.info = null; }
  async start(directory, {create = false} = {}) {
    if (this.child) throw Error('请先关闭当前工作区。');
    if (!path.isAbsolute(directory)) throw Error('请选择完整的本地工作区路径。');
    if (create) await fs.mkdir(directory, {recursive: true});
    directory = await fs.realpath(directory);
    if (!(await fs.stat(directory)).isDirectory()) throw Error('工作区必须是文件夹。');
    if (!create) await fs.access(path.join(directory, 'briefloop.db')).catch(() => { throw Error('这个文件夹还不是 BriefLoop 工作区，请使用“新建工作区”。'); });
    const python = path.join(this.runtime, 'python', 'bin', 'python3');
    const node = path.join(this.runtime, 'node', 'bin', 'node');
    await Promise.all([fs.access(python, 1), fs.access(node, 1)]);
    const launchId = randomUUID();
    const log = await fs.open(path.join(directory, 'desktop-server.log'), 'a', 0o600);
    const env = {...process.env, BRIEFLOOP_LAUNCH_ID: launchId, BRIEFLOOP_NODE: node,
      PATH: `${path.dirname(node)}:${process.env.PATH || '/usr/bin:/bin'}`, PYTHONNOUSERSITE: '1', PYTHONUNBUFFERED: '1'};
    delete env.PYTHONHOME; delete env.PYTHONPATH; delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn(python, ['-m', 'briefloop', 'serve', '--workspace', directory, '--port', '0', '--paused'],
      {cwd: directory, env, stdio: ['ignore', log.fd, log.fd], windowsHide: true});
    this.child = child; this.directory = directory;
    this.exited = new Promise(resolve => child.once('close', (code, signal) => { this.child = null; this.info = null; resolve({code, signal}); this.onExit({code, signal}); }));
    let spawnError; child.once('error', error => { spawnError = error; });
    await log.close();
    try {
      const deadline = Date.now() + 45000;
      while (Date.now() < deadline) {
        if (spawnError) throw spawnError;
        if (child.exitCode !== null || child.signalCode !== null) throw Error('工作区服务启动失败，请查看工作区内的 desktop-server.log。已有服务和数据已保留。');
        let marker;
        try { marker = JSON.parse(await fs.readFile(path.join(directory, 'server.json'), 'utf8')); } catch {}
        if (marker?.launch_id === launchId) {
          const url = validateMarker(marker, child, launchId);
          const runtime = await this.requestAt(url, '/api/runtime');
          if (runtime.server_pid !== child.pid) throw Error('工作区进程身份验证失败。');
          this.info = {...marker, url};
          this.token = (await this.request('/api/session')).token;
          return {path: directory, url};
        }
        await sleep(100);
      }
      throw Error('工作区启动超时，请查看 desktop-server.log。');
    } catch (error) {
      if (this.child === child && child.pid) { child.kill('SIGTERM'); await Promise.race([this.exited, sleep(10000)]); }
      throw error;
    }
  }
  async requestAt(url, route, body) {
    const response = await fetch(url + route, {method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-BriefLoop-Token': this.token},
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000)});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || `工作区请求失败（${response.status}）。`);
    return data;
  }
  request(route, body) { if (!this.info) throw Error('工作区服务尚未就绪。'); return this.requestAt(this.info.url, route, body); }
  async status() {
    const status = await this.request('/api/service-status');
    if (status.pid !== this.child?.pid || status.workspace_id !== this.info.workspace_id) throw Error('工作区服务身份已变化。');
    return status;
  }
  async stop({cancelBusy = false} = {}) {
    if (!this.child) return;
    if (!this.info) throw Error('工作区服务尚未就绪，请稍后重试。');
    const info = this.info;
    await this.request('/api/service-stop', {pid: info.pid, workspace_id: info.workspace_id, ...(cancelBusy ? {busy_action: 'cancel'} : {})});
    const done = await Promise.race([this.exited.then(() => true), sleep(90000).then(() => false)]);
    if (!done) throw Error('工作区仍在保存或停止任务，窗口已保留，请稍后重试。');
    // Remove only our own stale marker, after the owned child has really exited.
    try { const marker = JSON.parse(await fs.readFile(path.join(this.directory, 'server.json'), 'utf8'));
      if (marker.launch_id === info.launch_id && marker.pid === info.pid) await fs.unlink(path.join(this.directory, 'server.json'));
    } catch {}
  }
}
module.exports = {WorkspaceService, validateMarker};
