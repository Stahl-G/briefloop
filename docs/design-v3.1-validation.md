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

## Remaining verification and limits

- The managed browser blocked localhost and standalone Chromium could not create required sockets. Full live UI screenshot/keyboard/responsive verification remains pending; no alternate route bypassed those restrictions.
- Native macOS/Windows packaging, DMG Finder layout, Electron print UI, and Microsoft Word/Excel application behavior remain unverified. Asset formats/pixels and Electron harnesses are not substitutes for those checks.
- LibreOffice suppressed the first cover-page footer of the built-in template even though its first-page footer XML includes the notice. The document-end disclosure and ordinary-page footer were visibly present; no claim is made that every renderer displays every cover footer.
- Changing a report's presentation setting conservatively changes its review input snapshot and may require a new independent review. Review binding was not weakened.
- The AI/academe purple distinction awaits actual UI screenshots. Existing raster chart colors require regeneration to follow a changed convention.
- Labeling support follows the cited TC260 format guidance; it is not a claim of universal legal compliance or human review.
