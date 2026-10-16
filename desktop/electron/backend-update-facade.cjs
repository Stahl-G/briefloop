'use strict';
// One existing settings entry: full App updates take precedence. Compatible
// research-component releases use the same check/download/install actions.
function combinedUpdates({app, full, backend, environment}) {
  let research = false;
  return {
    status: () => research ? backend.status() : full.status(),
    check: async () => {
      research = false;
      const value = await full.check();
      if (value.state !== 'current') return value;
      const candidate = await backend.check(environment());
      research = ['available', 'downloaded', 'downloading', 'error'].includes(candidate.state);
      return research ? candidate : value;
    },
    download: () => research ? backend.download() : full.download(),
    installReady: async () => {
      if (!research) return full.installReady();
      await backend.activate();
      app.relaunch();
      return {mode: 'backend', requested: true};
    },
  };
}
module.exports = {combinedUpdates};
