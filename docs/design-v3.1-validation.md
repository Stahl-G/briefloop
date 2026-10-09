# Design v3.1 implementation validation

Status: unshipped candidate, based on `a172b006` (0.28.0). No application version bump, release, or daily installation replacement.

## Implemented

- Supplied v3.1 tokens and original SVG; the SVG metadata is retained. Platform icons preserve their existing silhouettes and use the new mark geometry. DMG artwork has reproducible source and packaging configuration.
- One-version `--green` / `--green-hover` aliases point to the new brand aliases. Retired colors are rejected in all tracked and non-ignored new text files, case-insensitively, including Word-style values without `#`; binary Office/icon outputs have separate checks.
- Action-only process disclosures, stable numbered citations, focus/hover source cards, and source-rail positioning. Original model reasoning/runtime messages are excluded from the action projection.
- Report-local market convention, language defaults, explicit delta-only foreground colors, export metadata, and chart-authoring guidance. Existing raster figures are not recolored.
- Positive-provenance AI labeling survives user revisions; human-only imported content is not asserted to be AI-generated. Word/Excel/HTML visible notices and format-specific metadata are present. Desktop PDF finalization writes metadata through the authenticated local service. Browser print retains the visible notice but cannot guarantee PDF metadata.

## Checks

- `npm run design:check`, `npm run build:check`, and `git diff --check`: passed.
- Full frontend suite: 226 passed, 3 skipped.
- Full Electron unit/harness suite: 91 passed, 5 skipped.
- Consolidated relevant Python suite: 114 passed, 13 platform-specific skips.
- Actual synthetic plain/template DOCX and single/multi-sheet XLSX files were created, reopened, inspected at the OOXML package level, and rendered through LibreOffice for visual inspection. Visible disclosure, positive/negative/flat delta colors and neutral ordinary amounts were checked.
- Independent read-only review found and verified fixes for three P2 issues: mixed-direction spreadsheet prose, merged-grid header inference, and an invalid legacy table-selection CSS value. No remaining concrete P1/P2 was found in that review.

## MBP verification (2026-10-01)

- The transferred Git bundle SHA256 matched `8f12fd58d65de56da1fae53aa16bd6536ec8f9ee27636d1c240d6910070fc340`. A separate clean clone consumed commit `cd53320`; the earlier checkout and its uncommitted changes were preserved.
- Frontend build regenerated identical committed output; `build:check`, `design:check` and whitespace checks passed. Frontend: 226 passed / 3 skipped. Relevant Python: 81 passed (three local-service tests rerun with authorized socket access). Electron on Mac and the CI Node 20 runtime: 92 passed / 4 skipped.
- Word and Excel were generated and downloaded using actual UI controls. Both reopened as valid OOXML packages and retained `BriefLoopMarketConvention=intl`; the bundled example correctly had no `AIGC` property. A separately labelled artificial agent-provenance fixture was also downloaded as Word via the UI: body and all footer variants contain the notice, the package carries AIGC and international metadata, and its positive/negative table deltas use green/red respectively. These are package checks, not a claim of native Office visual verification.
- [Process fixtures](screenshots/design-v31/process-fixture.jpg) document the default disclosure states using artificial completed-job records.
- [Citation card](screenshots/design-v31/citation-card.jpg) and [report with actual completed exports](screenshots/design-v31/report-and-export.jpg) show the real product UI and software-provided synthetic material. No model was called, and no private report/account/key appears.
- [PR #881 CI](https://github.com/Stahl-G/briefloop/actions/runs/36857551124) completed successfully for implementation commit `cd53320`: test, native-engine, macos-process-tree and windows-native all passed.

## Remaining verification and limits

- Live desktop UI was subsequently verified on MBP through Chrome against an isolated synthetic workspace: report-local international colors saved and survived reload/reopening; ArrowDown opened the numbered citation source card with focus on the original-source action; the source-rail action positioned and focused its matching source. Responsive/mobile product verification remains pending. The live recent-task view additionally verified a completed normal process collapsed by default, a conflicting process expanded with the conflict first, and Enter toggling the normal process. These task records were explicitly synthetic fixtures, not actual model runs; their raw thinking field did not appear in the UI.
- Native macOS/Windows packaging, DMG Finder layout, Electron print UI, and Microsoft Word/Excel application behavior remain unverified. Microsoft Word/Excel and Inkscape are not installed in this MBP environment; WPS native UI connection timed out before a document was opened. No installed daily app was replaced. Asset formats/pixels and Electron harnesses are not substitutes for those checks.
- LibreOffice suppressed the first cover-page footer of the built-in template even though its first-page footer XML includes the notice. The document-end disclosure and ordinary-page footer were visibly present; no claim is made that every renderer displays every cover footer.
- Changing a report's presentation setting conservatively changes its review input snapshot and may require a new independent review. Review binding was not weakened.
- The live screenshots show the existing AI purple in context; they do not establish a side-by-side academic-category comparison, so both existing purples remain unchanged. Existing raster chart colors require regeneration to follow a changed convention.
- Labeling support follows the cited TC260 format guidance; it is not a claim of universal legal compliance or human review.
