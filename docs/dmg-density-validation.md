# DMG background density validation

The installer icon centers are `(150, 210)` and `(390, 210)` in Finder points. The background uses a 540 × 380 pixel/point coordinate system, with its arrow at y=210 and installation instruction at y=320.

The original Inkscape PNG encoded 96 DPI. Finder displayed it at 405 × 285 points, scaling the arrow to y=157.5 and instruction baseline to y=240, while the icon centers remained at y=210. The instruction consequently overlapped the app icon. This was observed in the actual read-only 0.28.1 DMG, not inferred solely from a mockup.

The generator now normalizes the background to 72 DPI. The corrected PNG retains identical decoded RGBA pixels and dimensions, so Finder uses the intended 540 × 380 point layout. `design:check` verifies its physical-density metadata and rejects the original 96-DPI background.

Validation on macOS:

- Corrected PNG: 540 × 380, `sips` reports 72 DPI, decoded RGBA pixels identical to the original.
- `npm run design:check`: passed; replacing the PNG with the original 96-DPI file produces the expected failure.
- A separately named test DMG was built with the corrected background and the already-built App. No release package, wheel, tag, or published asset was replaced.
- Corrected Finder-window visual confirmation is pending: the Mac locked before this observation. Packaged App startup remains a separate verification step.

The existing 0.28.1 draft remains tied to its original frozen source and hashes. This change does not publish a release or change its version.
