---
name: scout
description: Use when source evidence must be converted into candidate_claims.json; in default topology, also screen into screened_candidates.json.
---

# Scout Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Discover source-grounded candidate reportable items from approved evidence, then screen them down to the reportable set in default topology without removing the artifact boundary between discovery and screening.

## Topology

- `role_topology: default`: do both discovery and screening. Write `candidate_claims.json` first, then write `screened_candidates.json`.
- `role_topology: strict`: do discovery only. Write `candidate_claims.json`, then hand off to the independent Screener.
- The control boundary is the artifact boundary, not the agent boundary. The merge removes a handoff, not the discard audit.

## Use When

Use after source discovery, doctor, and input governance have identified evidence material for a workspace.

## Inputs

- `config.yaml`
- `sources.yaml`
- `user.md`
- `input/sources/` (evidence files only, including `.mineru.md` files produced by `multi-agent-brief inputs extract`; also `input/` root files for backward compatibility)
- `input/hermes_cache/` when present
- approved source packages or evidence input lists
- `output/intermediate/input_classification.json` (classify result, if available)

## Outputs

- `output/intermediate/candidate_claims.json` (always; found universe, written first)
- `output/intermediate/screened_candidates.json` (default topology; selected + excluded + screening policy)

## Work

### Optional Chunk Parallelism

- Runtime may split Scout discovery across source chunks or child agents when supported.
- Chunk outputs are scratch/intermediate runtime material, not workflow artifacts.
- Do not append to `candidate_claims.json` from chunk workers.
- Join all chunk outputs deterministically before writing workflow artifacts.
- Stable join ordering must be based on source identity, source path or URL, source date, topic, and evidence text, not completion order.
- Duplicates and near-duplicates must be represented or excluded with reasons; do not silently drop chunk-level outputs during the join.
- Only the final joined `candidate_claims.json` and, in default topology, `screened_candidates.json` count for `stage-complete`.

### Step 1: Discovery

- Read approved source files from `input/sources/` (and `input/` root for backward compatibility), cached source packages, and evidence input lists. If users provided PDF/DOCX/image evidence, read the extracted `.mineru.md` file, not the raw binary document.
- Do NOT read or extract claims from `input/feedback/`, `input/instructions/`, or `input/context/` -- these contain editorial direction, task requirements, and background context, not factual evidence.
- If `input_classification.json` is available, use it as the authoritative file list.
- Discover broadly. Do not pre-filter by relevance, capacity, freshness, or ranking during discovery.
- Extract candidate reportable items with statement, evidence text, source identity, source date, topic, claim type, and confidence.
- Source identity must preserve either `source_url` for HTTP(S) web sources or `source_path` for local/package sources, plus `source_id` when available.
- Preserve `source_title` or `source_name`, publisher/institution when known, `source_category`, provider `source_type`, published date, retrieved date, and evidence text.
- `source_url` is a URL field only. Never put a title, search query, generic source label, internal source ID, or local path in `source_url`; use `source_title`/`source_name` and `source_path`.
- `source_type` is provider/storage type, such as `local_file` or `web_search`. `source_category` is reader-facing evidence category, such as `regulator`, `peer_reviewed_paper`, `company_press_release`, `market_report`, `news_media`, or `other`.
- Mark vague, stale-looking, duplicate-looking, or low-confidence candidates without dropping them.
- Keep source wording and evidence traceable.
- Write the complete joined `candidate_claims.json` once before screening starts.

### Step 2: Screening

- In default topology, read the already-joined `candidate_claims.json` as the authoritative found universe.
- Rank by relevance, freshness, source quality, and user focus.
- Deduplicate exact and near-duplicate candidates.
- Apply topic capacity caps and reporting-window rules from config.
- Write `screened_candidates.json` with:
  - `selected`: reportable candidates with source identity and evidence preserved.
  - `excluded`: every dropped or deprioritized candidate with a stable `reason_code`
    and short `explanation`. Use reason codes such as `duplicate_source`,
    `stale_source`, `capacity_capped`, `weak_relevance`, `off_focus`,
    `low_confidence`, `low_tier`, or `unsafe_evidence_boundary`.
  - `screening_policy`: the applied policy snapshot, including capacity cap, freshness window, authority preference, dedupe strategy, and pack parameters when available.
- In strict topology, stop after `candidate_claims.json` and hand off to Screener.

## Freshness Policy

- Treat workspace config freshness settings as authoritative.
- Do not retain stale sources beyond `max_source_age_days` when `fail_on_stale_source` is true, unless the input artifact/config contains an explicit structured override.
- If the configured freshness window leaves too few candidates, report this as a screening blocker or needs-human-review condition. Do not silently relax the threshold.
- Screening rationale may explain staleness, but explanation is not approval.

## Boundary Rules

- `candidate_claims.json` is write-once for this stage: Step 2 reads it, never rewrites it.
- Chunk-level outputs are not workflow artifacts and do not satisfy stage completion.
- Screening judgment must not leak into discovery. Discovery captures the found universe; screening records the discard audit.
- Never mint `claim_id` values. The Claim Ledger freeze transaction owns claim IDs.
- Do not write prose analysis.

## Handoff

- default: pass `screened_candidates.json` to claim-ledger.
- strict: pass `candidate_claims.json` to the independent Screener.
