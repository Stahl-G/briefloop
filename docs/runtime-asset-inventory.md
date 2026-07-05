# Runtime Asset Inventory

This page describes which MABW runtime assets are available from each
distribution surface. It is a truth table for install/runtime behavior, not a
new workflow contract.

## Availability Terms

| Term | Meaning |
|---|---|
| Packaged package data | Included inside the Python package under `multi_agent_brief`. Available from wheel/sdist installs. |
| Source-clone-only | Present in the repository checkout, but not shipped as Python package data. Requires a source clone or an explicit `--repo-workdir`. |
| Generated source asset | Derived from source manifests and checked in for runtime adapters. |
| Hand-maintained source asset | Maintained directly in the source repository. |
| Installer asset | Used to install the package or plugin, but not part of the Python package runtime data. |
| Public docs asset | User-facing documentation. |

## Asset Classes

| Asset | Classification | Package install availability | Notes |
|---|---|---|---|
| `src/multi_agent_brief/configs/*.yaml` | Packaged package data | Available | Contract data used by handoff, runtime state, gates, feedback, controls, provenance, and eval cases. |
| `src/multi_agent_brief/configs/policy_packs/*.yaml` | Packaged package data | Available | Default public-safe policy pack. |
| `src/multi_agent_brief/evaluation_cases/fixtures/**` | Packaged package data | Available | Public-safe developer/CI regression fixtures. |
| `.agents/skills/**` | Hand-maintained source asset | Source-clone-only | Runtime role skill contracts for repository/runtime use. |
| `.agents/hermes-skills/**` | Hand-maintained source asset | Source-clone-only | Hermes source plugin skill assets. |
| `.claude/agents/**` | Generated source asset | Source-clone-only | Claude Code source-repo subagent assets. |
| `.claude/commands/**` | Hand-maintained source asset | Source-clone-only | Source-repo slash commands. Use `runtime install` for workspace-local copies. |
| `.codex/config.toml` and `.codex/agents/**` | Generated source asset | Source-clone-only | Codex source-repo custom-agent assets. Use `runtime install` for workspace-local copies. |
| `.opencode/agents/**` | Generated source asset | Source-clone-only | OpenCode source-repo agent assets. |
| `.opencode/commands/**` | Hand-maintained source asset | Source-clone-only | Source-repo commands. Use `runtime install` for workspace-local copies. |
| `docs/agents/**` | Generated source/docs asset | Source-clone-only | Adapter documentation generated from role source. |
| `integrations/hermes-plugin/**` | Hermes plugin source asset | Source-clone-only | Plugin source tree; package-only installs should not assume it exists. |
| `.agents/skills/briefloop-workbuddy/**` | WorkBuddy Skill source asset | Source-clone-only | Canonical local WorkBuddy Skill source bundle; `workbuddy pack-skill` generates a local Skill zip from these source-clone files. Package-only installs should not assume the source tree exists. |
| `.codebuddy/skills/briefloop/**` | CodeBuddy project Skill adapter | Source-clone-only | Experimental project-level CodeBuddy Skill adapter used by `--runtime codebuddy`. It keeps orchestration in the main CodeBuddy session and does not add gate, delivery, release, or control-file authority. |
| `.codebuddy/agents/briefloop-*.md` | CodeBuddy project role sub-agents | Source-clone-only | Experimental native CodeBuddy role-agent assets for Scout, Analyst, Editor, Auditor, and Formatter. These are project-level sub-agents for handoff-assigned drafting, not gate, delivery, release, or control-file authority. |
| `integrations/workbuddy/briefloop/**` | WorkBuddy Skill legacy mirror | Source-clone-only | Compatibility mirror kept for older references; not the canonical pack source. |
| `scripts/check_workbuddy_skill_pack.py` | WorkBuddy package readiness check | Source-clone-only | Builds and validates the local WorkBuddy Skill zip shape without publishing to WorkBuddy Marketplace. |
| `scripts/install.sh` | Installer asset | Source-clone-only | curl/archive install helper. |
| `scripts/install.ps1` | Installer asset | Source-clone-only | PowerShell install helper. |
| `Formula/multi-agent-brief.rb` | Installer asset | Source-clone-only | Homebrew formula source. |

## Install Mode Matrix

| Install mode | CLI commands | Packaged contracts/eval cases | Source runtime assets | Workspace runtime kit |
|---|---|---|---|---|
| Source clone + editable install | Supported | Supported | Available | `multi-agent-brief runtime install --workspace <ws> --runtime opencode\|claude\|codex\|all` copies assets into the workspace. |
| Wheel / sdist install | Supported | Supported | Not included | Source-clone-only unless `--repo-workdir` points to a source clone. |
| PyPI install | Experimental | Supported when package data is present | Not included | Source-clone-only unless `--repo-workdir` points to a source clone. |
| curl / PowerShell installer | Experimental CLI-only | Supported when installed package includes package data | Not included | Source-clone-only unless a source clone is also available. |
| Homebrew formula source | Experimental CLI-only | Supported when installed package includes package data | Not included | Source-clone-only unless a source clone is also available. |
| Hermes plugin source install | Supported from source clone | Uses CLI/package contracts | Source plugin tree required | Plugin installation remains source-clone-driven unless plugin assets are packaged separately. |
| WorkBuddy Skill source bundle | Experimental from source clone | Uses CLI/package contracts | Source Skill tree required | `workbuddy pack-skill` generates a local Skill zip; this is not Marketplace publication or Python package data. |
| CodeBuddy project Skill adapter | Experimental from source clone | Uses CLI/package contracts | Source `.codebuddy/skills` tree required | Main-session adapter used by `--runtime codebuddy` to route to CodeBuddy project role agents when explicitly delegated. Not a forked Skill, gate authority, delivery approval, release authority, or semantic proof. |
| CodeBuddy project role agents | Experimental from source clone | Uses CLI/package contracts | Source `.codebuddy/agents` tree required | Role agents may draft role-owned artifacts only. The main session still runs deterministic BriefLoop CLI transactions. |

## Workspace Runtime Kit

`multi-agent-brief runtime install` creates workspace-local runtime assets for
Claude Code, OpenCode, and Codex:

```bash
multi-agent-brief runtime install --workspace <workspace> --runtime opencode
multi-agent-brief runtime install --workspace <workspace> --runtime claude
multi-agent-brief runtime install --workspace <workspace> --runtime codex
multi-agent-brief runtime install --workspace <workspace> --runtime all
```

The kit copies runtime-discoverable commands, agents, and a small
`multi-agent-brief-workflow` project skill into the workspace. For Codex, it
copies `.codex/config.toml` and `.codex/agents/*.toml` custom-agent files rather
than slash commands. It does not
reinitialize the workspace, does not overwrite user-owned files by default, and
does not write absolute source-checkout paths into generated workspace files.

If the Python package cannot discover source-clone runtime assets, the command
fails with a source-clone-only message and suggests `--repo-workdir`.

## Verification

Run:

```bash
python3 scripts/check_runtime_asset_parity.py
```

This verifies the highest-risk inventory entries:

- source-clone runtime assets exist in a source checkout.
- packaged contract files exist under `src/multi_agent_brief/configs/`.
- packaged public-safe eval fixtures exist.
- `pyproject.toml` still declares the package-data patterns required for
  contracts and eval cases.
