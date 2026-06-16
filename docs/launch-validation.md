# Launch Validation Checklist

This checklist is for two manual checks before wider public promotion:

1. The maintainer runs one real weekly brief from the golden path.
2. A pilot user starts from a fresh clone or fresh install and runs a demo or
   real brief.

It is not a feature spec and does not define new runtime behavior.

## 1. Golden Path Self-Test

Goal: verify that [MABW Golden Path](golden-path.md) lets the maintainer finish
a real brief without reading code or relying on private memory.

### Preconditions

- Read only `docs/golden-path.md` and the documents it explicitly links.
- Do not open private planning.
- Do not use old chat history to fill gaps.
- Do not patch code during the test.

### Steps

First confirm the global CLI and Claude Code command point at the current
checkout, not an older install:

```bash
which multi-agent-brief
multi-agent-brief version
multi-agent-brief claude install --repo-workdir .
```

If `multi-agent-brief version` is not the current repository version, repair
the install path before starting the self-test.

Create a test directory:

```bash
BASE="$HOME/mabw-runs/golden-path-self-test-$(date +%Y%m%d-%H%M)"
mkdir -p "$BASE"
```

Create notes:

```bash
cat > "$BASE/golden_path_self_test_notes.md" <<'EOF'
# Golden Path Self-Test Notes

Date:
Repo commit:
Runtime:
Model:
Workspace:

## Step Log

## Confusions

## Commands That Were Not Obvious

## Places I Needed Non-Doc Memory

## Gate / Deliver Result

## Verdict

PASS / FAIL / PARTIAL
EOF
```

Then follow the golden path:

```text
/mabw new
/mabw run <workspace>
/generate-brief <workspace>
/mabw status <workspace>
/mabw feedback <workspace> "..."
/mabw deliver <workspace>
```

Every moment of "what button or command comes next?" goes into `Confusions`.

### Pass Criteria

- A run can be completed without private planning.
- The operator can explain the difference between `/mabw run` and
  `/generate-brief`.
- `status` does not write state.
- `deliver` goes through gates, reader-final gate, and `finalize-complete`.
- The final reader output has no internal claim IDs, workflow residue, local
  paths, or empty source rows.

### Failure Handling

Do not repair the process during the test. Finish the notes first. Afterwards,
decide whether the result requires a docs patch, CLI help patch, or runtime bug.

## 2. Fresh-Clone Pilot

Goal: verify that a pilot user with no prior context can start from the README
and run the demo.

### Boundary For The Pilot User

The pilot only needs to:

- follow README / quickstart installation and run the demo;
- record where they get stuck.

They do not need to understand Improvement Ledger, control surfaces, policy
packs, or internal architecture.

### Message To Send

```text
I would like you to test the onboarding path for an open-source briefing tool.

Please start from a new folder and read only the README / quickstart, not our
previous chat history. The goal is not to judge report quality. The goal is to
record where you get stuck and which step is unclear.

If it fails, please do not fix it. Send me the error, a screenshot if useful,
and what you expected to happen.
```

### Pilot Steps

```bash
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
bash scripts/setup.sh
source .venv/bin/activate
which multi-agent-brief
multi-agent-brief version
python3 -m pytest -q tests/test_runtime_assets.py tests/test_subagent_first_contract.py tests/test_status_commands.py
multi-agent-brief init /tmp/mabw-demo --demo --force
multi-agent-brief claude install --repo-workdir .
```

Then, in Claude Code:

```text
/mabw run /tmp/mabw-demo
/mabw status /tmp/mabw-demo
/generate-brief /tmp/mabw-demo
/mabw deliver /tmp/mabw-demo
```

If Claude Code is not available, test the CLI demo handoff:

```bash
multi-agent-brief doctor --config /tmp/mabw-demo/config.yaml
multi-agent-brief run --workspace /tmp/mabw-demo --skip-doctor
```

### What To Ask For

```text
1. Where was the first point of friction?
2. Was it a missing command, install problem, unclear document, or unexpected output?
3. Which README sentence helped most?
4. Which README sentence misled you?
5. If you only had 15 minutes, would you continue?
```

### Pass Criteria

- Fresh clone can complete setup.
- Demo workspace can initialize.
- Runtime handoff can be generated.
- If Claude Code is available, the `/mabw` five-verb entrypoint is visible.
- Friction can be classified as docs, environment, runtime, model, or product
  understanding.

## 3. Pre-Publication Leak Scan

Before a public release, reference pack, or external pilot material goes out,
scan tracked files and candidate bundles for private terms and paths.

Scan repository tracked files:

```bash
MABW_PUBLIC_SAFETY_BANNED_TERMS="<local private terms>" \
  python3 scripts/check_public_safety.py
```

Scan a candidate public workspace, reference pack, or demo bundle:

```bash
MABW_PUBLIC_SAFETY_BANNED_TERMS="<local private terms>" \
  python3 scripts/check_public_safety.py --path <candidate-reference-workspace-or-pack>
```

Do not write `<local private terms>` into the repository. Include local user
names, real company names, internal project names, local path fragments, private
chat tokens, or cloud document tokens as appropriate.

Any hit for real company names, user names, local absolute paths, chat tokens,
or private scan terms must be explained, removed, or confirmed as local-only
material before publication.

## 4. Release Record

Summarize the two validation runs in an internal release note:

```text
Golden-path self-test: PASS / FAIL / PARTIAL
Fresh-clone pilot: PASS / FAIL / PARTIAL
Public-safety scan: PASS / FAIL / PARTIAL
Top friction:
Release doc changes made:
Known limitations left for next release:
```

Only after both checks are recorded should the project link be promoted more
widely or external pilots be invited.
