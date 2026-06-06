# Code Review Fix Guide — Local Signal Feature

> Based on review of uncommitted changes across 7 files.  
> Date: 2026-06-06

---

## Priority Summary

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | 🔴 HIGH | deterministic.py | False positives on non-local-signal briefs |
| 2 | 🟡 MEDIUM | decider.py | Duplicated query-building logic (maintenance fork) |
| 3 | 🟡 MEDIUM | deterministic.py | Privacy check scope too broad |
| 4 | 🟡 MEDIUM | local_signal_planner.py | Missing error handling on file read |
| 5 | 🟡 MEDIUM | formatter.py | Missing error handling on file write |
| 6 | 🟡 MEDIUM | local_signal_planner.py | Shared mutable list across tasks |
| 7 | 🟢 LOW | deterministic.py | Dead variable `local_signal_report` |
| 8 | 🟢 LOW | decider.py | `added_local` missing from return dict |
| 9 | 🟢 LOW | deterministic.py | `re.IGNORECASE` no-op on Chinese regex |
| 10 | 🟢 LOW | pipeline.py | Redundant `import json` inside function body |
| 11 | 🟢 LOW | main.py | Redundant `import json` inside function body (already at module level) |
| 12 | 🟢 LOW | pipeline.py | `build_local_signal_tasks` called 3+ times |
| 13 | 🟢 LOW | main.py / pipeline.py | Duplicated `collector_tasks.json` write logic |
| 14 | 🟢 LOW | decider.py | `build_search_queries` appends local signal queries that are dead work in pipeline path |

---

## Fix 1 — False audit positives on non-local-signal briefs

**File:** `src/multi_agent_brief/audit/deterministic.py`  
**Line:** 278  
**Severity:** 🔴 HIGH

### Problem

`_check_local_signal_claims` runs unconditionally inside `DeterministicAuditAgent.run_audit` for every brief. The consumer pain-point regex patterns (e.g. `consumers report`, `用户抱怨`) match ordinary business English/Chinese that appears in any industry brief. When a brief cites an RSS or web_search source (not in `_CONSUMER_SOURCE_TYPES`), the audit emits a high-severity `local_signal_unsupported_claim` finding — even when local signal collection was never configured.

**Trigger:** Any brief that contains phrases like "consumers report strong demand" or "市场反馈显示" with a non-local-signal source.

**Impact:** Pipeline exit code 2 (blocking quality gate failed). Brief cannot be delivered.

### Fix

Gate the check on whether local signal discovery is actually configured:

```python
# In DeterministicAuditAgent.run_audit, line 277:
if context:
    # Only run local signal checks when local signal discovery is configured
    local_signal_report = context.metadata.get("local_signal_report")
    discovery = context.metadata.get("source_discovery", {})
    has_local_signal = bool(
        local_signal_report
        or discovery.get("local_signal_discovery", {}).get("enabled", False)
    )
    if has_local_signal:
        local_findings = _check_local_signal_claims(markdown, ledger, context)
        report.findings.extend(local_findings)
        if local_findings:
            from multi_agent_brief.audit.interfaces import recompute_report_status
            recompute_report_status(report)
```

### Test

Create a test with a brief containing "consumers report" + a `web_search` source, no local signal config → audit should produce zero `local_signal_unsupported_claim` findings.

---

## Fix 2 — Duplicated query-building logic

**File:** `src/multi_agent_brief/sources/decider.py`  
**Line:** 89–104  
**Severity:** 🟡 MEDIUM

### Problem

`build_search_tasks_with_metadata` (line 89–104) duplicates the standard query construction logic from `build_search_queries` (line 47–78) — same industry/company/focus_area iteration with the same cap of 5. If one function is updated and the other is not, the pipeline and the CLI will produce different search tasks.

### Fix

Refactor `build_search_tasks_with_metadata` to call `build_search_queries` and wrap the results:

```python
def build_search_tasks_with_metadata(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    """Build search tasks as dicts with metadata for pipeline injection."""
    tasks: list[dict[str, Any]] = []

    # Standard queries — delegate to build_search_queries
    for q in build_search_queries(discovery):
        tasks.append({"query": q, "domains": None})

    # Local signal tasks with metadata
    local_tasks = build_local_signal_tasks(discovery)
    existing_q = {t.get("query") for t in tasks}
    for task in local_tasks:
        if task.query not in existing_q:
            tasks.append({
                "query": task.query,
                "domains": None,
                "topic": "consumer_signal",
                "market": task.market,
                "language": task.language,
                "platform_group": task.platform_group,
                "signal_type": task.signal_type,
            })
            existing_q.add(task.query)

    return tasks
```

**Note:** `build_search_queries` already appends local signal queries (line 73–77). The `existing_q` dedup ensures metadata-enriched versions replace the plain ones. If you want the metadata-enriched versions to win, swap the order: add local signal tasks first, then standard queries.

### Test

Assert that `build_search_tasks_with_metadata(discovery)` returns the same set of query strings as `build_search_queries(discovery)` plus local signal queries.

---

## Fix 3 — Privacy check scope too broad

**File:** `src/multi_agent_brief/audit/deterministic.py`  
**Line:** 380–396  
**Severity:** 🟡 MEDIUM

### Problem

`LOCAL_SIGNAL_PRIVACY_001` iterates ALL ledger claims and flags any claim with `contains_personal_data=True`. But the finding type name `local_signal_privacy_violation` and its rule pack description imply it is scoped to local signal sources. A `web_search` claim accidentally tagged with `contains_personal_data=True` would trigger this finding, sending the safety repair owner to look for local signal infrastructure that doesn't exist.

### Fix

Scope the check to local signal claims only:

```python
# LOCAL_SIGNAL_PRIVACY_001: Check for personal data in local signal claims
for claim in ledger:
    is_local_signal = (
        claim.source_type == "local_signal"
        or claim.metadata.get("source_family") == "local_signal"
    )
    if is_local_signal and claim.metadata.get("contains_personal_data", False):
        findings.append(...)
```

### Test

Create a ledger with a `web_search` claim that has `contains_personal_data=True` → should NOT trigger `local_signal_privacy_violation`.

---

## Fix 4 — Missing error handling on file read

**File:** `src/multi_agent_brief/sources/local_signal_planner.py`  
**Line:** 434  
**Severity:** 🟡 MEDIUM

### Problem

`parse_local_signal_samples` calls `samples_path.read_text(encoding="utf-8")` without try/except. A non-UTF-8 file (e.g. Windows-1252) raises `UnicodeDecodeError` and aborts the entire pipeline during source collection.

### Fix

Wrap the read in try/except:

```python
def parse_local_signal_samples(samples_path: Path) -> list[dict[str, Any]]:
    if not samples_path.exists():
        return []

    try:
        text = samples_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Try with replacement characters
        text = samples_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    warnings: list[str] = []

    for line_num, line in enumerate(text.splitlines(), start=1):
        # ... rest of parsing
```

### Test

Create a non-UTF-8 `.jsonl` file → `parse_local_signal_samples` should return empty list with no crash.

---

## Fix 5 — Missing error handling on file write

**File:** `src/multi_agent_brief/agents/formatter.py`  
**Line:** 94–99  
**Severity:** 🟡 MEDIUM

### Problem

The new `local_signal_report.json` write has no try/except. If the write fails (disk full, permission denied), the formatter raises before writing `audit_report.json`, `brief.docx`, and other late-stage artifacts.

### Fix

Wrap in try/except consistent with other artifact writes:

```python
# Local signal report — produced by pipeline, persisted here
local_signal_report = context.metadata.get("local_signal_report")
if local_signal_report:
    try:
        local_signal_path = intermediate_dir / "local_signal_report.json"
        local_signal_path.write_text(
            json.dumps(local_signal_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifacts["local_signal_report"] = str(local_signal_path)
    except Exception:
        logger.warning("Failed to write local_signal_report.json", exc_info=True)
```

### Test

Mock `write_text` to raise `OSError` → formatter should continue and still produce `brief.md` and `audit_report.json`.

---

## Fix 6 — Shared mutable list across tasks

**File:** `src/multi_agent_brief/sources/local_signal_planner.py`  
**Line:** 316  
**Severity:** 🟡 MEDIUM

### Problem

`expected_findings` is assigned directly from `GOAL_TO_EXPECTED_FINDINGS` without copying:

```python
expected = GOAL_TO_EXPECTED_FINDINGS.get(goal, [goal])
```

All `LocalSignalTask` instances with the same goal share the same list object. If any downstream code mutates `task.expected_findings`, it corrupts every other task with that goal.

### Fix

Copy the list:

```python
expected = list(GOAL_TO_EXPECTED_FINDINGS.get(goal, [goal]))
```

Or use `field(default_factory=...)` in the dataclass if the default changes per instance.

### Test

Create two tasks with the same goal, mutate one's `expected_findings`, assert the other is unchanged.

---

## Fix 7 — Dead variable `local_signal_report`

**File:** `src/multi_agent_brief/audit/deterministic.py`  
**Line:** 317  
**Severity:** 🟢 LOW

### Problem

```python
local_signal_report = context.metadata.get("local_signal_report")
```

This variable is assigned but never read in `_check_local_signal_claims`. The function checks claims in the ledger and patterns in the markdown, but never cross-references whether the local signal report itself is complete or whether data gaps should suppress certain checks.

### Fix

Either remove the dead variable, or use it to gate checks:

```python
# Option A: Remove
# (delete line 317)

# Option B: Use it to suppress checks when no samples exist
local_signal_report = context.metadata.get("local_signal_report")
if local_signal_report and local_signal_report.get("status") == "no_samples":
    # Skip consumer pain-point checks — no samples to validate against
    return findings
```

Option B is recommended: if there are no local signal samples, consumer pain-point pattern checks will always fail (no consumer-level sources exist), which is the root cause of Fix 1.

---

## Fix 8 — `added_local` missing from return dict

**File:** `src/multi_agent_brief/sources/decider.py`  
**Line:** 400  
**Severity:** 🟢 LOW

### Problem

`merge_candidates_to_sources` computes `added_local` and writes it to `candidates["metadata"]["merged_local_tasks"]`, but the return dict at line 400 does not include it. The CLI caller (`run_sources_decide_from_args`) cannot report local task merge counts.

### Fix

Add `added_local` to the return dict:

```python
return {
    "added_manual": added_manual,
    "added_rss": added_rss,
    "added_filing": added_filing,
    "added_local": added_local,  # ← add this
    "total_enabled": len(enabled),
    "total_disabled": len(recommended) - len(enabled),
}
```

And update the CLI print in `main.py`:

```python
print(f"[sources] Merged {result['added_manual']} manual + {result['added_rss']} RSS + {result['added_local']} local signal tasks into sources.yaml")
```

---

## Fix 9 — `re.IGNORECASE` no-op on Chinese regex

**File:** `src/multi_agent_brief/audit/deterministic.py`  
**Line:** 291–294  
**Severity:** 🟢 LOW

### Problem

`re.IGNORECASE` is applied to patterns that match only Chinese characters (e.g. `r"消费者(?:认为|抱怨|普遍|反馈|觉得|表示)"`). Chinese has no case distinctions, so the flag is a no-op.

### Fix

Remove `re.IGNORECASE` from Chinese-only patterns, or split the list:

```python
# Chinese patterns — no case distinction
_CONSUMER_PAIN_PATTERNS_ZH = [
    re.compile(r"消费者(?:认为|抱怨|普遍|反馈|觉得|表示)"),
    re.compile(r"用户(?:抱怨|觉得|认为|反馈|评价|普遍)"),
    re.compile(r"市场反馈(?:显示|表明|指出)"),
    re.compile(r"用户评价(?:显示|表明)"),
]

# English patterns — case insensitive
_CONSUMER_PAIN_PATTERNS_EN = [
    re.compile(r"consumers?\s+(?:report|complain|believe|feel|say|indicate)", re.IGNORECASE),
    re.compile(r"users?\s+(?:complain|report|feel|believe|commonly)", re.IGNORECASE),
    re.compile(r"market\s+feedback\s+(?:shows|indicates|suggests)", re.IGNORECASE),
    re.compile(r"customer\s+(?:complaints?|feedback|reviews?)\s+(?:show|indicate|suggest)", re.IGNORECASE),
]

_CONSUMER_PAIN_PATTERNS = _CONSUMER_PAIN_PATTERNS_ZH + _CONSUMER_PAIN_PATTERNS_EN
```

---

## Fix 10 — Redundant `import json` inside function body

**File:** `src/multi_agent_brief/core/pipeline.py` line 142, `src/multi_agent_brief/cli/main.py` line 969  
**Severity:** 🟢 LOW

### Problem

`import json` appears inside function bodies despite `json` being a stdlib module. In `main.py`, `json` is already imported at module level (line 4).

### Fix

**pipeline.py:** Add `import json` at the top of the file (after existing imports).

**main.py:** Remove the inner `import json` at line 969 — the module-level import at line 4 already covers it.

---

## Fix 11 — `build_local_signal_tasks` called 3+ times per flow

**File:** `src/multi_agent_brief/core/pipeline.py`  
**Line:** 127, 139, 206  
**Severity:** 🟢 LOW

### Problem

In `_collect_sources`, `build_local_signal_tasks(discovery)` is called:
1. Inside `build_search_tasks_with_metadata` (line 127)
2. Inside `generate_collector_tasks` (line 139)
3. Explicitly at line 206 for the local signal report

Each call re-iterates target markets and rebuilds the same task objects.

### Fix

Compute once and reuse:

```python
# After line 125 (discovery = context.metadata.get("source_discovery"))
from multi_agent_brief.sources.local_signal_planner import (
    build_local_signal_tasks,
    generate_collector_tasks,
    generate_local_signal_report,
    parse_local_signal_samples,
)

if discovery and "web_search" in source_config.enabled_providers:
    local_tasks = build_local_signal_tasks(discovery)  # ← once

    # Use local_tasks for search tasks
    discovery_tasks = build_search_tasks_with_metadata(discovery, local_tasks)
    # ...

    # Use local_tasks for collector tasks
    collector_tasks = generate_collector_tasks(discovery, local_tasks)
    # ...

# Later, for local signal report:
if discovery and local_tasks:
    samples_path = input_dir / "local_signal_samples.jsonl"
    samples = parse_local_signal_samples(samples_path)
    local_signal_report = generate_local_signal_report(discovery, local_tasks, samples)
    context.metadata["local_signal_report"] = local_signal_report
```

This requires updating `build_search_tasks_with_metadata` and `generate_collector_tasks` to accept an optional `local_tasks` parameter.

---

## Fix 12 — Duplicated `collector_tasks.json` write logic

**File:** `src/multi_agent_brief/cli/main.py` line 965–977, `src/multi_agent_brief/core/pipeline.py` line 139–149  
**Severity:** 🟢 LOW

### Problem

The same 7-line block (generate collector tasks → mkdir → write JSON → print) is copy-pasted in two locations. If the output path or JSON formatting changes in one but not the other, artifacts diverge.

### Fix

Extract a shared helper in `local_signal_planner.py`:

```python
def write_collector_tasks_json(
    discovery: dict[str, Any],
    output_path: Path,
) -> dict[str, Any] | None:
    """Generate and write collector_tasks.json. Returns tasks dict or None."""
    tasks = generate_collector_tasks(discovery)
    if not tasks.get("tasks"):
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return tasks
```

Then call from both `main.py` and `pipeline.py`:

```python
from multi_agent_brief.sources.local_signal_planner import write_collector_tasks_json

collector_path = workspace / "output" / "intermediate" / "collector_tasks.json"
tasks = write_collector_tasks_json(discovery, collector_path)
if tasks:
    print(f"[sources] Generated collector_tasks.json at {collector_path}")
    print(f"[sources] {len(tasks['tasks'])} local signal collection tasks ready")
```

---

## Fix 13 — `build_search_queries` appends dead local signal queries

**File:** `src/multi_agent_brief/sources/decider.py`  
**Line:** 72–77  
**Severity:** 🟢 LOW

### Problem

`build_search_queries` calls `build_local_signal_tasks(discovery)` and appends those query strings to its return list (lines 72–77). But the pipeline now uses `build_search_tasks_with_metadata` exclusively for search task injection — which builds its own local signal tasks with metadata. The local signal queries appended inside `build_search_queries` are dead work when called from the pipeline path.

They are also misleading: any external caller of `build_search_queries` (e.g. `generate_source_candidates`, the CLI display at `run_sources_decide_from_args`) gets local signal queries as plain strings with no metadata, while the pipeline handles them separately with richer metadata.

### Fix

Remove the local signal query appending from `build_search_queries`. Let `build_search_tasks_with_metadata` be the single source of truth for local signal queries:

```python
def build_search_queries(discovery: dict[str, Any]) -> list[str]:
    """Build standard web search queries from source_discovery fields.

    Does NOT include local signal queries — those are handled by
    build_search_tasks_with_metadata which adds platform/market metadata.
    """
    company = discovery.get("company", "")
    industry = discovery.get("industry", "")
    focus_areas = discovery.get("focus_areas", [])

    queries = []
    if industry:
        queries.append(f"{industry} industry news recent")
    if company:
        queries.append(f"{company} official announcements news")
    if isinstance(focus_areas, str):
        focus_areas = [a.strip() for a in focus_areas.split(",") if a.strip()]
    for area in focus_areas[:5]:
        if company:
            queries.append(f"{company} {area}")
        elif industry:
            queries.append(f"{industry} {area}")

    # NOTE: Local signal queries are NOT appended here.
    # Use build_search_tasks_with_metadata() for pipeline injection
    # which adds topic/market/language metadata.

    return queries
```

Update the CLI display in `run_sources_decide_from_args` to also show local signal queries:

```python
queries = build_search_queries(discovery)
local_tasks = build_local_signal_tasks(discovery)
total_queries = queries + [t.query for t in local_tasks if t.query not in queries]
print(f"[sources] Generated {len(total_queries)} search queries ({len(queries)} standard + {len(local_tasks)} local signal):")
```

### Test

Assert `build_search_queries(discovery)` returns only industry/company/focus_area queries, even when `local_signal_discovery` is enabled. Assert `build_search_tasks_with_metadata` returns the full set including local signal queries with metadata.

---

## Testing Checklist

- [ ] Non-local-signal brief with "consumers report" → zero `local_signal_unsupported_claim` findings
- [ ] Local signal brief with consumer pain-point + local_signal source → zero findings
- [ ] Local signal brief with consumer pain-point + web_search source → high-severity finding
- [ ] `build_search_tasks_with_metadata` returns same queries as `build_search_queries` + local signal
- [ ] `merge_candidates_to_sources` return dict includes `added_local`
- [ ] Non-UTF-8 `.jsonl` file → `parse_local_signal_samples` returns empty, no crash
- [ ] Formatter write failure → `brief.md` and `audit_report.json` still produced
- [ ] Two tasks with same goal → mutating one's `expected_findings` doesn't affect the other
- [ ] `web_search` claim with `contains_personal_data=True` → no `local_signal_privacy_violation`
