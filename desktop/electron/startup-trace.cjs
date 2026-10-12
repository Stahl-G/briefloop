'use strict';

// Opt-in timing only: no workspace paths, IPC arguments or console payloads.
function createStartupTrace(enabled) {
  const started = process.hrtime.bigint();
  return (stage, fields = {}) => {
    if (!enabled) return;
    console.error(JSON.stringify({kind: 'briefloop-startup', stage,
      ms: Number(process.hrtime.bigint() - started) / 1e6, ...fields}));
  };
}

function observeStartup(window, trace) {
  trace('window-created');
  for (const event of ['show', 'focus', 'ready-to-show', 'resize']) window.on(event, () => trace(event));
  const contents = window.webContents;
  for (const event of ['did-start-loading', 'dom-ready', 'did-finish-load']) contents.on(event, () => trace(event));
  contents.on('preload-error', () => trace('preload-error'));
  contents.on('render-process-gone', (_event, details) => trace('renderer-exit', {reason: details.reason}));
  contents.on('console-message', details => {
    const prefix = '[BriefLoop startup] ';
    if (details.message?.startsWith(prefix)) {
      const marker = details.message.slice(prefix.length);
      if (/^[a-z-]{1,40}(?::[a-z-]{1,40}){0,2}$/.test(marker)) trace(marker);
    }
  });
}

module.exports = {createStartupTrace, observeStartup};
