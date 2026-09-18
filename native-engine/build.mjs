import { build } from "esbuild";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { collectFrontendLicenses, renderFrontendLicenses } from "../scripts/build_frontend_licenses.mjs";

// Bundle the engine into a single ESM file that ships inside the BriefLoop
// package (src/briefloop/static/). Node >=22.19 is provided by the launcher
// (Electron's embedded node in the desktop app, system/BRIEFLOOP_NODE for CLI).
// `--check` rebuilds in memory and fails when a committed artifact drifted, so
// the 6 MB minified bundle is never trusted without its source.
process.chdir(fileURLToPath(new URL(".", import.meta.url)));
const check = process.argv.includes("--check");
const outfile = "../src/briefloop/static/native-engine.mjs";

const result = await build({
  // esbuild fixes its default working directory at import, before chdir.
  absWorkingDir: process.cwd(),
  entryPoints: ["main.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  outfile,
  sourcemap: false,
  minify: true,
  metafile: true,
  write: false,
  banner: {
    js: [
      "// BriefLoop native engine — bundled from native-engine/ (pi SDK, MIT). Do not edit directly.",
      "// Third-party license texts: native-engine-licenses.txt.",
      "// pi's dependency graph contains CJS modules; give them a real require().",
      "import { createRequire as __blRequire } from 'node:module';",
      "const require = __blRequire(import.meta.url);",
    ].join("\n"),
  },
});

// Keep every bundled package's license text with the artifact, same rule the
// frontend bundle follows: missing license is a build failure, not an omission.
const rows = collectFrontendLicenses(result.metafile, ".", [
  // pi's scoped npm packages declare MIT but ship no license file; the text
  // is vendored from the source repository (github.com/earendil-works/pi).
  { match: /^@earendil-works\//, path: "third_party/pi/LICENSE" },
  // Other MIT-declared packages that ship no file get the canonical MIT text.
  { match: /./, license: "MIT", path: "third_party/mit-canonical.txt" },
]);
const artifacts = [
  ...result.outputFiles.map((file) => ({ path: file.path, contents: Buffer.from(file.contents) })),
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
