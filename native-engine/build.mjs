import { build } from "esbuild";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { collectFrontendLicenses, renderFrontendLicenses } from "../scripts/build_frontend_licenses.mjs";

// Bundle the engine and Pi's image worker into ESM files inside the BriefLoop
// package (src/briefloop/static/). Node >=22.19 is provided by the launcher
// (Electron's embedded node in the desktop app, system/BRIEFLOOP_NODE for CLI).
// `--check` rebuilds in memory and fails when a committed artifact drifted, so
// the generated bundles are never trusted without their source.
process.chdir(fileURLToPath(new URL(".", import.meta.url)));
const check = process.argv.includes("--check");
const outfile = "../src/briefloop/static/native-engine.mjs";
const require = createRequire(import.meta.url);
const piDist = dirname(fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent")));
const photonSource = require.resolve("@silvia-odwyer/photon-node", { paths: [piDist] });
function patchSdkSource(path, replacements) {
  let contents = readFileSync(path, "utf8");
  for (const [source, target] of replacements) {
    if (contents.split(source).length !== 2) {
      throw new Error(`SDK source layout changed: ${path}`);
    }
    contents = contents.replace(source, target);
  }
  return { contents, loader: "js" };
}
// esbuild does not relocate worker URLs or Photon's CJS __dirname-based WASM
// read. Keep these SDK assets beside the installed bundle, including wheels
// with no node_modules and Electron using its embedded Node runtime.
const relocateImages = {
  name: "briefloop-image-assets",
  setup(build) {
    build.onLoad({ filter: /[/\\]image-resize\.js$/ }, ({ path }) =>
      patchSdkSource(path, [['"./image-resize-worker.js"', '"./native-engine-image-worker.mjs"']]));
    build.onLoad({ filter: /[/\\]photon_rs\.js$/ }, ({ path }) =>
      patchSdkSource(path, [["require('path').join(__dirname, 'photon_rs_bg.wasm')",
        '__blAssetUrl("./native-engine-photon.wasm")']]));
    // Pi 1.0 rounds a thin image's short side to zero before Photon resize.
    // Keep both dimensions positive; installed SDK sources remain untouched.
    build.onLoad({ filter: /[/\\]image-resize-core\.js$/ }, ({ path }) =>
      patchSdkSource(path, [
        ['targetHeight = Math.round((targetHeight * opts.maxWidth) / targetWidth);',
          'targetHeight = Math.max(1, Math.round((targetHeight * opts.maxWidth) / targetWidth));'],
        ['targetWidth = Math.round((targetWidth * opts.maxHeight) / targetHeight);',
          'targetWidth = Math.max(1, Math.round((targetWidth * opts.maxHeight) / targetHeight));'],
      ]));
  },
};

const buildOptions = {
  // esbuild fixes its default working directory at import, before chdir.
  absWorkingDir: process.cwd(),
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  sourcemap: false,
  minify: true,
  metafile: true,
  write: false,
  plugins: [relocateImages],
  // Keep the bundle on the same patched version as the pnpm lock/override.
  alias: { "brace-expansion": fileURLToPath(import.meta.resolve("brace-expansion")) },
  banner: {
    js: [
      "// BriefLoop native engine — bundled from native-engine/ (pi SDK, MIT). Do not edit directly.",
      "// Third-party license texts: native-engine-licenses.txt.",
      "// pi's dependency graph contains CJS modules; give them a real require().",
      "import { createRequire as __blRequire } from 'node:module';",
      "const require = __blRequire(import.meta.url);",
      "const __blAssetUrl = (name) => new URL(name, import.meta.url);",
    ].join("\n"),
  },
};
const results = await Promise.all([
  build({ ...buildOptions, entryPoints: ["main.ts"], outfile }),
  build({ ...buildOptions, entryPoints: [join(piDist, "utils/image-resize-worker.js")],
    outfile: "../src/briefloop/static/native-engine-image-worker.mjs" }),
]);
const metafile = { outputs: Object.assign({}, ...results.map(r => r.metafile.outputs)) };

// Keep every bundled package's license text with the artifact, same rule the
// frontend bundle follows: missing license is a build failure, not an omission.
const rows = collectFrontendLicenses(metafile, ".", [
  // pi's scoped npm packages declare MIT but ship no license file; the text
  // is vendored from the source repository (github.com/earendil-works/pi).
  { match: /^@earendil-works\//, path: "third_party/pi/LICENSE" },
  // Other MIT-declared packages that ship no file get the canonical MIT text.
  { match: /./, license: "MIT", path: "third_party/mit-canonical.txt" },
]);
const artifacts = [
  ...results.flatMap(r => r.outputFiles).map((file) => ({ path: file.path, contents: Buffer.from(file.contents) })),
  { path: "../src/briefloop/static/native-engine-photon.wasm", contents: readFileSync(join(dirname(photonSource), "photon_rs_bg.wasm")) },
  { path: "../src/briefloop/static/native-engine-licenses.txt", contents: Buffer.from(renderFrontendLicenses(rows)) },
  { path: "../src/briefloop/static/native-engine-models.json", contents: readFileSync("models.json") },
];
let stale = false;
for (const artifact of artifacts) {
  if (check) {
    if (!existsSync(artifact.path) || !readFileSync(artifact.path).equals(artifact.contents)) {
      console.error(`Outdated generated file: ${artifact.path}; run node native-engine/build.mjs`);
      stale = true;
    }
  } else {
    mkdirSync("../src/briefloop/static", { recursive: true });
    writeFileSync(artifact.path, artifact.contents);
  }
}
if (stale) process.exitCode = 1;
else console.log(`native engine ${check ? "verified" : "built"} (${rows.length} license records)`);
