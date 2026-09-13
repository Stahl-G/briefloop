'use strict';

// Share the application files and identity; only the native payload and installer
// differ from the macOS package. No separate renderer or Python application.
const shared = require('./package.json').build;
module.exports = {
  ...shared,
  extends: null,
  forceCodeSigning: false,
  extraResources: [{from: 'runtime/windows-x64', to: 'runtime'}],
  files: [...shared.files, '!electron-builder.windows.cjs'],
  win: {
    ...shared.win,
    target: [{target: 'nsis', arch: ['x64']}],
    icon: 'assets/Win.ico',
    artifactName: 'BriefLoop-Setup-${version}-${arch}.${ext}',
    signAndEditExecutable: true,
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowElevation: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    runAfterFinish: true,
    deleteAppDataOnUninstall: false,
    installerIcon: 'assets/Win.ico',
    uninstallerIcon: 'assets/Win.ico',
    uninstallDisplayName: 'BriefLoop',
  },
  // Emit updater metadata locally; build-windows.cjs always passes --publish never.
  publish: {provider: 'github', owner: 'Stahl-G', repo: 'briefloop', channel: 'latest', releaseType: 'release'},
};
