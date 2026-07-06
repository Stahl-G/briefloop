# BriefLoop Release Checklist

This checklist is an operator document for release preparation. It is not a
capability claim, benchmark claim, or roadmap commitment. Passing this checklist
means the release mechanics and public wording were checked; it does not prove
semantic truth, output-quality improvement, automatic ready-to-send delivery, or
publication readiness.

Use it after the implementation line is already reviewed. Do not use it to rush
unfinished product work into a release.

## 1. Version, Tag, And Release Source Files

Confirm the release version is represented consistently in the release source
files before publishing:

```bash
python3 scripts/check_version_consistency.py
python3 scripts/check_release_consistency.py --no-tag
```

Before creating a public release, check the version-bearing files that are
expected to change together:

- `VERSION`
- `pyproject.toml`
- `README.md`
- `README.zh-CN.md`
- `README_en.md` compatibility pointer shape
- `CHANGELOG.md`
- Hermes skill metadata and runtime asset parity when applicable
- `Formula/multi-agent-brief.rb` when Homebrew/formula metadata is published
  for this release

If a branch claims a released version, the corresponding tag and GitHub release
must exist before the release is called published.

Release-prep commits may leave `Formula/multi-agent-brief.rb` on the last
published archive until the new tag exists. Do not point the formula at an
unpublished tag or a placeholder checksum. After the tag/archive exists, update
the formula in the same release flow or in a post-tag packaging PR with the real
archive checksum.

## 2. Required Release Guards

Run the release/readiness guards from a clean checkout:

```bash
python3 scripts/check_release_consistency.py --no-tag
python3 scripts/check_product_baseline.py
python3 scripts/check_briefloop_skill_freshness.py
python3 scripts/check_launch_smoke.py
python3 scripts/check_launch_smoke.py --json
git diff --check
```

For a v1.0 release, also require the RC readiness and pilot evidence gates to be
satisfied:

```bash
python3 scripts/check_v1_rc_readiness.py --require-satisfied
python3 scripts/check_v1_pilot_evidence.py --require-satisfied
```

Without `--require-satisfied`, `check_release_consistency.py --no-tag` only
verifies that the v1.0 pilot evidence record exists, is public-safe in shape,
and honestly states its current status.

When the release includes a generated reference pack, demo bundle, launch pack,
or other public artifact outside the normal git-tracked file set, also scan the
actual candidate artifact paths before publishing:

```bash
MABW_PUBLIC_SAFETY_BANNED_TERMS="<private-term-1>,<private-term-2>" \
  python3 scripts/check_public_safety.py \
    --path <candidate-reference-pack-or-demo-bundle>
```

Use as many `--path` arguments as needed. This explicit path scan is separate
from the default tracked-file public-safety scan and is required when the thing
being published is generated, archived, or otherwise not represented exactly by
tracked source files.

`check_release_consistency.py --no-tag` delegates to the product baseline,
skill freshness, minimal comparative evaluation packet, public-safety, and
launch-smoke guards. Running the component scripts directly is still useful
when diagnosing a failure because their output is narrower.

The launch smoke is a setup/runtime-handoff and deterministic-demo check. It
creates temporary workspaces outside the repo and verifies import, CLI version,
demo init, doctor, runtime handoff, and the API-free demo artifact package. It
does not call an LLM, require an API key, run subagents, judge output quality,
or approve delivery.

## 3. Public-Claim Guard

Public docs, README text, release notes, launch notes, and demos must stay
within the artifact-supported boundary. Required language:

- traceability, not semantic proof
- measurement infrastructure, not a benchmark claim
- no output-quality improvement proof unless a specific public artifact supports
  that exact claim
- human-triggered delivery

Forbidden release-claim categories include:

- truth proof;
- hallucination elimination;
- automatic ready-to-send reports;
- Python judgment of prose quality, semantic manifestation, or factual
  regression;
- benchmark-win proof from experiment/evaluation packets.

Run:

```bash
python3 scripts/check_product_baseline.py
```

If the guard passes but a new public sentence still sounds stronger than the
artifact can support, narrow the sentence anyway.

## 4. GitHub Release And Package Metadata

Before calling a release complete:

```bash
git tag --list "v<version>"
gh release view "v<version>"
```

If the release uses package/archive metadata, verify those surfaces too:

- GitHub release exists and points at the intended tag.
- Source archive URL references the same tag.
- Homebrew formula URL references the same tag when formula metadata is
  published for this release.
- Formula checksum matches the published archive when formula metadata is
  published for this release.
- PyPI / pipx install instructions only appear after the package-index artifact
  exists and has passed a published-artifact smoke.
- Any package-index or install instructions point at a real published artifact,
  not a local branch or private path.

Do not update formula/package metadata ahead of the tag or release archive.

For pipx / PyPI prep, follow
[`docs/packaging-pipx.md`](packaging-pipx.md). The short version:

- do not add `pipx install briefloop` to first-user docs until the distribution
  name exists on the package index;
- verify `pyproject.toml` metadata and both console scripts,
  `briefloop` and `multi-agent-brief`;
- install the built wheel in a fresh virtual environment before publishing;
- after publishing, verify the real `pipx install ...` command from a clean
  environment.

## 5. Public-Safe Evidence Links

When release notes or launch docs cite evidence, verify each link exists and
the linked document states its boundary:

```bash
test -f docs/reference-runs/v0.7.4-organoid-failure-study.md
test -f docs/evaluation-results/v0.11.4-minimal-comparative-evaluation/README.md
test -f docs/reference-runs/v0.11.3-product-os-reader-quality-reference.md
```

Evidence links may support process traceability, guard behavior, or a bounded
public-safe observation. They must not be presented as semantic proof,
output-quality proof, or release approval.

## 6. Do Not Expand Scope During Release Prep

Do not use this checklist as a reason to add new product scope during release
prep. In particular, do not:

- complete issue #156 Evidence Extraction Mode;
- add a new warning surface;
- add Studio, TypeScript, React, or UI work;
- add semantic proof, quality score, support-sufficiency judgment, or release
  authority;
- rush an 080 formal experiment before release;
- treat #357 or another unrelated slice as a launch blocker unless it is
  already naturally fixed, reviewed, and merged.

If a late issue is truly release-blocking, open a narrow hotfix PR. Otherwise,
record it as a known limitation or follow-up.

## 7. Release Record

Record the final state in the release notes or an internal release log:

```text
Version:
Commit:
Tag:
GitHub release:
Release consistency: PASS / FAIL
Product baseline: PASS / FAIL
Launch smoke: PASS / FAIL
Public-claim guard: PASS / FAIL
GitHub release exists: PASS / FAIL
Formula/package metadata: PASS / FAIL / NOT APPLICABLE
PyPI/pipx published-artifact smoke: PASS / FAIL / NOT APPLICABLE
Known non-blocking follow-ups:
```

Only mark the release complete after the code line, public wording, tag/release,
and package metadata agree.
