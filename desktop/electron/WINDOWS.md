# Windows x64 desktop build

This installer packages the same Electron window, WebUI, Python service and Store
as the macOS build. It runs on native Windows x64; WSL and development-mode launch
are not installation acceptance.

From the repository root, build the shared frontend and prepare the locked Windows
runtime, then build the installer:

```powershell
npm.cmd ci
npm.cmd run build
python -X utf8 desktop/electron/scripts/prepare-runtime-windows.py
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
payload contains `resources/runtime/python/python.exe` and
`resources/runtime/node/node.exe`, the installed BriefLoop wheel, dependencies and
licenses. No Python, Node, npm, pip installation or runtime download is required
at application first launch. Model CLIs and credentials remain user-managed.

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
`--publish never`. The baseline configuration does not yet emit `latest.yml`;
update integration and native upgrade acceptance remain pending.
