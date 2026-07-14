# pipx And PyPI Packaging Prep

This document tracks packaging readiness for a future `pipx` / PyPI install
path. It is an operator checklist, not a current install instruction and not a
capability claim.

Current launch docs remain source-clone-first:

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/demo.sh
```

Do not add `pipx install briefloop` to the README or first-user docs until a
real package-index artifact exists and has passed the published-artifact smoke
below.

## Current Packaging Surface

- PyPI distribution metadata currently lives in `pyproject.toml`.
- The current distribution name is `briefloop`.
- The Python import package remains `multi_agent_brief`.
- The installed console scripts are `multi-agent-brief` and `briefloop`.
- Source-clone setup remains the supported launch path until release notes say
  otherwise.

Do not rename the Python import package or remove the `multi-agent-brief`
console script as part of pipx prep. The public `briefloop` command should
remain an additive user-facing entrypoint.

## Before Publishing Any PyPI Artifact

1. Decide the package-index distribution name explicitly.
   The intended package-index distribution name is `briefloop`. Confirm that
   the `briefloop` distribution name is available or already reserved for this
   project before changing public install docs.
2. Verify the package metadata:

   ```bash
   python3 -m venv /tmp/briefloop-metadata-smoke
   /tmp/briefloop-metadata-smoke/bin/python -m pip install --dry-run .
   python3 scripts/check_version_consistency.py
   python3 scripts/check_release_consistency.py --no-tag
   ```

3. Build and inspect the artifacts in a clean environment:

   ```bash
   python3 -m build
   python3 -m twine check dist/*
   ```

4. Install the built wheel into a fresh virtual environment and smoke both
   console scripts:

   ```bash
   python3 -m venv /tmp/briefloop-wheel-smoke
   /tmp/briefloop-wheel-smoke/bin/python -m pip install dist/*.whl
   /tmp/briefloop-wheel-smoke/bin/briefloop version
   /tmp/briefloop-wheel-smoke/bin/multi-agent-brief version
   /tmp/briefloop-wheel-smoke/bin/multi-agent-brief init /tmp/briefloop-wheel-ws --demo --force
   /tmp/briefloop-wheel-smoke/bin/multi-agent-brief run --workspace /tmp/briefloop-wheel-ws --runtime operator --skip-doctor
   ```

5. Publish only after the tag/release and package metadata agree.
   Prefer trusted publishing or another auditable release process.

## After Publishing

Only after the package exists on the index, verify the actual user-facing pipx
path from a clean machine or disposable environment:

```bash
python3 -m pip index versions briefloop
pipx install briefloop
briefloop version
briefloop init /tmp/briefloop-pipx-ws --demo --force
briefloop run --workspace /tmp/briefloop-pipx-ws --runtime operator --skip-doctor
pipx uninstall briefloop
```

If the published distribution name is not `briefloop`, update the public docs
to use the real distribution name instead of implying that `pipx install
briefloop` works.

## Public Wording Boundary

Packaging checks only verify that the command installs and reaches deterministic
setup / handoff surfaces. A packaging smoke does not prove semantic truth, does
not improve output quality, does not approve delivery, does not run agents, and
does not authorize publication.
