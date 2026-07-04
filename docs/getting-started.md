# Getting Started

BriefLoop helps you produce briefing packages that can be questioned, reviewed,
repaired, and handed off. It provides traceability and process accountability,
not semantic proof.

This guide is the shortest path for a first-time source-clone user. It does not
require an API key for the demo.

## 1. Set Up The Checkout

macOS or Linux:

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
```

Windows PowerShell:

```powershell
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```

Check that the command is available:

```bash
multi-agent-brief version
```

## 2. Run The API-Free Demo

```bash
bash scripts/demo.sh
```

On Windows, use the Python helper directly:

```powershell
python scripts/demo.py
```

The demo creates a temporary reference workspace and prints paths like:

```text
BriefLoop demo complete.

Open:
- output/delivery/brief.md
- output/intermediate/quality_panel.html
- output/intermediate/claim_ledger.json
- output/intermediate/quality_panel.json
- output/intermediate/quality_summary.md
- output/source_appendix.md
- output/intermediate/event_log_excerpt.jsonl
```

The demo copies public-safe reference artifacts. It does not call a model, fetch
sources, run agents, or prove output quality.

## 3. Inspect The Result

Open these first:

| File | What it shows |
|---|---|
| `output/delivery/brief.md` | the reader-facing final brief |
| `output/intermediate/quality_panel.html` | the static audit/operator view of checks, warnings, and next actions |
| `output/intermediate/claim_ledger.json` | the registered claims and source metadata |
| `output/intermediate/quality_summary.md` | a human-readable summary of checks and warnings |
| `output/source_appendix.md` | source labels and source context |
| `output/intermediate/event_log_excerpt.jsonl` | a small event-trace excerpt |

The same public-safe package is available in
[`examples/reference-workspaces/industry-weekly-demo/`](../examples/reference-workspaces/industry-weekly-demo/README.md).

## 4. Create Your Own Workspace

Choose the product entry that matches the work:

```bash
briefloop new industry-weekly ./weekly-brief
briefloop new management-monthly ./monthly-review
briefloop new document-review ./document-review
```

Add local source files:

```bash
mkdir -p ./weekly-brief/input/sources
cp ./my-sources/*.md ./weekly-brief/input/sources/
```

Start the runtime handoff:

```bash
briefloop run --workspace ./weekly-brief
```

`run` prepares the handoff and control files. It does not make the final brief
by itself and does not mark stages complete.

## 5. What BriefLoop Does Not Do

BriefLoop does not prove that every claim is true.

BriefLoop does not:

- publish or send reports for you;
- prove that every claim is true;
- replace legal, compliance, investment, disclosure, or editorial judgment;
- make unsupported source material safe by formatting it nicely;
- turn feedback into long-term guidance without human approval.

When you are ready to use BriefLoop weekly, continue with
[`weekly-loop.md`](weekly-loop.md). If something blocks, use
[`troubleshooting.md`](troubleshooting.md).
