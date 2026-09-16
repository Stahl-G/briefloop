import { build } from "esbuild";
import { cpSync, mkdirSync, writeFileSync } from "node:fs";
import { collectFrontendLicenses, renderFrontendLicenses } from "../scripts/build_frontend_licenses.mjs";

// Bundle the engine into a single ESM file that ships inside the BriefLoop
// package (src/briefloop/static/). Node >=22.19 is provided by the launcher
// (Electron's embedded node in the desktop app, system/BRIEFLOOP_NODE for CLI).
const outfile = "../src/briefloop/static/native-engine.mjs";
mkdirSync("../src/briefloop/static", { recursive: true });

const result = await build({
  entryPoints: ["main.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  outfile,
  sourcemap: false,
  minify: true,
  metafile: true,
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
writeFileSync(
  "../src/briefloop/static/native-engine-licenses.txt",
  renderFrontendLicenses(rows),
);
cpSync("models.json", "../src/briefloop/static/native-engine-models.json");

console.log(`built ${outfile} (${rows.length} license records)`);
