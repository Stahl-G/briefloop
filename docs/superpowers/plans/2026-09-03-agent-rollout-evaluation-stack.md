# 方案 B′：agent-rollout 评测栈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成一个能对 agent rollout 打分的评测栈，在 `main` 上产出第一个可复现的 ℛ 数字，填掉 `EF-1/EF-2` 这个空名字。

**Architecture:** 新包 `evaluation_v2`，四个单一职责模块：contracts（strict pydantic DTO）、corpus（加载与不变量校验）、scoring（ℛ 计算）、runner（rollout 编排，rollout 函数可注入）。真实 Codex rollout 适配器最后接入，前七个任务全部可离线 TDD。

**Tech Stack:** Python 3.12+、pydantic v2 strict、pytest。复用 `multi_agent_brief.contracts.base.SchemaRegistry` 的注册风格。

**基线：** `main` @ `3f1334e8`。规格见 [`docs/superpowers/specs/2026-09-03-briefloop-usability-and-evaluation-design.md`](../specs/2026-09-03-briefloop-usability-and-evaluation-design.md) §4。

**为什么不复活 `eval-cases`：** 该名称在 LD2-3 已 Retired 并记录在 support matrix。新建 `evaluation_v2` 避免与已退役的 CLI 契约重名。旧的 `evaluation_cases/fixtures/` 保持原地不动，仅作为 Task 6 的标注来源读取。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `src/multi_agent_brief/evaluation_v2/__init__.py` | 包锚点 |
| `src/multi_agent_brief/evaluation_v2/contracts.py` | Case / RolloutOutcome / CorpusScore 的 strict DTO |
| `src/multi_agent_brief/evaluation_v2/corpus.py` | 从磁盘加载 case、执行语料不变量校验 |
| `src/multi_agent_brief/evaluation_v2/scoring.py` | ℛ = defect_recall × true_negative_rate |
| `src/multi_agent_brief/evaluation_v2/runner.py` | 按 split 编排 rollout，rollout 函数注入 |
| `src/multi_agent_brief/evaluation_v2/staging.py` | 把 workspace 推进到目标 stage（从测试私有 helper 提取） |
| `src/multi_agent_brief/evaluation_v2/codex_rollout.py` | 真实 Codex 角色 rollout 适配器 |
| `src/multi_agent_brief/cli/eval_commands.py` | `briefloop eval run\|score\|report` |
| `evaluation/corpus/` | 80 个 case 的语料目录 |
| `tests/test_evaluation_v2_contracts.py` | DTO 契约 |
| `tests/test_evaluation_v2_scoring.py` | ℛ 计算，含退化情形 |
| `tests/test_evaluation_v2_corpus.py` | 加载 + 语料不变量 |
| `tests/test_evaluation_v2_runner.py` | 编排（用假 rollout） |
| `tests/test_evaluation_v2_staging.py` | stage seeding 提取后的等价性 |
| `tests/test_evaluation_v2_cli.py` | CLI 接线 |

---

## Task 1: Case 契约 DTO

**Files:**
- Create: `src/multi_agent_brief/evaluation_v2/__init__.py`
- Create: `src/multi_agent_brief/evaluation_v2/contracts.py`
- Test: `tests/test_evaluation_v2_contracts.py`

**设计要点：** case schema 里**没有任何命令或 shell 字段**。这让红线
`Do not execute arbitrary shell strings from evaluation fixtures`
在结构上不可违反，而不是靠约定遵守。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evaluation_v2_contracts.py`：

```python
"""Strict contracts for agent-rollout evaluation cases."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from multi_agent_brief.evaluation_v2.contracts import (
    FINDING_TYPES,
    EvaluationCase,
    RolloutOutcome,
    SeededDefect,
)


def _case_payload(**overrides):
    payload = {
        "case_id": "stale_source_in_weekly_pack",
        "synthetic": True,
        "source_pack": "cases/stale_source_in_weekly_pack/sources",
        "report_date": "2026-06-08",
        "rollout": {"role": "auditor", "runtime": "codex"},
        "seeded_defects": [
            {
                "defect_id": "d1",
                "finding_type": "stale_source",
                "locator": "source-002.md#L14",
            }
        ],
        "clean_claims": ["source-001.md#L8"],
        "must_block": True,
    }
    payload.update(overrides)
    return payload


def test_valid_case_parses():
    case = EvaluationCase.model_validate(_case_payload(), strict=True)
    assert case.case_id == "stale_source_in_weekly_pack"
    assert case.seeded_defects[0].finding_type == "stale_source"
    assert case.must_block is True


def test_case_rejects_unknown_finding_type():
    payload = _case_payload(
        seeded_defects=[
            {"defect_id": "d1", "finding_type": "not_a_real_type", "locator": "a#L1"}
        ]
    )
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(payload, strict=True)


def test_case_rejects_non_synthetic():
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(_case_payload(synthetic=False), strict=True)


def test_case_rejects_extra_fields():
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(
            _case_payload(command="rm -rf /"), strict=True
        )


def test_must_block_case_requires_at_least_one_defect():
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(
            _case_payload(seeded_defects=[]), strict=True
        )


def test_must_not_block_case_requires_no_defects():
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(
            _case_payload(must_block=False), strict=True
        )


def test_defect_ids_must_be_unique_within_a_case():
    payload = _case_payload(
        seeded_defects=[
            {"defect_id": "d1", "finding_type": "stale_source", "locator": "a#L1"},
            {"defect_id": "d1", "finding_type": "number_without_source", "locator": "a#L2"},
        ]
    )
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(payload, strict=True)


def test_finding_types_match_the_retired_corpus():
    assert FINDING_TYPES == frozenset(
        {
            "claim_support_matrix_blocking_support",
            "number_without_source",
            "stale_source",
            "target_priority_claim_missing_from_summary",
            "target_relevance_gap",
            "final_incomplete_key_case_fields",
            "final_missing_comparison_basis",
            "final_missing_limitation_section",
            "final_scope_title_mismatch",
            "final_unsupported_superlative",
        }
    )


def test_rollout_outcome_parses():
    outcome = RolloutOutcome.model_validate(
        {
            "case_id": "c1",
            "found_defect_ids": ["d1"],
            "flagged_claim_locators": ["source-001.md#L8"],
            "blocked": True,
        },
        strict=True,
    )
    assert outcome.found_defect_ids == ("d1",)
    assert outcome.blocked is True


def test_seeded_defect_rejects_blank_locator():
    with pytest.raises(ValidationError):
        SeededDefect.model_validate(
            {"defect_id": "d1", "finding_type": "stale_source", "locator": "  "},
            strict=True,
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_contracts.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'multi_agent_brief.evaluation_v2'`

- [ ] **Step 3: 实现**

创建 `src/multi_agent_brief/evaluation_v2/__init__.py`：

```python
"""Agent-rollout evaluation stack.

Scores agent role rollouts against seeded-defect cases.  This is the
Store-native rebuild the support matrix refers to as EF-1/EF-2; the retired
``eval-cases`` surface is not resurrected.
"""
```

创建 `src/multi_agent_brief/evaluation_v2/contracts.py`：

```python
"""Strict DTOs for agent-rollout evaluation.

The case schema deliberately carries no command, script, or shell field, so
the red line "Do not execute arbitrary shell strings from evaluation fixtures"
is structurally unviolable rather than merely observed.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

FINDING_TYPES = frozenset(
    {
        "claim_support_matrix_blocking_support",
        "number_without_source",
        "stale_source",
        "target_priority_claim_missing_from_summary",
        "target_relevance_gap",
        "final_incomplete_key_case_fields",
        "final_missing_comparison_basis",
        "final_missing_limitation_section",
        "final_scope_title_mismatch",
        "final_unsupported_superlative",
    }
)

EVOLVABLE_ROLES = ("scout", "screener", "claim-ledger", "auditor", "editor")

_NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SeededDefect(_Strict):
    """One deliberately planted defect with a known location."""

    defect_id: _NonBlank
    finding_type: Literal[
        "claim_support_matrix_blocking_support",
        "number_without_source",
        "stale_source",
        "target_priority_claim_missing_from_summary",
        "target_relevance_gap",
        "final_incomplete_key_case_fields",
        "final_missing_comparison_basis",
        "final_missing_limitation_section",
        "final_scope_title_mismatch",
        "final_unsupported_superlative",
    ]
    locator: _NonBlank


class RolloutSpec(_Strict):
    """Which role to run, on which runtime."""

    role: Literal["scout", "screener", "claim-ledger", "auditor", "editor"]
    runtime: Literal["codex"]


class EvaluationCase(_Strict):
    """One scored task instance."""

    case_id: _NonBlank
    synthetic: Literal[True]
    source_pack: _NonBlank
    report_date: date
    rollout: RolloutSpec
    seeded_defects: tuple[SeededDefect, ...] = Field(default=())
    clean_claims: tuple[_NonBlank, ...] = Field(default=())
    must_block: bool

    @model_validator(mode="after")
    def _check_defect_expectations(self) -> "EvaluationCase":
        ids = [defect.defect_id for defect in self.seeded_defects]
        if len(ids) != len(set(ids)):
            raise ValueError("defect_id values must be unique within a case")
        if self.must_block and not self.seeded_defects:
            raise ValueError("a must_block case requires at least one seeded defect")
        if not self.must_block and self.seeded_defects:
            raise ValueError("a must-not-block case must have no seeded defects")
        return self


class RolloutOutcome(_Strict):
    """What one rollout actually reported for one case."""

    case_id: _NonBlank
    found_defect_ids: tuple[_NonBlank, ...] = Field(default=())
    flagged_claim_locators: tuple[_NonBlank, ...] = Field(default=())
    blocked: bool


class CorpusScore(_Strict):
    """The reward and the counts it was computed from."""

    defect_recall: float
    true_negative_rate: float
    reward: float
    seeded_total: int
    seeded_detected: int
    clean_total: int
    clean_flagged: int
    block_agreement: float
    case_count: int
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_contracts.py -q`
Expected: PASS，10 passed

- [ ] **Step 5: 提交**

```bash
git add src/multi_agent_brief/evaluation_v2/ tests/test_evaluation_v2_contracts.py
git commit -m "feat(eval): add strict contracts for agent-rollout evaluation cases"
```

---

## Task 2: ℛ 计算

**Files:**
- Create: `src/multi_agent_brief/evaluation_v2/scoring.py`
- Test: `tests/test_evaluation_v2_scoring.py`

**公式（规格 §4 B′3）：**

```
defect_recall      = |detected ∩ seeded| / |seeded|
false_flag_rate    = |flagged ∩ clean|   / |clean|
true_negative_rate = 1 - false_flag_rate
ℛ                  = defect_recall × true_negative_rate
```

**乘法不是加法**：全部拦截 → 第二项归零；全部放行 → 第一项归零。
`block_agreement` 单独报告，**不进入 ℛ**。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evaluation_v2_scoring.py`：

```python
"""Reward is paired so omission cannot score."""

from __future__ import annotations

import pytest

from multi_agent_brief.evaluation_v2.contracts import EvaluationCase, RolloutOutcome
from multi_agent_brief.evaluation_v2.scoring import score_corpus


def _blocking_case(case_id: str) -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "case_id": case_id,
            "synthetic": True,
            "source_pack": f"cases/{case_id}/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": [
                {"defect_id": "d1", "finding_type": "stale_source", "locator": "s2#L1"}
            ],
            "clean_claims": ["s1#L8"],
            "must_block": True,
        },
        strict=True,
    )


def _clean_case(case_id: str) -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "case_id": case_id,
            "synthetic": True,
            "source_pack": f"cases/{case_id}/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": [],
            "clean_claims": ["s1#L2", "s1#L9"],
            "must_block": False,
        },
        strict=True,
    )


def _outcome(case_id, found=(), flagged=(), blocked=False) -> RolloutOutcome:
    return RolloutOutcome.model_validate(
        {
            "case_id": case_id,
            "found_defect_ids": list(found),
            "flagged_claim_locators": list(flagged),
            "blocked": blocked,
        },
        strict=True,
    )


def test_perfect_run_scores_one():
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome("b1", found=["d1"], blocked=True),
        _outcome("c1", blocked=False),
    ]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 1.0
    assert score.true_negative_rate == 1.0
    assert score.reward == 1.0


def test_flagging_everything_scores_zero():
    """The omission attractor's mirror: over-blocking must not pay."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome("b1", found=["d1"], flagged=["s1#L8"], blocked=True),
        _outcome("c1", flagged=["s1#L2", "s1#L9"], blocked=True),
    ]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 1.0
    assert score.true_negative_rate == 0.0
    assert score.reward == 0.0


def test_detecting_nothing_scores_zero():
    """Passing everything through must not pay either."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [_outcome("b1", blocked=False), _outcome("c1", blocked=False)]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 0.0
    assert score.true_negative_rate == 1.0
    assert score.reward == 0.0


def test_reward_is_the_product_not_the_mean():
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome("b1", found=["d1"], blocked=True),
        _outcome("c1", flagged=["s1#L2"], blocked=True),
    ]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 1.0
    assert score.true_negative_rate == pytest.approx(2 / 3)
    assert score.reward == pytest.approx(2 / 3)
    assert score.reward != pytest.approx((1.0 + 2 / 3) / 2)


def test_block_agreement_is_reported_but_excluded_from_reward():
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome("b1", found=["d1"], blocked=False),
        _outcome("c1", blocked=False),
    ]
    score = score_corpus(cases, outcomes)
    assert score.block_agreement == 0.5
    assert score.reward == 1.0


def test_empty_seeded_set_yields_recall_one():
    cases = [_clean_case("c1")]
    outcomes = [_outcome("c1", blocked=False)]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 1.0
    assert score.seeded_total == 0


def test_empty_clean_set_yields_tnr_one():
    case = EvaluationCase.model_validate(
        {
            "case_id": "b1",
            "synthetic": True,
            "source_pack": "cases/b1/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": [
                {"defect_id": "d1", "finding_type": "stale_source", "locator": "s2#L1"}
            ],
            "clean_claims": [],
            "must_block": True,
        },
        strict=True,
    )
    score = score_corpus([case], [_outcome("b1", found=["d1"], blocked=True)])
    assert score.true_negative_rate == 1.0


def test_unknown_found_defect_id_is_not_credited():
    cases = [_blocking_case("b1")]
    outcomes = [_outcome("b1", found=["nonexistent"], blocked=True)]
    score = score_corpus(cases, outcomes)
    assert score.seeded_detected == 0
    assert score.defect_recall == 0.0


def test_missing_outcome_raises():
    with pytest.raises(ValueError, match="missing rollout outcome"):
        score_corpus([_blocking_case("b1")], [])


def test_duplicate_outcome_raises():
    cases = [_blocking_case("b1")]
    outcomes = [_outcome("b1", blocked=True), _outcome("b1", blocked=True)]
    with pytest.raises(ValueError, match="duplicate rollout outcome"):
        score_corpus(cases, outcomes)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_scoring.py -q`
Expected: FAIL —— `ModuleNotFoundError: ... evaluation_v2.scoring`

- [ ] **Step 3: 实现**

创建 `src/multi_agent_brief/evaluation_v2/scoring.py`：

```python
"""Paired reward for agent-rollout evaluation.

    R = defect_recall * true_negative_rate

The product, not the mean.  A role that blocks everything drives the second
term to zero; a role that passes everything drives the first to zero.  This is
what makes the red line "Precision-only quality gate ... can reward omission"
structurally satisfied rather than merely promised.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from multi_agent_brief.evaluation_v2.contracts import (
    CorpusScore,
    EvaluationCase,
    RolloutOutcome,
)


def _index_outcomes(outcomes: Iterable[RolloutOutcome]) -> dict[str, RolloutOutcome]:
    indexed: dict[str, RolloutOutcome] = {}
    for outcome in outcomes:
        if outcome.case_id in indexed:
            raise ValueError(f"duplicate rollout outcome for case {outcome.case_id}")
        indexed[outcome.case_id] = outcome
    return indexed


def score_corpus(
    cases: Sequence[EvaluationCase],
    outcomes: Iterable[RolloutOutcome],
) -> CorpusScore:
    """Score one corpus split.  Every case must have exactly one outcome."""
    indexed = _index_outcomes(outcomes)

    seeded_total = 0
    seeded_detected = 0
    clean_total = 0
    clean_flagged = 0
    block_matches = 0

    for case in cases:
        outcome = indexed.get(case.case_id)
        if outcome is None:
            raise ValueError(f"missing rollout outcome for case {case.case_id}")

        seeded_ids = {defect.defect_id for defect in case.seeded_defects}
        seeded_total += len(seeded_ids)
        seeded_detected += len(seeded_ids & set(outcome.found_defect_ids))

        clean = set(case.clean_claims)
        clean_total += len(clean)
        clean_flagged += len(clean & set(outcome.flagged_claim_locators))

        if outcome.blocked is case.must_block:
            block_matches += 1

    defect_recall = 1.0 if seeded_total == 0 else seeded_detected / seeded_total
    true_negative_rate = 1.0 if clean_total == 0 else 1.0 - clean_flagged / clean_total
    block_agreement = 1.0 if not cases else block_matches / len(cases)

    return CorpusScore(
        defect_recall=defect_recall,
        true_negative_rate=true_negative_rate,
        reward=defect_recall * true_negative_rate,
        seeded_total=seeded_total,
        seeded_detected=seeded_detected,
        clean_total=clean_total,
        clean_flagged=clean_flagged,
        block_agreement=block_agreement,
        case_count=len(cases),
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_scoring.py -q`
Expected: PASS，10 passed

- [ ] **Step 5: 提交**

```bash
git add src/multi_agent_brief/evaluation_v2/scoring.py tests/test_evaluation_v2_scoring.py
git commit -m "feat(eval): add paired reward so omission cannot score"
```

---

## Task 3: 语料加载与不变量

**Files:**
- Create: `src/multi_agent_brief/evaluation_v2/corpus.py`
- Create: `evaluation/corpus/manifest.yaml`（先放最小骨架）
- Test: `tests/test_evaluation_v2_corpus.py`

**语料不变量（规格 §4 B′4/B′5）：**

| 不变量 | 阈值 |
|---|---|
| 总量 | ≥ 80 |
| split | `train` 与 `val` 各 40 |
| must_block 占比 | 0.55 – 0.65 |
| 每种 finding_type 出现次数 | ≥ 4 |
| finding_type 覆盖 | 10 种全覆盖 |
| `auditor` 与 `finalize` 两组 | 均须出现 |
| 全部合成 | `synthetic: true` |

Task 3 只实现校验器；语料本体在 Task 6/7 填。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evaluation_v2_corpus.py`：

```python
"""Corpus loading and the invariants that keep R meaningful."""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest
import yaml

from multi_agent_brief.evaluation_v2.corpus import (
    CorpusError,
    load_corpus,
    validate_corpus,
)


def _case(case_id: str, *, split: str, finding_type: str | None, must_block: bool):
    case = {
        "case_id": case_id,
        "synthetic": True,
        "split": split,
        "source_pack": f"cases/{case_id}/sources",
        "report_date": "2026-06-08",
        "rollout": {"role": "auditor", "runtime": "codex"},
        "clean_claims": ["s1#L2"],
        "must_block": must_block,
    }
    if must_block:
        case["seeded_defects"] = [
            {"defect_id": "d1", "finding_type": finding_type, "locator": "s2#L1"}
        ]
    else:
        case["seeded_defects"] = []
    return case


def _write_manifest(tmp_path: Path, cases: list[dict]) -> Path:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"schema_version": "briefloop-evaluation-corpus/v1", "cases": cases},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return manifest


def test_load_corpus_parses_cases(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [_case("b1", split="train", finding_type="stale_source", must_block=True)],
    )
    loaded = load_corpus(manifest)
    assert [case.case_id for case in loaded.cases] == ["b1"]
    assert loaded.split_of("b1") == "train"


def test_load_corpus_rejects_duplicate_case_ids(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [
            _case("b1", split="train", finding_type="stale_source", must_block=True),
            _case("b1", split="val", finding_type="stale_source", must_block=True),
        ],
    )
    with pytest.raises(CorpusError, match="duplicate case_id"):
        load_corpus(manifest)


def test_load_corpus_rejects_unknown_split(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [_case("b1", split="holdout", finding_type="stale_source", must_block=True)],
    )
    with pytest.raises(CorpusError, match="split"):
        load_corpus(manifest)


def test_validate_rejects_small_corpus(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [_case("b1", split="train", finding_type="stale_source", must_block=True)],
    )
    with pytest.raises(CorpusError, match="at least 80 cases"):
        validate_corpus(load_corpus(manifest))


def test_validate_rejects_thin_finding_type_coverage(tmp_path):
    from multi_agent_brief.evaluation_v2.contracts import FINDING_TYPES

    cases = []
    ordered = sorted(FINDING_TYPES)
    for index in range(48):
        cases.append(
            _case(
                f"b{index}",
                split="train" if index < 24 else "val",
                finding_type=ordered[0] if index else ordered[1],
                must_block=True,
            )
        )
    for index in range(32):
        cases.append(
            _case(
                f"c{index}",
                split="train" if index < 16 else "val",
                finding_type=None,
                must_block=False,
            )
        )
    manifest = _write_manifest(tmp_path, cases)
    with pytest.raises(CorpusError, match="fewer than 4 cases"):
        validate_corpus(load_corpus(manifest))


def test_validate_rejects_skewed_block_ratio(tmp_path):
    from multi_agent_brief.evaluation_v2.contracts import FINDING_TYPES

    ordered = sorted(FINDING_TYPES)
    cases = [
        _case(
            f"b{index}",
            split="train" if index < 40 else "val",
            finding_type=ordered[index % len(ordered)],
            must_block=True,
        )
        for index in range(80)
    ]
    manifest = _write_manifest(tmp_path, cases)
    with pytest.raises(CorpusError, match="must_block ratio"):
        validate_corpus(load_corpus(manifest))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_corpus.py -q`
Expected: FAIL —— `ModuleNotFoundError: ... evaluation_v2.corpus`

- [ ] **Step 3: 实现**

创建 `src/multi_agent_brief/evaluation_v2/corpus.py`：

```python
"""Corpus loading and the invariants that keep the reward meaningful.

A 40-case validation split moves 2.5 points per flipped case.  Thinner splits
or thinner per-finding-type coverage make R noise rather than signal, so these
invariants are enforced, not documented.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from multi_agent_brief.evaluation_v2.contracts import FINDING_TYPES, EvaluationCase

MIN_CASES = 80
MIN_SPLIT_CASES = 40
MIN_CASES_PER_FINDING_TYPE = 4
MIN_BLOCK_RATIO = 0.55
MAX_BLOCK_RATIO = 0.65

Split = Literal["train", "val"]


class CorpusError(Exception):
    """Raised when a corpus cannot be loaded or violates an invariant."""


@dataclass(frozen=True)
class Corpus:
    """A loaded corpus plus its split assignment."""

    cases: tuple[EvaluationCase, ...]
    splits: dict[str, str]

    def split_of(self, case_id: str) -> str:
        return self.splits[case_id]

    def select(self, split: str) -> tuple[EvaluationCase, ...]:
        return tuple(case for case in self.cases if self.splits[case.case_id] == split)


def load_corpus(manifest_path: Path) -> Corpus:
    """Load and strictly parse a corpus manifest."""
    raw = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    entries = (raw or {}).get("cases") or []

    cases: list[EvaluationCase] = []
    splits: dict[str, str] = {}
    for entry in entries:
        payload = dict(entry)
        split = payload.pop("split", None)
        if split not in ("train", "val"):
            raise CorpusError(
                f"case {payload.get('case_id')!r} has invalid split {split!r}"
            )
        try:
            case = EvaluationCase.model_validate(payload, strict=True)
        except ValidationError as exc:
            raise CorpusError(
                f"case {payload.get('case_id')!r} failed validation: {exc}"
            ) from exc
        if case.case_id in splits:
            raise CorpusError(f"duplicate case_id {case.case_id!r}")
        splits[case.case_id] = split
        cases.append(case)

    return Corpus(cases=tuple(cases), splits=splits)


def validate_corpus(corpus: Corpus) -> None:
    """Enforce the invariants that keep R a signal rather than noise."""
    total = len(corpus.cases)
    if total < MIN_CASES:
        raise CorpusError(f"corpus needs at least 80 cases, found {total}")

    for split in ("train", "val"):
        count = len(corpus.select(split))
        if count < MIN_SPLIT_CASES:
            raise CorpusError(
                f"split {split!r} needs at least {MIN_SPLIT_CASES} cases, found {count}"
            )

    blocking = sum(1 for case in corpus.cases if case.must_block)
    ratio = blocking / total
    if not MIN_BLOCK_RATIO <= ratio <= MAX_BLOCK_RATIO:
        raise CorpusError(
            f"must_block ratio {ratio:.2f} outside "
            f"[{MIN_BLOCK_RATIO}, {MAX_BLOCK_RATIO}]"
        )

    counts = Counter(
        defect.finding_type for case in corpus.cases for defect in case.seeded_defects
    )
    thin = sorted(
        finding_type
        for finding_type in FINDING_TYPES
        if counts.get(finding_type, 0) < MIN_CASES_PER_FINDING_TYPE
    )
    if thin:
        raise CorpusError(
            f"finding types with fewer than 4 cases: {', '.join(thin)}"
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_corpus.py -q`
Expected: PASS，6 passed

- [ ] **Step 5: 提交**

```bash
git add src/multi_agent_brief/evaluation_v2/corpus.py tests/test_evaluation_v2_corpus.py
git commit -m "feat(eval): add corpus loading with enforced split invariants"
```

---

## Task 4: Runner（rollout 可注入）

**Files:**
- Create: `src/multi_agent_brief/evaluation_v2/runner.py`
- Test: `tests/test_evaluation_v2_runner.py`

**设计要点：** rollout 函数通过参数注入，使编排逻辑可以完全离线测试。
真实 Codex 适配器在 Task 8 接入。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evaluation_v2_runner.py`：

```python
"""Runner orchestration, tested with a fake rollout."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from multi_agent_brief.evaluation_v2.contracts import RolloutOutcome
from multi_agent_brief.evaluation_v2.corpus import load_corpus
from multi_agent_brief.evaluation_v2.runner import run_split


def _manifest(tmp_path: Path) -> Path:
    cases = [
        {
            "case_id": "b1",
            "synthetic": True,
            "split": "val",
            "source_pack": "cases/b1/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": [
                {"defect_id": "d1", "finding_type": "stale_source", "locator": "s2#L1"}
            ],
            "clean_claims": ["s1#L2"],
            "must_block": True,
        },
        {
            "case_id": "t1",
            "synthetic": True,
            "split": "train",
            "source_pack": "cases/t1/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": [],
            "clean_claims": ["s1#L4"],
            "must_block": False,
        },
    ]
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {"schema_version": "briefloop-evaluation-corpus/v1", "cases": cases},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_run_split_only_runs_the_requested_split(tmp_path):
    seen = []

    def fake_rollout(case):
        seen.append(case.case_id)
        return RolloutOutcome.model_validate(
            {
                "case_id": case.case_id,
                "found_defect_ids": ["d1"],
                "flagged_claim_locators": [],
                "blocked": True,
            },
            strict=True,
        )

    result = run_split(load_corpus(_manifest(tmp_path)), "val", fake_rollout)
    assert seen == ["b1"]
    assert result.score.reward == 1.0
    assert result.split == "val"


def test_run_split_rejects_outcome_for_the_wrong_case(tmp_path):
    def wrong_rollout(case):
        return RolloutOutcome.model_validate(
            {
                "case_id": "somewhere-else",
                "found_defect_ids": [],
                "flagged_claim_locators": [],
                "blocked": False,
            },
            strict=True,
        )

    with pytest.raises(ValueError, match="returned outcome for"):
        run_split(load_corpus(_manifest(tmp_path)), "val", wrong_rollout)


def test_run_split_rejects_empty_split(tmp_path):
    def unused_rollout(case):  # pragma: no cover - must not be called
        raise AssertionError("should not run")

    with pytest.raises(ValueError, match="no cases"):
        run_split(load_corpus(_manifest(tmp_path)), "nonexistent", unused_rollout)


def test_result_carries_per_case_outcomes(tmp_path):
    def fake_rollout(case):
        return RolloutOutcome.model_validate(
            {
                "case_id": case.case_id,
                "found_defect_ids": [],
                "flagged_claim_locators": [],
                "blocked": False,
            },
            strict=True,
        )

    result = run_split(load_corpus(_manifest(tmp_path)), "val", fake_rollout)
    assert [outcome.case_id for outcome in result.outcomes] == ["b1"]
    assert result.score.defect_recall == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_runner.py -q`
Expected: FAIL —— `ModuleNotFoundError: ... evaluation_v2.runner`

- [ ] **Step 3: 实现**

创建 `src/multi_agent_brief/evaluation_v2/runner.py`：

```python
"""Split orchestration for agent-rollout evaluation.

The rollout callable is injected so orchestration stays testable without model
credentials.  The real Codex adapter lives in ``codex_rollout``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from multi_agent_brief.evaluation_v2.contracts import (
    CorpusScore,
    EvaluationCase,
    RolloutOutcome,
)
from multi_agent_brief.evaluation_v2.corpus import Corpus
from multi_agent_brief.evaluation_v2.scoring import score_corpus

RolloutFn = Callable[[EvaluationCase], RolloutOutcome]


@dataclass(frozen=True)
class SplitResult:
    """One scored split, with the per-case outcomes it was computed from."""

    split: str
    outcomes: tuple[RolloutOutcome, ...]
    score: CorpusScore


def run_split(corpus: Corpus, split: str, rollout: RolloutFn) -> SplitResult:
    """Run every case in ``split`` and score the results."""
    cases = corpus.select(split)
    if not cases:
        raise ValueError(f"split {split!r} has no cases")

    outcomes: list[RolloutOutcome] = []
    for case in cases:
        outcome = rollout(case)
        if outcome.case_id != case.case_id:
            raise ValueError(
                f"rollout for {case.case_id!r} returned outcome for "
                f"{outcome.case_id!r}"
            )
        outcomes.append(outcome)

    return SplitResult(
        split=split,
        outcomes=tuple(outcomes),
        score=score_corpus(cases, outcomes),
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_runner.py -q`
Expected: PASS，4 passed

- [ ] **Step 5: 提交**

```bash
git add src/multi_agent_brief/evaluation_v2/runner.py tests/test_evaluation_v2_runner.py
git commit -m "feat(eval): add split runner with injectable rollout"
```

---

## Task 5: CLI 接线

**Files:**
- Create: `src/multi_agent_brief/cli/eval_commands.py`
- Modify: `src/multi_agent_brief/cli/main.py`（`build_parser` 与 `_dispatch`）
- Modify: `src/multi_agent_brief/cli/experimental.py`（把 `eval` 加入隐藏集）
- Test: `tests/test_evaluation_v2_cli.py`

**依赖：** 本任务修改方案 A 的 `experimental.py`。若 A 尚未完成，
先完成 A 的 Task 1–2。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evaluation_v2_cli.py`：

```python
"""The eval command is registered, hidden by default, and validates corpora."""

from __future__ import annotations

import argparse

import yaml

from multi_agent_brief.cli.experimental import EXPERIMENTAL_COMMANDS
from multi_agent_brief.cli.main import build_parser, main


def test_eval_is_registered():
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if getattr(action, "dest", None) == "command" and hasattr(action, "choices")
    )
    assert "eval" in subparsers.choices


def test_eval_is_experimental():
    assert "eval" in EXPERIMENTAL_COMMANDS


def test_eval_hidden_from_default_help(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    assert "eval" not in build_parser().format_help()


def test_eval_validate_reports_corpus_violation(tmp_path, capsys):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "briefloop-evaluation-corpus/v1",
                "cases": [
                    {
                        "case_id": "b1",
                        "synthetic": True,
                        "split": "train",
                        "source_pack": "cases/b1/sources",
                        "report_date": "2026-06-08",
                        "rollout": {"role": "auditor", "runtime": "codex"},
                        "seeded_defects": [
                            {
                                "defect_id": "d1",
                                "finding_type": "stale_source",
                                "locator": "s2#L1",
                            }
                        ],
                        "clean_claims": ["s1#L2"],
                        "must_block": True,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    exit_code = main(["eval", "validate", "--corpus", str(manifest)])
    assert exit_code == 1
    assert "at least 80 cases" in capsys.readouterr().err
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_cli.py -q`
Expected: FAIL —— `assert "eval" in subparsers.choices`

- [ ] **Step 3: 实现 CLI 模块**

创建 `src/multi_agent_brief/cli/eval_commands.py`：

```python
"""eval command: validate a corpus, run a split, report a reward."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from multi_agent_brief.evaluation_v2.corpus import (
    CorpusError,
    load_corpus,
    validate_corpus,
)

DEFAULT_CORPUS = Path("evaluation/corpus/manifest.yaml")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the eval subparser."""
    parser = subparsers.add_parser(
        "eval",
        help="Experimental: score agent rollouts against seeded-defect cases.",
    )
    actions = parser.add_subparsers(dest="eval_action", required=True)

    validate_parser = actions.add_parser(
        "validate", help="Check corpus invariants without running rollouts."
    )
    validate_parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help="Path to the corpus manifest.",
    )

    run_parser = actions.add_parser("run", help="Run one split and report R.")
    run_parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    run_parser.add_argument("--split", choices=("train", "val"), default="val")
    run_parser.add_argument("--json", action="store_true", dest="json_output")


def handle(args: argparse.Namespace) -> int:
    """Dispatch eval sub-actions."""
    if args.eval_action == "validate":
        return _handle_validate(args)
    if args.eval_action == "run":
        return _handle_run(args)
    return 1


def _handle_validate(args: argparse.Namespace) -> int:
    try:
        corpus = load_corpus(Path(args.corpus))
        validate_corpus(corpus)
    except CorpusError as exc:
        print(f"corpus invalid: {exc}", file=sys.stderr)
        return 1
    print(f"corpus valid: {len(corpus.cases)} cases")
    return 0


def _handle_run(args: argparse.Namespace) -> int:
    from multi_agent_brief.evaluation_v2.codex_rollout import build_codex_rollout
    from multi_agent_brief.evaluation_v2.runner import run_split

    try:
        corpus = load_corpus(Path(args.corpus))
        validate_corpus(corpus)
    except CorpusError as exc:
        print(f"corpus invalid: {exc}", file=sys.stderr)
        return 1

    result = run_split(corpus, args.split, build_codex_rollout())
    payload = result.score.model_dump()
    payload["split"] = result.split

    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"split           {result.split}")
        print(f"cases           {result.score.case_count}")
        print(f"defect_recall   {result.score.defect_recall:.4f}")
        print(f"true_neg_rate   {result.score.true_negative_rate:.4f}")
        print(f"R               {result.score.reward:.4f}")
        print(f"block_agreement {result.score.block_agreement:.4f}")
    return 0
```

- [ ] **Step 4: 接入 main.py**

在 import 区加 `from multi_agent_brief.cli import eval_commands`。

在 `build_parser` 的 `# Experimental measurement harnesses` 分组下、
`experiments_commands.register(subparsers)` 之后加：

```python
    eval_commands.register(subparsers)
```

在 `_dispatch` 的 `if cmd == "experiments":` 之后加：

```python
    if cmd == "eval":
        return eval_commands.handle(args)
```

在 `src/multi_agent_brief/cli/experimental.py` 的 `EXPERIMENTAL_COMMANDS`
中加入 `"eval"`，并同步更新
`tests/test_cli_experimental_surface.py::test_experimental_command_list_is_frozen`
的期望集合。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_cli.py tests/test_cli_experimental_surface.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/multi_agent_brief/cli/eval_commands.py src/multi_agent_brief/cli/main.py \
        src/multi_agent_brief/cli/experimental.py \
        tests/test_evaluation_v2_cli.py tests/test_cli_experimental_surface.py
git commit -m "feat(cli): add experimental eval command"
```

---

## Task 6: 迁移 24 个已标注 case

**Files:**
- Create: `evaluation/corpus/manifest.yaml`
- Create: `evaluation/corpus/cases/<case_id>/sources/*.md`
- Create: `scripts/port_legacy_eval_cases.py`
- Test: `tests/test_evaluation_v2_corpus.py`（追加）

**来源：** `src/multi_agent_brief/evaluation_cases/fixtures/manifest.yaml`。
**只读**，不修改、不删除。这批 case 的 `description`、`findings_any`、
`workflow_state.blocked` 是已完成的人工标注，是本任务唯一的复用资产。

**注意语义差异：** 旧 case 的 action 是确定性 Python 调用
（`gates.check` 等），新 case 是 agent rollout。因此**只迁移标注，不迁移
执行方式**——每个旧 case 变成新 case 的骨架，`source_pack` 需要新建。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_evaluation_v2_corpus.py`：

```python
REPO_CORPUS = Path(__file__).resolve().parents[1] / "evaluation" / "corpus" / "manifest.yaml"


def test_repo_corpus_loads():
    corpus = load_corpus(REPO_CORPUS)
    assert len(corpus.cases) >= 24


def test_repo_corpus_source_packs_exist():
    corpus = load_corpus(REPO_CORPUS)
    root = REPO_CORPUS.parent
    for case in corpus.cases:
        pack = root / case.source_pack
        assert pack.is_dir(), f"missing source pack for {case.case_id}: {pack}"
        assert any(pack.glob("*.md")), f"empty source pack for {case.case_id}"


def test_repo_corpus_is_entirely_synthetic():
    for case in load_corpus(REPO_CORPUS).cases:
        assert case.synthetic is True


def test_repo_corpus_carries_no_command_fields():
    """Structurally impossible via the schema; asserted here as a guard."""
    raw = yaml.safe_load(REPO_CORPUS.read_text(encoding="utf-8"))
    for entry in raw["cases"]:
        for banned in ("command", "commands", "action", "actions", "shell"):
            assert banned not in entry, f"{entry['case_id']} carries {banned}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_corpus.py -q`
Expected: FAIL —— `FileNotFoundError: evaluation/corpus/manifest.yaml`

- [ ] **Step 3: 写迁移脚本**

创建 `scripts/port_legacy_eval_cases.py`：

```python
#!/usr/bin/env python3
"""Port the LD2-3 evaluation-case annotations into the agent-rollout corpus.

Reads the retired fixture manifest read-only and emits corpus skeletons.  Only
the human annotation carries over: finding types, blocking expectations, and
descriptions.  Source packs are authored separately.
"""

from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEGACY = (
    ROOT
    / "src"
    / "multi_agent_brief"
    / "evaluation_cases"
    / "fixtures"
    / "manifest.yaml"
)
TARGET = ROOT / "evaluation" / "corpus" / "manifest.yaml"

AUDITOR_TYPES = {
    "claim_support_matrix_blocking_support",
    "number_without_source",
    "stale_source",
    "target_priority_claim_missing_from_summary",
}


def _role_for(finding_types: set[str]) -> str:
    return "auditor" if finding_types & AUDITOR_TYPES else "editor"


def main() -> int:
    legacy = yaml.safe_load(LEGACY.read_text(encoding="utf-8"))
    ported = []
    for index, entry in enumerate(legacy.get("cases", [])):
        case_id = entry["case_id"]
        expected = entry.get("expected", {}) or {}
        findings = expected.get("findings_any") or []
        finding_types = {
            finding["finding_type"] for finding in findings if "finding_type" in finding
        }
        must_block = bool(
            expected.get("workflow_state", {}).get("blocked") or finding_types
        )
        ported.append(
            {
                "case_id": case_id,
                "synthetic": True,
                "split": "train" if index % 2 == 0 else "val",
                "source_pack": f"cases/{case_id}/sources",
                "report_date": "2026-06-08",
                "rollout": {"role": _role_for(finding_types), "runtime": "codex"},
                "seeded_defects": [
                    {
                        "defect_id": f"d{position}",
                        "finding_type": finding_type,
                        "locator": "TO_BE_ANNOTATED",
                    }
                    for position, finding_type in enumerate(sorted(finding_types), 1)
                ]
                if must_block
                else [],
                "clean_claims": [],
                "must_block": must_block,
            }
        )

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        yaml.safe_dump(
            {"schema_version": "briefloop-evaluation-corpus/v1", "cases": ported},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    print(f"ported {len(ported)} case skeletons to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行迁移**

Run: `python scripts/port_legacy_eval_cases.py`
Expected: `ported 24 case skeletons to .../evaluation/corpus/manifest.yaml`

- [ ] **Step 5: 人工完成标注**

脚本产出的是骨架。每个 case 必须由人补齐：

1. 在 `evaluation/corpus/cases/<case_id>/sources/` 下写合成 source 文件
2. 把每个 `seeded_defects[].locator` 从 `TO_BE_ANNOTATED` 改成真实位置
   （如 `source-002.md#L14`）
3. 补 `clean_claims`：该 case 中**不该被误杀**的真实声明位置
4. 核对 `report_date` 与 source 日期一致，使 `stale_source` 类缺陷成立

**红线约束（规格 §6）：** 全部内容必须合成——不得含私有 prompt、
内部路径、真实 URL、token 或商业 benchmark case。

Run: `grep -c TO_BE_ANNOTATED evaluation/corpus/manifest.yaml`
Expected: `0`

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_corpus.py -q`
Expected: 迁移相关的 4 个测试 PASS。
`validate_corpus` 相关测试仍会因 24 < 80 而失败——由 Task 7 解决。

- [ ] **Step 7: 提交**

```bash
git add evaluation/corpus/ scripts/port_legacy_eval_cases.py tests/test_evaluation_v2_corpus.py
git commit -m "feat(eval): port 24 annotated legacy cases into the rollout corpus"
```

---

## Task 7: 扩充语料至 80

**Files:**
- Modify: `evaluation/corpus/manifest.yaml`
- Create: `evaluation/corpus/cases/*/sources/*.md`（新增 56 组）
- Test: `tests/test_evaluation_v2_corpus.py`（追加）

**目标分布：**

| 项 | 目标 |
|---|---|
| 总量 | 80（train 40 / val 40） |
| must_block | 48（占比 0.60） |
| must-not-block | 32 |
| 每种 finding_type | ≥ 4 例，10 种全覆盖 |
| `auditor` 组 4 种 / `finalize` 组 6 种 | 均须出现 |

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_evaluation_v2_corpus.py`：

```python
def test_repo_corpus_passes_all_invariants():
    validate_corpus(load_corpus(REPO_CORPUS))


def test_repo_corpus_covers_both_gate_stages():
    from multi_agent_brief.evaluation_v2.contracts import FINDING_TYPES

    auditor_types = {
        "claim_support_matrix_blocking_support",
        "number_without_source",
        "stale_source",
        "target_priority_claim_missing_from_summary",
    }
    finalize_types = FINDING_TYPES - auditor_types
    present = {
        defect.finding_type
        for case in load_corpus(REPO_CORPUS).cases
        for defect in case.seeded_defects
    }
    assert present & auditor_types
    assert present & finalize_types


def test_repo_corpus_splits_are_balanced():
    corpus = load_corpus(REPO_CORPUS)
    assert len(corpus.select("train")) == 40
    assert len(corpus.select("val")) == 40
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_corpus.py::test_repo_corpus_passes_all_invariants -q`
Expected: FAIL —— `corpus needs at least 80 cases, found 24`

- [ ] **Step 3: 生成候选 source pack**

用模型批量生成 56 组合成 source pack，每组 2–4 个 markdown 源文件，
主题限定为公开安全的行业周报素材。

**标注不可自动化。** 生成的是素材，`seeded_defects` 的 `finding_type`
与 `locator`、以及 `clean_claims` 必须逐条人工确认——这是 ground truth，
错一条就污染 ℛ。

- [ ] **Step 4: 逐 case 补齐 manifest 条目**

每个新 case 的条目形如：

```yaml
  - case_id: missing_limitation_section_q3_pack
    synthetic: true
    split: val
    source_pack: cases/missing_limitation_section_q3_pack/sources
    report_date: "2026-06-08"
    rollout:
      role: editor
      runtime: codex
    seeded_defects:
      - defect_id: d1
        finding_type: final_missing_limitation_section
        locator: "draft.md#L42"
    clean_claims:
      - "source-001.md#L11"
      - "source-002.md#L7"
    must_block: true
```

- [ ] **Step 5: 校验分布**

Run: `BRIEFLOOP_EXPERIMENTAL=1 python -m multi_agent_brief.cli.main eval validate --corpus evaluation/corpus/manifest.yaml`
Expected: `corpus valid: 80 cases`

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_corpus.py -q`
Expected: PASS，全部通过

- [ ] **Step 7: 提交**

```bash
git add evaluation/corpus/ tests/test_evaluation_v2_corpus.py
git commit -m "feat(eval): expand rollout corpus to 80 annotated cases"
```

---

## Task 8: 真实 Codex rollout 适配器

**Files:**
- Create: `src/multi_agent_brief/evaluation_v2/codex_rollout.py`
- Test: `tests/test_evaluation_v2_codex_rollout.py`

**说明：** 这是唯一需要真实模型调用的部分。适配器把一个 case 变成一次
角色 invocation，再把角色产出的 findings 映射回 `RolloutOutcome`。
映射逻辑本身用录制的 fixture 离线测试。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evaluation_v2_codex_rollout.py`：

```python
"""Findings-to-outcome mapping, tested offline with recorded payloads."""

from __future__ import annotations

import pytest

from multi_agent_brief.evaluation_v2.codex_rollout import outcome_from_findings
from multi_agent_brief.evaluation_v2.contracts import EvaluationCase


def _case() -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "case_id": "b1",
            "synthetic": True,
            "source_pack": "cases/b1/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": [
                {
                    "defect_id": "d1",
                    "finding_type": "stale_source",
                    "locator": "source-002.md#L14",
                },
                {
                    "defect_id": "d2",
                    "finding_type": "number_without_source",
                    "locator": "source-003.md#L3",
                },
            ],
            "clean_claims": ["source-001.md#L8"],
            "must_block": True,
        },
        strict=True,
    )


def test_matching_finding_credits_the_defect():
    findings = [
        {"finding_type": "stale_source", "locator": "source-002.md#L14",
         "blocking_level": "blocking"}
    ]
    outcome = outcome_from_findings(_case(), findings)
    assert outcome.found_defect_ids == ("d1",)
    assert outcome.blocked is True


def test_right_type_wrong_location_is_not_credited():
    findings = [
        {"finding_type": "stale_source", "locator": "source-009.md#L99",
         "blocking_level": "blocking"}
    ]
    outcome = outcome_from_findings(_case(), findings)
    assert outcome.found_defect_ids == ()


def test_right_location_wrong_type_is_not_credited():
    findings = [
        {"finding_type": "target_relevance_gap", "locator": "source-002.md#L14",
         "blocking_level": "blocking"}
    ]
    outcome = outcome_from_findings(_case(), findings)
    assert outcome.found_defect_ids == ()


def test_finding_on_a_clean_claim_is_recorded_as_a_false_flag():
    findings = [
        {"finding_type": "number_without_source", "locator": "source-001.md#L8",
         "blocking_level": "blocking"}
    ]
    outcome = outcome_from_findings(_case(), findings)
    assert outcome.flagged_claim_locators == ("source-001.md#L8",)
    assert outcome.found_defect_ids == ()


def test_non_blocking_findings_do_not_set_blocked():
    findings = [
        {"finding_type": "stale_source", "locator": "source-002.md#L14",
         "blocking_level": "warning"}
    ]
    outcome = outcome_from_findings(_case(), findings)
    assert outcome.found_defect_ids == ("d1",)
    assert outcome.blocked is False


def test_no_findings_yields_empty_outcome():
    outcome = outcome_from_findings(_case(), [])
    assert outcome.found_defect_ids == ()
    assert outcome.flagged_claim_locators == ()
    assert outcome.blocked is False


def test_malformed_finding_raises():
    with pytest.raises(ValueError, match="finding missing"):
        outcome_from_findings(_case(), [{"locator": "source-002.md#L14"}])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_codex_rollout.py -q`
Expected: FAIL —— `ModuleNotFoundError: ... evaluation_v2.codex_rollout`

- [ ] **Step 3: 实现映射层**

创建 `src/multi_agent_brief/evaluation_v2/codex_rollout.py`：

```python
"""Codex role rollout adapter.

``outcome_from_findings`` is pure and offline-testable.  ``build_codex_rollout``
wires it to a real role invocation and is the only part that needs credentials.

A defect counts as detected only when finding_type AND locator both match: a
role that reports the right complaint about the wrong line has not found it.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from multi_agent_brief.evaluation_v2.contracts import EvaluationCase, RolloutOutcome


def outcome_from_findings(
    case: EvaluationCase,
    findings: Iterable[Mapping[str, Any]],
) -> RolloutOutcome:
    """Map reported findings onto the case's ground truth."""
    by_key = {
        (defect.finding_type, defect.locator): defect.defect_id
        for defect in case.seeded_defects
    }
    clean = set(case.clean_claims)

    found: list[str] = []
    flagged: list[str] = []
    blocked = False

    for finding in findings:
        if "finding_type" not in finding or "locator" not in finding:
            raise ValueError(f"finding missing required keys: {sorted(finding)}")
        finding_type = finding["finding_type"]
        locator = finding["locator"]

        defect_id = by_key.get((finding_type, locator))
        if defect_id is not None and defect_id not in found:
            found.append(defect_id)
        elif locator in clean and locator not in flagged:
            flagged.append(locator)

        if finding.get("blocking_level") == "blocking":
            blocked = True

    return RolloutOutcome(
        case_id=case.case_id,
        found_defect_ids=tuple(found),
        flagged_claim_locators=tuple(flagged),
        blocked=blocked,
    )


def build_codex_rollout() -> Callable[[EvaluationCase], RolloutOutcome]:
    """Return a rollout callable backed by a real Codex role invocation.

    Wiring note for the implementer: materialise ``case.source_pack`` into a
    scratch workspace, run the role named by ``case.rollout.role`` through the
    packaged Codex kit, read the resulting findings, then hand them to
    ``outcome_from_findings``.  Reuse the existing runtime host rather than
    adding a second invocation path.
    """
    raise NotImplementedError(
        "Codex rollout wiring is Task 8 Step 5; see the docstring for the shape."
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_codex_rollout.py -q`
Expected: PASS，7 passed

**`build_codex_rollout` 在本任务保持 `NotImplementedError`。**
它依赖 Task 9 提取出的 stage seeding 能力——单个角色无法孤立调用，
运行时是 Store 驱动的阶段机（见 Task 9 的背景说明）。

- [ ] **Step 5: 提交**

```bash
git add src/multi_agent_brief/evaluation_v2/codex_rollout.py tests/test_evaluation_v2_codex_rollout.py
git commit -m "feat(eval): add findings-to-outcome mapping for rollout scoring"
```

---

## Task 9: 提取 stage seeding 为可复用模块

**Files:**
- Create: `src/multi_agent_brief/evaluation_v2/staging.py`
- Modify: `tests/test_core_run_v2.py`（改为从新模块 import，删除本地副本）
- Test: `tests/test_evaluation_v2_staging.py`

**背景（实现者必读）：** 单个角色**无法孤立调用**。`runtime_host_v2` 是
Store 驱动的阶段机：要让 Auditor 跑起来，必须先把 workspace 推进到
auditor 阶段。已退役的旧 fixture 用 `initial_stage: auditor` 表达这件事，
但做这件事的代码随 LD2-3 一起删了。

现存的等价能力在 `tests/test_core_run_v2.py`（7321 行、30 个私有 helper）里：

```
_advance_to_scout_ready
_advance_to_input_governance_ready
_advance_to_claim_ledger_ready
_advance_to_analyst_ready
_advance_before_auditor
_advance_to_auditor_ready      # 已接受 audit_findings 参数
_advance_to_finalize_ready
```

它们已被 `tests/test_core_run_v2_next_action.py` import，说明是可复用的。
本任务把它们提到产品代码，成为 evaluation 栈与测试的共同依赖。

**必须一同迁移的模块级常量**（`tests/test_core_run_v2.py:102-105`）：
`RUN_ID`、`WORKSPACE_ID`、`NOW`、`CLOCK`。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evaluation_v2_staging.py`：

```python
"""Stage seeding, extracted from the core-run test module."""

from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.evaluation_v2.staging import (
    SEEDABLE_STAGES,
    StagingError,
    seed_workspace_to_stage,
)
from tests.helpers import initialize_workspace


def test_seedable_stages_cover_every_rollout_role():
    """Every RolloutSpec.role must map onto a seedable stage."""
    from multi_agent_brief.evaluation_v2.codex_rollout import SEED_STAGE_FOR_ROLE
    from multi_agent_brief.evaluation_v2.contracts import EVOLVABLE_ROLES

    assert set(SEED_STAGE_FOR_ROLE) == set(EVOLVABLE_ROLES)
    assert set(SEED_STAGE_FOR_ROLE.values()) <= set(SEEDABLE_STAGES)


def test_seedable_stages_is_the_expected_tuple():
    assert SEEDABLE_STAGES == (
        "scout",
        "screener",
        "claim-ledger",
        "analyst",
        "auditor",
        "finalize",
    )


def test_seed_to_auditor_reaches_the_auditor_stage(tmp_path):
    workspace = initialize_workspace(tmp_path / "ws")
    service = seed_workspace_to_stage(workspace, "auditor")
    assert service.snapshot().stage_id == "auditor"


def test_seed_rejects_unknown_stage(tmp_path):
    workspace = initialize_workspace(tmp_path / "ws")
    with pytest.raises(StagingError, match="not seedable"):
        seed_workspace_to_stage(workspace, "nonexistent")


def test_seed_is_deterministic(tmp_path):
    first = seed_workspace_to_stage(
        initialize_workspace(tmp_path / "a"), "auditor"
    ).snapshot()
    second = seed_workspace_to_stage(
        initialize_workspace(tmp_path / "b"), "auditor"
    ).snapshot()
    assert first.stage_id == second.stage_id
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_evaluation_v2_staging.py -q`
Expected: FAIL —— `ModuleNotFoundError: ... evaluation_v2.staging`

- [ ] **Step 3: 提取**

创建 `src/multi_agent_brief/evaluation_v2/staging.py`。把
`tests/test_core_run_v2.py` 中下列内容**整体移动**（不是复制）到新模块：

- 常量 `RUN_ID`、`WORKSPACE_ID`、`NOW`、`CLOCK`
- helper `_start_invocation`、`_submit_proposal`
- 全部 `_advance_*` 系列函数

去掉前导下划线成为公共 API，并在文件头写明：

```python
"""Drive a workspace to a target stage.

A single role cannot be invoked in isolation: runtime_host_v2 is a
Store-driven stage machine, so reaching the Auditor means walking the run
there first.  These helpers previously lived as private functions inside
tests/test_core_run_v2.py; the evaluation stack and the tests now share one
copy.
"""
```

新增公共入口：

```python
SEEDABLE_STAGES = (
    "scout",
    "screener",
    "claim-ledger",
    "analyst",
    "auditor",
    "finalize",
)


class StagingError(Exception):
    """Raised when a workspace cannot be advanced to the requested stage."""


_ADVANCE_BY_STAGE = {
    "scout": advance_to_scout_ready,
    "screener": advance_to_input_governance_ready,
    "claim-ledger": advance_to_claim_ledger_ready,
    "analyst": advance_to_analyst_ready,
    "auditor": advance_to_auditor_ready,
    "finalize": advance_to_finalize_ready,
}


def seed_workspace_to_stage(workspace: Path, stage_id: str, **kwargs):
    """Advance ``workspace`` to ``stage_id`` and return the CoreRunService."""
    advance = _ADVANCE_BY_STAGE.get(stage_id)
    if advance is None:
        raise StagingError(f"stage {stage_id!r} is not seedable")
    return advance(workspace, **kwargs)
```

- [ ] **Step 4: 重接测试文件**

在 `tests/test_core_run_v2.py` 顶部加 import，并**删除**被移走的定义：

```python
from multi_agent_brief.evaluation_v2.staging import (
    CLOCK,
    NOW,
    RUN_ID,
    WORKSPACE_ID,
    advance_before_auditor as _advance_before_auditor,
    advance_to_analyst_ready as _advance_to_analyst_ready,
    advance_to_auditor_ready as _advance_to_auditor_ready,
    advance_to_claim_ledger_ready as _advance_to_claim_ledger_ready,
    advance_to_finalize_ready as _advance_to_finalize_ready,
    advance_to_input_governance_ready as _advance_to_input_governance_ready,
    advance_to_scout_ready as _advance_to_scout_ready,
    start_invocation as _start_invocation,
    submit_proposal as _submit_proposal,
)
```

保留 `_` 别名，使文件其余 7000 行无需改动。

- [ ] **Step 5: 验证提取无行为变化**

Run: `python -m pytest tests/test_core_run_v2.py tests/test_core_run_v2_next_action.py -q`
Expected: PASS，且通过数与提取前一致。

提取前先记录基线：
```bash
git stash list >/dev/null; python -m pytest tests/test_core_run_v2.py -q 2>&1 | tail -1
```

**若通过数下降，回滚本任务**——评测栈不值得用核心测试覆盖率换。

- [ ] **Step 6: 运行新测试确认通过**

Run: `python -m pytest tests/test_evaluation_v2_staging.py -q`
Expected: PASS，5 passed

- [ ] **Step 7: 接上真实 rollout**

回到 `src/multi_agent_brief/evaluation_v2/codex_rollout.py`，实现
`build_codex_rollout`：

```python
def build_codex_rollout() -> Callable[[EvaluationCase], RolloutOutcome]:
    """Return a rollout callable backed by a real Codex role invocation."""
    import shutil
    import tempfile
    from pathlib import Path

    from multi_agent_brief.evaluation_v2.staging import seed_workspace_to_stage

    corpus_root = Path("evaluation/corpus")

    def rollout(case: EvaluationCase) -> RolloutOutcome:
        workspace = Path(tempfile.mkdtemp(prefix=f"eval-{case.case_id}."))
        try:
            sources = workspace / "input" / "sources"
            sources.mkdir(parents=True)
            for source in (corpus_root / case.source_pack).glob("*.md"):
                shutil.copy2(source, sources / source.name)

            service = seed_workspace_to_stage(
                workspace, SEED_STAGE_FOR_ROLE[case.rollout.role]
            )
            findings = service.snapshot().gate_findings
            return outcome_from_findings(case, findings)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    return rollout
```

并在 `codex_rollout.py` 模块顶部加入角色到 seed stage 的映射：

```python
# A role's own name is not always the stage you seed to.  The six final_*
# defect types surface at the finalize gate, so exercising the Editor means
# walking the run to finalize.
SEED_STAGE_FOR_ROLE = {
    "scout": "scout",
    "screener": "screener",
    "claim-ledger": "claim-ledger",
    "auditor": "auditor",
    "editor": "finalize",
}
```

实现者注意：`service.snapshot().gate_findings` 是占位取值路径——
运行 Task 10 Step 1 前先用一个 case 验证该属性确实承载 findings，
若不是，改为从 `output/intermediate/quality_gate_report.json` 读取。

- [ ] **Step 8: 提交**

```bash
git add src/multi_agent_brief/evaluation_v2/staging.py \
        src/multi_agent_brief/evaluation_v2/codex_rollout.py \
        tests/test_core_run_v2.py tests/test_evaluation_v2_staging.py
git commit -m "feat(eval): extract stage seeding and wire the Codex rollout"
```

---

## Task 10: 产出第一个 ℛ 并更新 claims

**Files:**
- Modify: `docs/claims.md`
- Create: `docs/evaluation-results/first-reward.md`

- [ ] **Step 1: 跑 val split 三次**

```bash
for i in 1 2 3; do
  BRIEFLOOP_EXPERIMENTAL=1 python -m multi_agent_brief.cli.main eval run \
    --split val --json > "/tmp/eval-run-$i.json"
done
```
Expected: 三个 JSON 文件，各含 `reward` 字段

- [ ] **Step 2: 计算重测方差**

```bash
python -c "
import json, statistics
rewards = [json.load(open(f'/tmp/eval-run-{i}.json'))['reward'] for i in (1,2,3)]
print('rewards:', rewards)
print('stdev  :', statistics.stdev(rewards))
print('spread :', max(rewards) - min(rewards))
"
```

**判定（规格 §8）：** 若 spread > 0.025（即 2.5 个百分点，一个 val case
的粒度），**不要上门控**——回到 Task 7 扩充语料。记录该结论并停止。

- [ ] **Step 3: 记录结果**

创建 `docs/evaluation-results/first-reward.md`，记录：语料版本（git SHA）、
角色版本（`configs/agent_roles.yaml` 的 SHA）、三次 reward、方差、
以及 `defect_recall` / `true_negative_rate` 的分解。

- [ ] **Step 4: 更新 docs/claims.md**

把 "What Is Not Measured" 一节中关于缺陷检测的部分，替换为带数字的
measured claim，并明确保留仍未测量的部分（复用效用、输出质量）。

**这是规格 §4 B′8 的核心验收标准：至少 1 处 `NOT MEASURED` 被数字替代。**

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: PASS（Python 3.13+ 上的 2 个 `resolve()` 符号链接失败与本改动无关）

- [ ] **Step 6: 提交**

```bash
git add docs/claims.md docs/evaluation-results/first-reward.md
git commit -m "docs(eval): record the first measured reward and narrow the NOT MEASURED claim"
```
