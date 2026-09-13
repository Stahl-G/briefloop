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
- In a separate synthetic lifecycle test app, force-stop only its recorded main
  PID. Verify the matching service and its recorded descendants exit, an
  unrelated CLI remains alive, and the same workspace can reopen paused without
  automatically resubmitting interrupted work. The backend must implement the
  `BRIEFLOOP_DESKTOP_OWNER_PIPE=1` stdin-EOF contract; shell-only tests cannot
  establish backend cleanup. Do not terminate the user's working app.
- Try an invalid target folder while a workspace is active: the current service
  and editor must remain available. Then exercise a valid target whose service
  fails to start; verify the original workspace recovers paused and the error
  remains visible. A target that has not exited must remain owned, not be hidden
  behind a second managed service.
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

## Release handoff and completion evidence

The release coordinator freezes one source commit and builds one backend wheel
for both desktop platforms. Windows packaging consumes that exact wheel and its
manifest; it must not rebuild or install a different backend under the same
version. Record the source commit, application version, backend version and
wheel SHA256, installer SHA256, and installed executable version separately.
Run `scripts/check_versions.py` with the available native artifacts and report
missing platform evidence explicitly.

After that freeze, Windows acceptance must cover the installed NSIS application
outside the checkout: startup in a Chinese/space workspace path, authorized
runtime discovery and one short actual generation, edit/save/reopen, Word export
and font inspection in WPS, normal cancellation and exit, and failed workspace
switch recovery. Preserve the synthetic owner-crash acceptance evidence alongside
these checks; it does not replace installed application or model execution tests.

Before publication, exercise download/install/restart from an available older
version using an isolated feed. Verify save/cancel gates, preserved workspace
data, and the installed version after restart. The coordinator then publishes
the already-verified installer, blockmap and `latest.yml`. Recheck the actual
GitHub release and stable update entry point after publication. Source version
changes and successful packaging alone do not make a Windows update available.
