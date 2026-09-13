# Windows x64 runtime

Run from a Windows x64 checkout with Python 3.11 or newer:

```powershell
python -X utf8 desktop/electron/scripts/prepare-runtime-windows.py
```

The output is `desktop/electron/runtime/windows-x64`. Electron should include the
whole directory as application resources, start `python/python.exe -I -X utf8 -m
briefloop`, and set `BRIEFLOOP_NODE` to the bundled `node/node.exe`. Python, Node,
and dependencies are installed during this build. Application startup does not
invoke pip, npm, or an installer, and does not require a host Python or Node.

The Windows-specific lock pins CPython 3.13.15 from the 20260901 upstream
python-build-standalone release, Node 22.23.2 from nodejs.org, and every Windows
dependency wheel by filename, HTTPS URL, version, and SHA-256. It includes the
Windows-only pywin32 dependency. Source checksums are linked in the lock. The
business wheel is rebuilt from this checkout on every build, including current
source changes, and installed without editable links or dependency resolution.

After one successful download, `--offline` requires all verified artifacts in
`desktop/electron/.cache/windows-runtime`; absent or mismatched artifacts fail
before installation. Dependency installation uses pip `--no-index`,
`--require-hashes`, and a local wheel directory. A change to project requirements
requires review and regeneration of the Windows lock; do not loosen the hash
checks or reuse another platform's native wheels.

The bundle preserves installed license files and distribution RECORDs, collects
package licenses under `licenses/python-packages`, includes Python distribution
licenses/PYTHON.json and Node's license, and records the business wheel hash in
`manifest.json`. Windows console EXEs with absolute build paths are replaced by
relative `.cmd` launchers. The application's primary entry remains `python -m
briefloop`. RECORD hashes are refreshed after launcher normalization.

The preparation script automatically runs `verify-runtime-windows.py`. It moves
the artifact to an unrelated path containing Chinese characters and spaces,
removes host Python/Node settings and PATH entries, verifies imports including
native DLL packages, checks every installed RECORD, runs the BriefLoop CLI and
pip's dependency checker, and executes bundled Node. It restores the build
directory even if verification fails and writes `relocation-proof.json` on
success. This verifies packaging and relocation, not model authentication or
runtime-specific paid API access.

ZIP/TAR extraction rejects traversal, drive/ADS paths, Windows device aliases,
duplicate case-insensitive paths, links, and special entries. The fixed upstream
Python full archive is used only for license extraction via Windows `tar` stdout;
the builder chooses and validates every destination. Generated runtimes/caches
are build outputs and must not be committed.
