# Fifteen-Minute Pilot

Use this page when you want to see BriefLoop before reading the architecture
docs.

BriefLoop helps you produce briefing packages that can be questioned, reviewed,
repaired, and handed off. It provides traceability and process accountability,
not semantic proof.

## What BriefLoop Is

BriefLoop is a source-first workflow for recurring business briefings. It keeps
the working materials around a brief inspectable:

- sources and source labels;
- registered claims;
- quality checks and warnings;
- final reader-facing delivery.

The fastest pilot path is the deterministic local demo. It uses public-safe
example artifacts and does not require an API key.

## What BriefLoop Is Not

BriefLoop is not:

- a semantic proof engine;
- an automatic truth checker;
- a replacement for human review;
- a report publisher or delivery approval system;
- evidence that output quality improved.

In short: BriefLoop is not a semantic proof engine.

The demo shows the artifact chain. It does not prove that a real report is
ready to send.

## Run The Local Demo

From a fresh checkout:

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/demo.sh
```

The demo is deterministic. It does not call an LLM, fetch sources, or require
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or other model credentials.

On Windows, use the PowerShell setup flow from
[`getting-started.md`](getting-started.md), then run:

```powershell
python scripts/demo.py
```

## Inspect These Three Files

The demo prints the workspace path. Open these files first:

| File | Why it matters |
|---|---|
| `output/intermediate/quality_panel.html` | static audit/operator view of the run status, warnings, and next actions |
| `output/intermediate/quality_summary.md` | compact human-readable quality summary |
| `output/intermediate/claim_ledger.json` | machine-readable record of claims and source metadata |

Treat these as review surfaces, not authority to publish. Delivery remains
human-triggered and gated.

For the longer first-user path, continue with
[`getting-started.md`](getting-started.md).
