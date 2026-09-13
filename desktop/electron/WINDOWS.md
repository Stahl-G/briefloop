# Windows x64 desktop build

This installer packages the same Electron window, WebUI, Python service and Store
as the macOS build. It runs on native Windows x64; WSL and development-mode launch
are not installation acceptance.

From native Windows PowerShell, build the shared frontend and backend wheel,
then build the installer. Use an installed Python 3.11+ for the build:

```powershell
npm.cmd ci
npm.cmd run build
py -3 -X utf8 desktop/electron/scripts/prepare-backend.py
Set-Location desktop/electron
npm.cmd ci
node scripts/build-windows.cjs
```

`--dir` builds the unpacked staging app only. The default produces an assisted,
per-user NSIS installer at `dist/BriefLoop-Setup-<version>-x64.exe`. It allows an
installation directory choice and creates Desktop and Start menu shortcuts.
No administrator installation, signing or publishing is performed by this
build. `dist/windows-artifact.json` records the exact source HEAD, dirty state,
installer SHA-256 and size; a dirty artifact is not a reproducible release claim.

The approved shared `assets/Win.ico` is used for application, installer and
uninstaller icons. Do not replace it with a generated or fallback icon. The app
payload contains `resources/backend/manifest.json` and the verified BriefLoop
wheel. It reuses Electron's internal Node instead of shipping another Node or
Python interpreter. First launch detects an existing Python 3.11+, offers the
official Python download page when missing, and installs dependencies into an
App-owned venv under user data. First-time dependency preparation requires a
network connection; it does not install packages into the host Python. Model
CLIs and credentials remain user-managed. The NSIS installer and service launch
do not require changing PowerShell execution policy.

## Native acceptance

Use a fresh current-version synthetic workspace. Historical workspace migration
and cross-version upgrade compatibility are outside this pre-release scope.

- Run the NSIS installer and launch the installed app outside the source tree.
- Create and open a workspace under a Chinese/space path; verify installed CLI
  discovery and a short real conversation with the existing authorized account.
- Open a synthetic report, edit and save; export Word through the native Save As
  dialog and inspect it in installed Office/WPS, including Chinese fonts.
- With a model task running, close the window and test both continue and stop
  choices. Verify actual owned process exit and workspace lock release.
- Reopen the same-version workspace and verify the saved report and exports.
- Verify Desktop, Start menu and taskbar icons. Uninstall without deleting the
  workspace, then verify installed app removal and preservation of report data.

Record exact artifact HEAD/SHA, installed paths, UI evidence and outstanding
limitations separately. HTTP status or unit tests alone do not establish these
results; do not mark unexecuted acceptance steps as passed.

## Update integration

The shared Electron updater owns version checks, download progress and the
save/running-task gate before install and restart. Windows supplies NSIS update
artifacts. Controlled local two-version acceptance must use a separate test feed;
it must not upload to the official GitHub Releases feed. Packaging always uses
`--publish never`. The GitHub provider configuration generates `latest.yml` and
the installer blockmap locally; it does not create a GitHub release. Shared
updater integration and native upgrade acceptance remain pending.
