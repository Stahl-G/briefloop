# Bundled native engine

Pi is pinned to `@earendil-works/pi-coding-agent@1.0.0`. This SDK integration
loads only BriefLoop's explicit tools and bundled continuity hook. It does not
enable Pi's MCP, Codemode, personal credentials, skills or extension discovery.

Build from the repository root:

```sh
pnpm --dir native-engine install --frozen-lockfile
pnpm --dir native-engine exec tsc -p .
pnpm --dir native-engine audit
node native-engine/build.mjs
node native-engine/build.mjs --check
node --test native-engine/engine.test.mjs
```

The generated engine, image worker, Photon WASM, model metadata and license
notices all ship under `src/briefloop/static/`. The worker uses the installed
WASM beside it; it does not require `node_modules` or a build-machine path.
The build relocates these two SDK asset references without modifying installed
third-party sources. Wheel/sdist checks verify their exact bytes.

Only the native-engine project uses pinned pnpm 10.34.6; frontend and desktop
keep their existing npm workflow. For a one-off bootstrap, prefix the pnpm
commands with `npm exec --yes --package=pnpm@10.34.6 --`.

Pi 1.0's published npm shrinkwrap retains `brace-expansion@5.0.9`; npm root
overrides do not replace that nested dependency. The native pnpm lock and
override resolve every copy to `5.0.12`, including the development tree.
The esbuild alias and generated notices use the same patched version.
CI installs with the frozen lock and audits it without ignored advisories.

Cache warming is explicitly off: long child tools must not cause provider
calls outside BriefLoop's normal request/usage boundary. The image build
also clamps rounded dimensions to at least one pixel so thin images remain
attached. Both behavior regressions use the shipped bundle and a local provider.

`fixtures/pi-0.85.1-session.jsonl` was generated with the BriefLoop 0.28.2
released Pi 0.85.1 bundle and a local scripted provider, not fabricated from
the new SDK. It contains only synthetic text and no credentials. Its original
temporary cwd was normalized to `/synthetic/briefloop-pi-085`. The migration
test resumes it under the current restricted role and checks that saved work,
unresolved findings and the current tool contract reach the provider.
