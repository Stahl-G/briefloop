"""Product-layer quality panel projection.

The Quality Panel summarizes existing control-plane artifacts for operator
review. It does not run gates, call LLMs, mutate workflow state, approve
delivery, or decide release eligibility.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from html import escape as _html_escape
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.product.guidance_manifestation import (
    validate_guidance_manifestation_projection_payload,
)
from multi_agent_brief.product.materiality_selection import validate_materiality_selection_payload
from multi_agent_brief.product.quality_closeout import (
    quality_panel_closeout_projection,
    validate_quality_panel_closeout_payload,
)
from multi_agent_brief.product.support_wording import validate_support_wording_payload
from multi_agent_brief.product.template_conformance import validate_report_template_conformance_payload
from multi_agent_brief.product.trajectory_regulation import validate_trajectory_regulation_payload

QUALITY_PANEL_SCHEMA_VERSION = "briefloop.quality_panel.v1"
QUALITY_PANEL_BOUNDARY = "product_quality_panel_projection_only_not_gate_or_release_authority"
QUALITY_PANEL_RUNTIME_EFFECT = "projection_only"
QUALITY_SUMMARY_BOUNDARY = (
    "deterministic projection of quality_panel.json only; not a quality score, "
    "not a truth proof, not a gate report replacement, and not a release authorization"
)
QUALITY_PANEL_HTML_BOUNDARY = (
    "static deterministic projection of quality_panel.json only; not a quality score, "
    "not a truth proof, not a gate report replacement, not a release authorization, "
    "and not an interactive frontend"
)
QUALITY_PANEL_HTML_BOUNDARY_ZH = (
    "仅为 quality_panel.json 的静态确定性投影；不是质量评分，不是真实性证明，"
    "不能替代门禁报告，不构成发布授权，也不是交互式前端"
)

_INTERMEDIATE = Path("output") / "intermediate"
_BLOCKING_SUPPORT_LABELS = {"unsupported", "contradicted", "insufficient_evidence"}
_QUALITY_SUMMARY_FORBIDDEN_PHRASES = (
    "ready to publish",
    "truth proven",
    "approved for publication",
    "approved for release",
    "release authorized",
)
_QUALITY_PANEL_SHA_PREFIX = "Quality-Panel-SHA256: sha256:"
_QUALITY_PANEL_HTML_FORBIDDEN_MARKERS = (
    "<script",
    "<link",
    "<iframe",
    " src=",
    "src=",
    "javascript:",
    " onload=",
    " onclick=",
)
_QUALITY_PANEL_RECOMMENDED_ACTIONS = {
    "inspect_workflow_blocker",
    "materialize_durable_source_evidence",
    "repair_source_evidence_pack_manifest",
    "resolve_quality_gate_blockers",
    "regenerate_scoped_gate_reports",
    "complete_finalize_delivery_hygiene",
    "review_claim_support_records",
    "repair_reader_final_residue",
    "inspect_run_integrity",
    "request_human_review",
    "block_run",
    "review_materiality_exclusions",
    "review_reader_template_conformance",
    "review_support_wording_warnings",
}
_QUALITY_PANEL_HTML_LABELS = {
    "audit_attachment": ("BriefLoop audit attachment", "BriefLoop 审计附件"),
    "quality_panel": ("Quality Panel", "质量面板"),
    "overall_status": ("Overall status", "总体状态"),
    "run_id": ("Run ID", "运行 ID"),
    "generated": ("Generated", "生成时间"),
    "gate_blockers": ("Gate blockers", "门禁阻断项"),
    "gate_warnings": ("Gate warnings", "门禁警告"),
    "missing_incomplete": ("Missing/incomplete", "缺失/未完成"),
    "materiality_findings": ("Materiality findings", "重要性发现"),
    "template_warnings": ("Template warnings", "模板警告"),
    "support_wording": ("Support wording", "支持措辞"),
    "recommended_actions": ("Recommended actions", "建议动作"),
    "recommended_next_actions": ("Recommended Next Actions", "建议下一步"),
    "control_integrity": ("Control Integrity", "控制完整性"),
    "run_integrity": ("Run integrity", "运行完整性"),
    "reference_eligible": ("Reference eligible", "可作为参考"),
    "fact_layer": ("Fact layer", "事实层"),
    "runtime_effect": ("Runtime effect", "运行影响"),
    "source_evidence": ("Source Evidence", "来源证据"),
    "source_pack": ("Source pack", "来源包"),
    "durable_sources": ("Durable sources", "持久来源"),
    "missing_titles": ("Missing titles", "缺失标题"),
    "missing_publishers": ("Missing publishers/institutions", "缺失发布方/机构"),
    "retrieval_source_mix": ("Retrieval source mix", "检索来源构成"),
    "underlying_evidence_mix": ("Underlying evidence mix", "底层证据构成"),
    "gate_findings": ("Gate Findings", "门禁发现"),
    "auditor_gate": ("Auditor gate", "审计门禁"),
    "finalize_gate": ("Finalize gate", "定稿门禁"),
    "legacy_latest_gate": ("Legacy/latest gate", "旧版/最新门禁"),
    "blocking_findings": ("Blocking findings", "阻断发现"),
    "warning_findings": ("Warning findings", "警告发现"),
    "claim_support_risk": ("Claim And Support Risk", "声明与支持风险"),
    "claim_count": ("Claim count", "声明数量"),
    "claim_support_matrix": ("Claim-Support Matrix", "声明支持矩阵"),
    "unsupported_rows": (
        "Unsupported / contradicted / insufficient rows",
        "不支持 / 矛盾 / 证据不足行",
    ),
    "weak_support_atoms": ("Weak-support atoms", "弱支持原子"),
    "materiality_selection": ("Materiality selection", "重要性筛选"),
    "materiality_exclusions": ("Materiality/focus exclusions", "重要性/焦点排除"),
    "reader_template_conformance": ("Reader template conformance", "读者模板一致性"),
    "reader_template_warnings": ("Reader template warnings", "读者模板警告"),
    "support_wording_warnings": ("Support wording warnings", "支持措辞警告"),
    "reader_clean_citation": ("Reader Clean And Citation Hygiene", "读者清洁与引用卫生"),
    "reader_clean_status": ("Reader-clean status", "读者清洁状态"),
    "duplicate_citation_count": ("Duplicate citation count", "重复引用数量"),
    "source_appendix_warnings": ("Source appendix warnings", "来源附录警告"),
    "quality_closeout_bundle": ("Quality Closeout And Bundle Separation", "质量收口与包分离"),
    "closeout_status": ("Closeout status", "收口状态"),
    "closeout_command": ("Closeout command", "收口命令"),
    "audit_bundle": ("Audit bundle", "审计包"),
    "delivery_bundle": ("Delivery bundle", "交付包"),
    "none": ("none", "无"),
    "no_recommended_action": (
        "No recommended action reported by quality_panel.json.",
        "quality_panel.json 未报告建议动作。",
    ),
    "color_legend": ("Color legend", "颜色图例"),
    "legend_pass": ("pass / clean", "通过 / 正常"),
    "legend_warning": ("warning", "警告"),
    "legend_block": ("blocking", "阻断"),
    "legend_missing": ("missing / incomplete", "缺失 / 未完成"),
    "legend_info": ("neutral / informational", "中性 / 信息"),
}

# Status-value semantics: machine value -> color level. Values not listed
# render as level "missing" when falsy-ish and "info" otherwise via
# _status_level. Levels: pass, warning, block, missing, info.
_QUALITY_PANEL_STATUS_LEVELS = {
    "pass": "pass",
    "clean": "pass",
    "checked": "pass",
    "generated": "pass",
    "complete": "pass",
    "ok": "pass",
    "true": "pass",
    "warning": "warning",
    "degraded": "warning",
    "stale": "warning",
    "false": "warning",
    "expected": "warning",
    "block": "block",
    "blocked": "block",
    "fail": "block",
    "failed": "block",
    "error": "block",
    "invalid": "block",
    "contaminated": "block",
    "missing": "missing",
    "incomplete": "missing",
    "unknown": "missing",
    "none": "missing",
    "projection_only": "info",
    "excluded": "info",
    "included_when_present_and_valid": "info",
    "not_applicable": "info",
    "skipped": "info",
}

# Machine status value -> zh display text. The en side always shows the raw
# machine value so the audit attachment stays greppable against control files.
_QUALITY_PANEL_HTML_VALUES_ZH = {
    "pass": "通过",
    "clean": "无污染",
    "checked": "已检查",
    "generated": "已生成",
    "complete": "已完成",
    "ok": "正常",
    "true": "是",
    "warning": "警告",
    "degraded": "已降级",
    "stale": "已过期",
    "false": "否",
    "expected": "待生成",
    "block": "阻断",
    "blocked": "已阻断",
    "fail": "失败",
    "failed": "失败",
    "error": "错误",
    "invalid": "无效",
    "contaminated": "已污染",
    "missing": "缺失",
    "incomplete": "未完成",
    "unknown": "未知",
    "none": "无",
    "projection_only": "仅投影，不改运行状态",
    "excluded": "已排除",
    "included_when_present_and_valid": "存在且有效时包含",
    "not_applicable": "不适用",
    "skipped": "已跳过",
}

# Recommended-action machine names -> zh display text (en shows the raw name).
_QUALITY_PANEL_HTML_ACTIONS_ZH = {
    "inspect_workflow_blocker": "检查工作流阻断项",
    "materialize_durable_source_evidence": "落地持久来源证据",
    "repair_source_evidence_pack_manifest": "修复来源证据包清单",
    "resolve_quality_gate_blockers": "解决质量门禁阻断项",
    "regenerate_scoped_gate_reports": "重新生成阶段门禁报告",
    "complete_finalize_delivery_hygiene": "完成定稿交付卫生检查",
    "review_claim_support_records": "复核声明支持记录",
    "repair_reader_final_residue": "修复读者终稿残留",
    "inspect_run_integrity": "检查运行完整性",
    "request_human_review": "请求人工审阅",
    "block_run": "阻断本次运行",
    "review_materiality_exclusions": "复核重要性排除项",
    "review_reader_template_conformance": "复核读者模板一致性",
    "review_support_wording_warnings": "复核支持措辞警告",
}

# Recommended-action reason codes -> zh display text (en shows the raw code).
_QUALITY_PANEL_HTML_REASONS_ZH = {
    "blocking_gate_findings": "存在门禁阻断发现",
    "finalize_or_reader_clean_missing": "定稿或读者清洁检查缺失",
    "materiality_or_focus_candidate_deprioritized": "重要性/焦点候选被降级",
    "materiality_or_focus_candidate_excluded_by_capacity_or_scope": "重要性/焦点候选因容量或范围被排除",
    "quality_gate_status_failed": "质量门禁状态为失败",
    "reader_clean_failed": "读者清洁检查失败",
    "reader_template_conformance_warning_only": "读者模板一致性仅为警告",
    "run_integrity_not_clean": "运行完整性不干净",
    "scoped_quality_gate_reports_missing": "阶段门禁报告缺失",
    "source_evidence_pack_invalid": "来源证据包无效",
    "source_evidence_pack_missing": "来源证据包缺失",
    "support_calibrated_wording_warning_only": "支持措辞校准仅为警告",
    "unsupported_claim_present_in_reader_text": "读者文本存在未支持声明",
    "unsupported_claim_support_rows": "存在未支持的声明支持行",
}


class QualityPanelError(ValueError):
    """Raised when a Quality Panel projection cannot be built or rendered."""


def quality_panel_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / _INTERMEDIATE / "quality_panel.json"


def quality_summary_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / _INTERMEDIATE / "quality_summary.md"


def quality_panel_html_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / _INTERMEDIATE / "quality_panel.html"


def build_quality_panel(workspace: str | Path) -> dict[str, Any]:
    """Build a read-only machine-readable quality projection."""

    from multi_agent_brief.status import build_workspace_status

    ws = Path(workspace).expanduser().resolve()
    workspace_status = build_workspace_status(ws)
    registry_payload = _read_json_mapping(ws / _INTERMEDIATE / "artifact_registry.json") or {}
    artifacts = registry_payload.get("artifacts") if isinstance(registry_payload, dict) else {}
    artifacts = artifacts if isinstance(artifacts, dict) else {}

    runtime = workspace_status.get("runtime") if isinstance(workspace_status.get("runtime"), dict) else {}
    workflow = workspace_status.get("workflow") if isinstance(workspace_status.get("workflow"), dict) else {}
    run_integrity = workflow.get("run_integrity") if isinstance(workflow.get("run_integrity"), dict) else {}
    source_evidence = _source_evidence_summary(ws, artifacts)
    gates = _gate_summary(ws)
    claims = _claim_summary(ws, workspace_status, artifacts)
    delivery = _delivery_summary(ws, workspace_status)
    trajectory = (
        workspace_status.get("trajectory_regulation")
        if isinstance(workspace_status.get("trajectory_regulation"), dict)
        else {}
    )
    guidance_manifestation = (
        workspace_status.get("guidance_manifestation")
        if isinstance(workspace_status.get("guidance_manifestation"), dict)
        else {}
    )
    materiality_selection = (
        workspace_status.get("materiality_selection")
        if isinstance(workspace_status.get("materiality_selection"), dict)
        else {}
    )
    report_template_conformance = (
        workspace_status.get("report_template_conformance")
        if isinstance(workspace_status.get("report_template_conformance"), dict)
        else {}
    )
    support_wording = (
        workspace_status.get("support_wording")
        if isinstance(workspace_status.get("support_wording"), dict)
        else {}
    )
    finalize_report = _read_json_mapping(ws / _INTERMEDIATE / "finalize_report.json") or {}
    closeout = quality_panel_closeout_projection(
        workspace=ws,
        finalize_report=finalize_report,
        generated_by_quality_summarize=True,
        artifact_registry=registry_payload,
    )
    control_integrity = {
        "run_integrity": run_integrity.get("status") or "unknown",
        "reference_eligible": bool(run_integrity.get("reference_eligible")),
        "fact_layer_status": _fact_layer_status(artifacts, source_evidence),
    }
    recommended_actions = _recommended_actions(
        workflow=workflow,
        control_integrity=control_integrity,
        source_evidence=source_evidence,
        gates=gates,
        claims=claims,
        delivery=delivery,
        trajectory=trajectory,
        materiality_selection=materiality_selection,
        report_template_conformance=report_template_conformance,
        support_wording=support_wording,
    )
    overall_status = _overall_status(
        workspace_status=workspace_status,
        workflow=workflow,
        control_integrity=control_integrity,
        source_evidence=source_evidence,
        gates=gates,
        claims=claims,
        delivery=delivery,
        materiality_selection=materiality_selection,
        report_template_conformance=report_template_conformance,
        support_wording=support_wording,
    )

    return {
        "schema_version": QUALITY_PANEL_SCHEMA_VERSION,
        "workspace": ".",
        "run_id": _text(runtime.get("run_id")) or "unknown",
        "generated_at": _utc_now(),
        "read_only": True,
        "runtime_effect": QUALITY_PANEL_RUNTIME_EFFECT,
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": overall_status,
        "control_integrity": control_integrity,
        "source_evidence": source_evidence,
        "gates": gates,
        "claims": claims,
        "delivery": delivery,
        "trajectory_regulation": trajectory,
        "guidance_manifestation": guidance_manifestation,
        "materiality_selection": materiality_selection,
        "report_template_conformance": report_template_conformance,
        "support_wording": support_wording,
        "quality_panel_closeout": closeout,
        "recommended_actions": recommended_actions,
        "non_goals": [
            "quality_score",
            "semantic_truth_proof",
            "release_eligibility_decision",
            "delivery_approval",
            "gate_reimplementation",
            "automatic_repair",
        ],
    }


def write_quality_panel(
    *,
    workspace: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    target = Path(output_path).expanduser() if output_path else quality_panel_path(ws)
    if not target.is_absolute():
        target = ws / target
    target = target.resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ValueError("quality_panel output must stay inside the workspace.") from exc
    payload = build_quality_panel(ws)
    _write_json_atomic(target, payload)
    return payload


def render_quality_summary(
    panel_payload: Mapping[str, Any],
    *,
    quality_panel_sha256: str,
) -> str:
    """Render a compact human-readable summary from a valid Quality Panel payload."""

    reason = validate_quality_panel_payload(panel_payload)
    if reason:
        raise QualityPanelError(f"quality_panel invalid: {reason}")
    panel_sha256 = _normalize_sha256(quality_panel_sha256)
    if not panel_sha256:
        raise QualityPanelError("quality_panel_sha256 must be a SHA-256 hex digest.")

    source = panel_payload.get("source_evidence")
    source = source if isinstance(source, Mapping) else {}
    gates = panel_payload.get("gates")
    gates = gates if isinstance(gates, Mapping) else {}
    claims = panel_payload.get("claims")
    claims = claims if isinstance(claims, Mapping) else {}
    delivery = panel_payload.get("delivery")
    delivery = delivery if isinstance(delivery, Mapping) else {}
    control = panel_payload.get("control_integrity")
    control = control if isinstance(control, Mapping) else {}
    materiality = panel_payload.get("materiality_selection")
    materiality = materiality if isinstance(materiality, Mapping) else {}
    template_conformance = panel_payload.get("report_template_conformance")
    template_conformance = template_conformance if isinstance(template_conformance, Mapping) else {}
    support_wording = panel_payload.get("support_wording")
    support_wording = support_wording if isinstance(support_wording, Mapping) else {}
    closeout = panel_payload.get("quality_panel_closeout")
    closeout = closeout if isinstance(closeout, Mapping) else {}
    actions = panel_payload.get("recommended_actions")
    actions = actions if isinstance(actions, list) else []

    lines = [
        "# Quality Summary",
        "",
        f"Boundary: {QUALITY_SUMMARY_BOUNDARY}.",
        f"{_QUALITY_PANEL_SHA_PREFIX}{panel_sha256}",
        "",
        "This summary is a read-only operator view of existing BriefLoop control artifacts.",
        "Use the source gate reports, artifact registry, event log, and human review records as authority.",
        "",
        "## Overall",
        "",
        f"- Overall status: `{_text(panel_payload.get('overall_status')) or 'unknown'}`",
        f"- Run ID: `{_text(panel_payload.get('run_id')) or 'unknown'}`",
        f"- Runtime effect: `{_text(panel_payload.get('runtime_effect')) or 'unknown'}`",
        f"- Quality Panel boundary: `{_text(panel_payload.get('boundary')) or 'unknown'}`",
        "",
        "## Blocking Issues",
        "",
    ]
    _extend_bullets(lines, _quality_summary_blocking_items(control, gates, claims, delivery))
    lines.extend(["", "## Warnings", ""])
    _extend_bullets(
        lines,
        _quality_summary_warning_items(
            source,
            gates,
            claims,
            delivery,
            materiality,
            template_conformance,
            support_wording,
        ),
    )
    lines.extend(["", "## Missing Or Incomplete Surfaces", ""])
    _extend_bullets(lines, _quality_summary_missing_items(control, source, gates, delivery))
    lines.extend(["", "## Source Evidence", ""])
    lines.extend([
        f"- Source pack status: `{_text(source.get('source_pack_status')) or 'unknown'}`",
        f"- Durable source records: `{_intish(source.get('source_count'))}`",
        f"- Missing source titles: `{_intish(source.get('missing_title_count'))}`",
        f"- Missing publishers/institutions: `{_intish(source.get('missing_publisher_count'))}`",
        f"- Retrieval source mix: {_inline_mapping(source.get('retrieval_source_mix'))}",
        f"- Underlying evidence mix: {_inline_mapping(source.get('underlying_evidence_mix'))}",
        "",
        "## Gates And Reader Clean",
        "",
        f"- Auditor gate: `{_text(gates.get('auditor_status')) or 'unknown'}`",
        f"- Finalize gate: `{_text(gates.get('finalize_status')) or 'unknown'}`",
        f"- Legacy/latest gate report: `{_text(gates.get('legacy_quality_gate_status')) or 'missing'}`",
        f"- Gate blocking findings: `{_intish(gates.get('blocking_count'))}`",
        f"- Gate warnings: `{_intish(gates.get('warning_count'))}`",
        f"- Reader-clean status: `{_text(delivery.get('reader_clean_status')) or 'unknown'}`",
        f"- Duplicate citation count: `{_intish(delivery.get('duplicate_citation_count'))}`",
        f"- Source appendix warnings: `{_intish(delivery.get('source_appendix_warning_count'))}`",
        "",
        "## Claims And Support Records",
        "",
        f"- Claim count: `{_intish(claims.get('claim_count'))}`",
        f"- Claim-Support Matrix status: `{_text(claims.get('claim_support_matrix_status')) or 'unknown'}`",
        f"- Unsupported/contradicted/insufficient support rows: `{_intish(claims.get('unsupported_count'))}`",
        f"- Weak-support atoms: `{_intish(claims.get('weak_support_count'))}`",
        f"- Materiality selection status: `{_text(materiality.get('status')) or 'unknown'}`",
        "- Materiality/focus exclusions: "
        f"`{_materiality_selection_warning_count(materiality)}`",
        "- Reader template conformance: "
        f"`{_text(template_conformance.get('status')) or 'unknown'}`",
        "- Reader template warnings: "
        f"`{_template_conformance_warning_count(template_conformance)}`",
        f"- Support wording status: `{_text(support_wording.get('status')) or 'unknown'}`",
        f"- Support wording warnings: `{_support_wording_warning_count(support_wording)}`",
        "",
        "## Quality Closeout And Bundle Separation",
        "",
        f"- Quality closeout status: `{_text(closeout.get('status')) or 'unknown'}`",
        f"- Closeout command: `{_text(closeout.get('command')) or 'unknown'}`",
        f"- Audit bundle: `{_text(closeout.get('audit_bundle')) or 'unknown'}`",
        f"- Delivery bundle: `{_text(closeout.get('delivery_bundle')) or 'unknown'}`",
        "",
        "## Recommended Next Actions",
        "",
    ])
    _extend_bullets(lines, _quality_summary_action_items(actions))
    text = "\n".join(lines).rstrip() + "\n"
    reason = validate_quality_summary_markdown(text)
    if reason:
        raise QualityPanelError(f"quality_summary invalid: {reason}")
    return text


def write_quality_summary(
    *,
    workspace: str | Path,
    output_path: str | Path | None = None,
    panel_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    panel_path = quality_panel_path(ws)
    source_panel_payload = _read_json_mapping(panel_path)
    if source_panel_payload is None:
        raise QualityPanelError("quality_panel.json is required before writing quality_summary.md.")
    if panel_payload is None:
        panel_payload = source_panel_payload
    elif dict(panel_payload) != source_panel_payload:
        raise QualityPanelError("panel_payload must match quality_panel.json before writing quality_summary.md.")
    text = render_quality_summary(panel_payload, quality_panel_sha256=_sha256_file(panel_path))
    target = Path(output_path).expanduser() if output_path else quality_summary_path(ws)
    if not target.is_absolute():
        target = ws / target
    target = target.resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ValueError("quality_summary output must stay inside the workspace.") from exc
    _write_text_atomic(target, text)
    return {
        "path": _workspace_relative(ws, target),
        "sha256": _sha256_text(text),
    }


def render_quality_panel_html(
    panel_payload: Mapping[str, Any],
    *,
    quality_panel_sha256: str,
) -> str:
    """Render a static, dependency-free Quality Panel HTML audit attachment."""

    reason = validate_quality_panel_payload(panel_payload)
    if reason:
        raise QualityPanelError(f"quality_panel invalid: {reason}")
    panel_sha256 = _normalize_sha256(quality_panel_sha256)
    if not panel_sha256:
        raise QualityPanelError("quality_panel_sha256 must be a SHA-256 hex digest.")

    source = panel_payload.get("source_evidence")
    source = source if isinstance(source, Mapping) else {}
    gates = panel_payload.get("gates")
    gates = gates if isinstance(gates, Mapping) else {}
    claims = panel_payload.get("claims")
    claims = claims if isinstance(claims, Mapping) else {}
    delivery = panel_payload.get("delivery")
    delivery = delivery if isinstance(delivery, Mapping) else {}
    control = panel_payload.get("control_integrity")
    control = control if isinstance(control, Mapping) else {}
    materiality = panel_payload.get("materiality_selection")
    materiality = materiality if isinstance(materiality, Mapping) else {}
    template_conformance = panel_payload.get("report_template_conformance")
    template_conformance = template_conformance if isinstance(template_conformance, Mapping) else {}
    support_wording = panel_payload.get("support_wording")
    support_wording = support_wording if isinstance(support_wording, Mapping) else {}
    closeout = panel_payload.get("quality_panel_closeout")
    closeout = closeout if isinstance(closeout, Mapping) else {}
    actions = panel_payload.get("recommended_actions")
    actions = actions if isinstance(actions, list) else []
    overall_status = _text(panel_payload.get("overall_status")) or "unknown"

    body = "\n".join(
        [
            _html_header_card(panel_payload, overall_status=overall_status),
            _html_metrics_grid(
                [
                    ("gate_blockers", _intish(gates.get("blocking_count")), "block"),
                    ("gate_warnings", _intish(gates.get("warning_count")), "warning"),
                    (
                        "missing_incomplete",
                        _quality_panel_incomplete_count(control, source, gates, delivery),
                        "incomplete",
                    ),
                    (
                        "materiality_findings",
                        _materiality_selection_warning_count(materiality),
                        "warning",
                    ),
                    (
                        "template_warnings",
                        _template_conformance_warning_count(template_conformance),
                        "warning",
                    ),
                    (
                        "support_wording",
                        _support_wording_warning_count(support_wording),
                        "warning",
                    ),
                    ("recommended_actions", len(actions), "action"),
                ]
            ),
            _html_section(
                "control-integrity",
                "control_integrity",
                [
                    ("run_integrity", _text(control.get("run_integrity")) or "unknown", "status"),
                    (
                        "reference_eligible",
                        str(bool(control.get("reference_eligible"))).lower(),
                        "status",
                    ),
                    ("fact_layer", _text(control.get("fact_layer_status")) or "unknown", "status"),
                    (
                        "runtime_effect",
                        _text(panel_payload.get("runtime_effect")) or "unknown",
                        "status",
                    ),
                ],
            ),
            _html_section(
                "source-evidence",
                "source_evidence",
                [
                    ("source_pack", _text(source.get("source_pack_status")) or "unknown", "status"),
                    ("durable_sources", _intish(source.get("source_count")), "count_neutral"),
                    ("missing_titles", _intish(source.get("missing_title_count")), "count_warning"),
                    (
                        "missing_publishers",
                        _intish(source.get("missing_publisher_count")),
                        "count_warning",
                    ),
                    (
                        "retrieval_source_mix",
                        _inline_mapping(source.get("retrieval_source_mix")),
                        "text",
                    ),
                    (
                        "underlying_evidence_mix",
                        _inline_mapping(source.get("underlying_evidence_mix")),
                        "text",
                    ),
                ],
            ),
            _html_section(
                "gate-findings",
                "gate_findings",
                [
                    ("auditor_gate", _text(gates.get("auditor_status")) or "unknown", "status"),
                    ("finalize_gate", _text(gates.get("finalize_status")) or "unknown", "status"),
                    (
                        "legacy_latest_gate",
                        _text(gates.get("legacy_quality_gate_status")) or "missing",
                        "status",
                    ),
                    ("blocking_findings", _intish(gates.get("blocking_count")), "count_block"),
                    ("warning_findings", _intish(gates.get("warning_count")), "count_warning"),
                ],
            ),
            _html_section(
                "claim-support-risk",
                "claim_support_risk",
                [
                    ("claim_count", _intish(claims.get("claim_count")), "count_neutral"),
                    (
                        "claim_support_matrix",
                        _text(claims.get("claim_support_matrix_status")) or "unknown",
                        "status",
                    ),
                    (
                        "unsupported_rows",
                        _intish(claims.get("unsupported_count")),
                        "count_block",
                    ),
                    (
                        "weak_support_atoms",
                        _intish(claims.get("weak_support_count")),
                        "count_warning",
                    ),
                    (
                        "materiality_selection",
                        _text(materiality.get("status")) or "unknown",
                        "status",
                    ),
                    (
                        "materiality_exclusions",
                        _materiality_selection_warning_count(materiality),
                        "count_warning",
                    ),
                    (
                        "reader_template_conformance",
                        _text(template_conformance.get("status")) or "unknown",
                        "status",
                    ),
                    (
                        "reader_template_warnings",
                        _template_conformance_warning_count(template_conformance),
                        "count_warning",
                    ),
                    (
                        "support_wording",
                        _text(support_wording.get("status")) or "unknown",
                        "status",
                    ),
                    (
                        "support_wording_warnings",
                        _support_wording_warning_count(support_wording),
                        "count_warning",
                    ),
                ],
            ),
            _html_section(
                "reader-clean-citation-hygiene",
                "reader_clean_citation",
                [
                    (
                        "reader_clean_status",
                        _text(delivery.get("reader_clean_status")) or "unknown",
                        "status",
                    ),
                    (
                        "duplicate_citation_count",
                        _intish(delivery.get("duplicate_citation_count")),
                        "count_warning",
                    ),
                    (
                        "source_appendix_warnings",
                        _intish(delivery.get("source_appendix_warning_count")),
                        "count_warning",
                    ),
                ],
            ),
            _html_section(
                "quality-closeout-bundle-separation",
                "quality_closeout_bundle",
                [
                    ("closeout_status", _text(closeout.get("status")) or "unknown", "status"),
                    ("closeout_command", _text(closeout.get("command")) or "unknown", "code"),
                    ("audit_bundle", _text(closeout.get("audit_bundle")) or "unknown", "status"),
                    ("delivery_bundle", _text(closeout.get("delivery_bundle")) or "unknown", "status"),
                ],
            ),
            _html_actions(actions),
        ]
    )

    html = (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <meta name=\"briefloop-boundary\" content=\"{_html(QUALITY_PANEL_HTML_BOUNDARY)}\">\n"
        "  <title>BriefLoop Quality Panel</title>\n"
        "  <style>\n"
        f"{_quality_panel_css()}\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"<!-- {_QUALITY_PANEL_SHA_PREFIX}{panel_sha256} -->\n"
        f"<!-- Boundary: {_html(QUALITY_PANEL_HTML_BOUNDARY)} -->\n"
        "<input class=\"language-radio\" id=\"lang-en\" name=\"qp-lang\" type=\"radio\" checked>\n"
        "<input class=\"language-radio\" id=\"lang-zh\" name=\"qp-lang\" type=\"radio\">\n"
        "<div class=\"language-toggle\" aria-label=\"Language\">\n"
        "  <label for=\"lang-en\">English</label>\n"
        "  <label for=\"lang-zh\">中文</label>\n"
        "</div>\n"
        "<main class=\"quality-panel\">\n"
        f"{body}\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
    reason = validate_quality_panel_html(html)
    if reason:
        raise QualityPanelError(f"quality_panel_html invalid: {reason}")
    return html


def write_quality_panel_html(
    *,
    workspace: str | Path,
    output_path: str | Path | None = None,
    panel_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    panel_path = quality_panel_path(ws)
    source_panel_payload = _read_json_mapping(panel_path)
    if source_panel_payload is None:
        raise QualityPanelError("quality_panel.json is required before writing quality_panel.html.")
    if panel_payload is None:
        panel_payload = source_panel_payload
    elif dict(panel_payload) != source_panel_payload:
        raise QualityPanelError("panel_payload must match quality_panel.json before writing quality_panel.html.")
    text = render_quality_panel_html(panel_payload, quality_panel_sha256=_sha256_file(panel_path))
    target = Path(output_path).expanduser() if output_path else quality_panel_html_path(ws)
    if not target.is_absolute():
        target = ws / target
    target = target.resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ValueError("quality_panel_html output must stay inside the workspace.") from exc
    _write_text_atomic(target, text)
    return {
        "path": _workspace_relative(ws, target),
        "sha256": _sha256_text(text),
    }


def validate_quality_panel_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "quality_panel_schema_error:not_object"
    if payload.get("schema_version") != QUALITY_PANEL_SCHEMA_VERSION:
        return "quality_panel_schema_error:schema_version"
    if payload.get("boundary") != QUALITY_PANEL_BOUNDARY:
        return "quality_panel_schema_error:boundary"
    if payload.get("runtime_effect") != QUALITY_PANEL_RUNTIME_EFFECT:
        return "quality_panel_schema_error:runtime_effect"
    if payload.get("workspace") != ".":
        return "quality_panel_schema_error:workspace"
    if not _text(payload.get("run_id")):
        return "quality_panel_schema_error:run_id"
    if payload.get("overall_status") not in {"pass", "warning", "block", "incomplete"}:
        return "quality_panel_schema_error:overall_status"
    for field in ("control_integrity", "source_evidence", "gates", "claims", "delivery"):
        if not isinstance(payload.get(field), dict):
            return f"quality_panel_schema_error:{field}"
    trajectory = payload.get("trajectory_regulation")
    if trajectory is not None:
        if not isinstance(trajectory, dict):
            return "quality_panel_schema_error:trajectory_regulation"
        trajectory_error = validate_trajectory_regulation_payload(trajectory)
        if trajectory_error:
            return f"quality_panel_schema_error:trajectory_regulation:{trajectory_error}"
    guidance = payload.get("guidance_manifestation")
    if guidance is not None:
        if not isinstance(guidance, dict):
            return "quality_panel_schema_error:guidance_manifestation"
        guidance_error = validate_guidance_manifestation_projection_payload(guidance)
        if guidance_error:
            return f"quality_panel_schema_error:guidance_manifestation:{guidance_error}"
    materiality = payload.get("materiality_selection")
    if materiality is not None:
        if not isinstance(materiality, dict):
            return "quality_panel_schema_error:materiality_selection"
        materiality_error = validate_materiality_selection_payload(materiality)
        if materiality_error:
            return f"quality_panel_schema_error:materiality_selection:{materiality_error}"
    template_conformance = payload.get("report_template_conformance")
    if template_conformance is not None:
        if not isinstance(template_conformance, dict):
            return "quality_panel_schema_error:report_template_conformance"
        template_error = validate_report_template_conformance_payload(template_conformance)
        if template_error:
            return f"quality_panel_schema_error:report_template_conformance:{template_error}"
    support_wording = payload.get("support_wording")
    if support_wording is not None:
        if not isinstance(support_wording, dict):
            return "quality_panel_schema_error:support_wording"
        support_wording_error = validate_support_wording_payload(support_wording)
        if support_wording_error:
            return f"quality_panel_schema_error:support_wording:{support_wording_error}"
    closeout = payload.get("quality_panel_closeout")
    if closeout is not None:
        if not isinstance(closeout, dict):
            return "quality_panel_schema_error:quality_panel_closeout"
        closeout_error = validate_quality_panel_closeout_payload(closeout)
        if closeout_error:
            return f"quality_panel_schema_error:quality_panel_closeout:{closeout_error}"
    recommended_actions = payload.get("recommended_actions")
    if not isinstance(recommended_actions, list):
        return "quality_panel_schema_error:recommended_actions"
    for item in recommended_actions:
        if not isinstance(item, dict):
            return "quality_panel_schema_error:recommended_actions"
        if _text(item.get("action")) not in _QUALITY_PANEL_RECOMMENDED_ACTIONS:
            return "quality_panel_schema_error:recommended_actions.action"
    if not isinstance(payload.get("non_goals"), list):
        return "quality_panel_schema_error:non_goals"
    forbidden = {"semantic_truth_proof", "release_eligibility_decision", "delivery_approval"}
    if not forbidden.issubset(set(str(item) for item in payload.get("non_goals", []))):
        return "quality_panel_schema_error:non_goals"
    return None


def validate_quality_summary_markdown(text: Any) -> str | None:
    if not isinstance(text, str):
        return "quality_summary_schema_error:not_text"
    if not text.strip():
        return "quality_summary_schema_error:empty"
    if not text.startswith("# Quality Summary\n"):
        return "quality_summary_schema_error:title"
    if f"Boundary: {QUALITY_SUMMARY_BOUNDARY}." not in text:
        return "quality_summary_schema_error:boundary"
    panel_sha = quality_summary_panel_sha256(text)
    if not panel_sha:
        return "quality_summary_schema_error:quality_panel_sha256"
    lower = text.lower()
    for phrase in _QUALITY_SUMMARY_FORBIDDEN_PHRASES:
        if phrase in lower:
            return f"quality_summary_schema_error:forbidden_phrase:{phrase.replace(' ', '_')}"
    required_sections = (
        "## Overall",
        "## Blocking Issues",
        "## Warnings",
        "## Missing Or Incomplete Surfaces",
        "## Source Evidence",
        "## Gates And Reader Clean",
        "## Claims And Support Records",
        "## Recommended Next Actions",
    )
    for section in required_sections:
        if section not in text:
            return f"quality_summary_schema_error:missing_section:{section[3:].lower().replace(' ', '_')}"
    return None


def validate_quality_panel_html(text: Any) -> str | None:
    if not isinstance(text, str):
        return "quality_panel_html_schema_error:not_text"
    if not text.strip():
        return "quality_panel_html_schema_error:empty"
    if not text.startswith("<!doctype html>\n"):
        return "quality_panel_html_schema_error:doctype"
    if QUALITY_PANEL_HTML_BOUNDARY not in text:
        return "quality_panel_html_schema_error:boundary"
    panel_sha = quality_panel_html_panel_sha256(text)
    if not panel_sha:
        return "quality_panel_html_schema_error:quality_panel_sha256"
    lower = text.lower()
    for marker in _QUALITY_PANEL_HTML_FORBIDDEN_MARKERS:
        if marker in lower:
            return f"quality_panel_html_schema_error:external_or_active_content:{marker.strip('<>= ')}"
    required_language_toggle = (
        'id="lang-en"',
        'id="lang-zh"',
        'for="lang-en">English</label>',
        'for="lang-zh">中文</label>',
        'class="lang-en"',
        'class="lang-zh"',
    )
    for fragment in required_language_toggle:
        if fragment not in text:
            return f"quality_panel_html_schema_error:missing_language_toggle:{fragment}"
    required_sections = (
        'data-section="control-integrity"',
        'data-section="source-evidence"',
        'data-section="gate-findings"',
        'data-section="claim-support-risk"',
        'data-section="reader-clean-citation-hygiene"',
        'data-section="quality-closeout-bundle-separation"',
        'data-section="recommended-next-actions"',
    )
    for section in required_sections:
        if section not in text:
            return f"quality_panel_html_schema_error:missing_section:{section}"
    return None


def quality_summary_panel_sha256(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(_QUALITY_PANEL_SHA_PREFIX):
            return _normalize_sha256(line.removeprefix(_QUALITY_PANEL_SHA_PREFIX).strip())
    return None


def quality_panel_html_panel_sha256(text: str) -> str | None:
    for line in text.splitlines():
        if _QUALITY_PANEL_SHA_PREFIX not in line:
            continue
        value = line.split(_QUALITY_PANEL_SHA_PREFIX, 1)[1]
        value = value.split("-->", 1)[0].strip()
        return _normalize_sha256(value)
    return None


def _source_evidence_summary(workspace: Path, artifacts: Mapping[str, Any]) -> dict[str, Any]:
    record = _artifact_record(artifacts, "source_evidence_pack_manifest")
    source_pack_status = _source_pack_status(record)
    if source_pack_status == "present":
        manifest = _read_json_mapping(workspace / _INTERMEDIATE / "source_evidence_pack_manifest.json") or {}
    else:
        manifest = {}
    records = manifest.get("records") if isinstance(manifest, dict) else []
    records = records if isinstance(records, list) else []
    retrieval_mix: Counter[str] = Counter()
    underlying_mix: Counter[str] = Counter()
    missing_title_count = 0
    missing_publisher_count = 0
    usable_records = 0
    for item in records:
        if not isinstance(item, dict):
            continue
        usable_records += 1
        title = _first_text(item, "source_title", "title", "source_name")
        publisher = _first_text(item, "publisher", "publisher_or_institution", "source_name")
        if not title:
            missing_title_count += 1
        if not publisher:
            missing_publisher_count += 1
        retrieval_mix[_first_text(item, "retrieval_source_type") or "unknown"] += 1
        underlying_mix[_first_text(item, "underlying_evidence_type", "source_category") or "unknown"] += 1
    return {
        "source_pack_status": source_pack_status,
        "source_count": int(manifest.get("record_count") or usable_records or 0)
        if isinstance(manifest, dict)
        else 0,
        "missing_title_count": missing_title_count,
        "missing_publisher_count": missing_publisher_count,
        "retrieval_source_mix": dict(sorted(retrieval_mix.items())),
        "underlying_evidence_mix": dict(sorted(underlying_mix.items())),
    }


def _gate_summary(workspace: Path) -> dict[str, Any]:
    auditor = _gate_file_summary(workspace / _INTERMEDIATE / "gates" / "auditor_quality_gate_report.json")
    finalize = _gate_file_summary(workspace / _INTERMEDIATE / "gates" / "finalize_quality_gate_report.json")
    legacy = _gate_file_summary(workspace / _INTERMEDIATE / "quality_gate_report.json")
    return {
        "auditor_status": auditor["status"],
        "finalize_status": finalize["status"],
        "auditor_report_status": _scoped_gate_report_status(auditor),
        "finalize_report_status": _scoped_gate_report_status(finalize),
        "legacy_quality_gate_present": legacy["present"],
        "legacy_quality_gate_status": legacy["status"],
        "legacy_quality_gate_stage": legacy["stage"],
        "blocking_count": auditor["blocking_count"] + finalize["blocking_count"],
        "warning_count": auditor["warning_count"] + finalize["warning_count"],
    }


def _gate_file_summary(path: Path) -> dict[str, bool | int | str]:
    payload = _read_json_mapping(path)
    if payload is None:
        return {
            "present": False,
            "status": "missing",
            "stage": "",
            "blocking_count": 0,
            "warning_count": 0,
        }
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    blocking = 0
    warning = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("blocking") is True or finding.get("blocking_level") == "blocking":
            blocking += 1
        else:
            warning += 1
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    status = _text(payload.get("status")) or "unknown"
    stage = _text(metadata.get("gate_stage_id")) or _text(payload.get("stage")) or _text(payload.get("gate_stage"))
    return {
        "present": True,
        "status": status,
        "stage": stage,
        "blocking_count": blocking,
        "warning_count": warning,
    }


def _scoped_gate_report_status(summary: Mapping[str, Any]) -> str:
    return "present" if summary.get("present") is True else "missing_scoped_report"


def _claim_summary(
    workspace: Path,
    workspace_status: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    matrix = workspace_status.get("claim_support_matrix")
    matrix = matrix if isinstance(matrix, dict) else {}
    matrix_status = _optional_artifact_status(
        _artifact_record(artifacts, "claim_support_matrix"),
        not_available="not_available",
    )
    counts = (
        matrix.get("summary_counts")
        if matrix_status == "valid" and isinstance(matrix.get("summary_counts"), dict)
        else {}
    )
    rows = _matrix_rows(workspace) if matrix_status == "valid" else []
    return {
        "claim_count": _claim_count(workspace / _INTERMEDIATE / "claim_ledger.json"),
        "claim_support_matrix_status": matrix_status,
        "weak_support_count": int(counts.get("weak_atom_count") or 0),
        "unsupported_count": sum(
            1
            for row in rows
            if isinstance(row, dict) and _text(row.get("support_label")) in _BLOCKING_SUPPORT_LABELS
        ),
    }


def _delivery_summary(workspace: Path, workspace_status: Mapping[str, Any]) -> dict[str, Any]:
    reader = workspace_status.get("reader_clean")
    reader = reader if isinstance(reader, dict) else {}
    finalize_report = _read_json_mapping(workspace / _INTERMEDIATE / "finalize_report.json") or {}
    source_warnings = finalize_report.get("source_appendix_warnings")
    trace_warnings = finalize_report.get("source_appendix_trace_warnings")
    source_warning_count = len(source_warnings) if isinstance(source_warnings, list) else 0
    trace_warning_count = len(trace_warnings) if isinstance(trace_warnings, list) else 0
    return {
        "reader_clean_status": reader.get("status") or "missing",
        "duplicate_citation_count": int(finalize_report.get("duplicate_citation_count") or 0)
        if isinstance(finalize_report, dict)
        else 0,
        "source_appendix_warning_count": source_warning_count + trace_warning_count,
    }


def _overall_status(
    *,
    workspace_status: Mapping[str, Any],
    workflow: Mapping[str, Any],
    control_integrity: Mapping[str, Any],
    source_evidence: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
    materiality_selection: Mapping[str, Any],
    report_template_conformance: Mapping[str, Any],
    support_wording: Mapping[str, Any],
) -> str:
    if not workspace_status.get("ok"):
        return "incomplete"
    auditor_gate_level = _gate_status_level(gates.get("auditor_status"))
    finalize_gate_level = _gate_status_level(gates.get("finalize_status"))
    reader_clean_status = _text(delivery.get("reader_clean_status"))
    if (
        workflow.get("blocked") or
        control_integrity.get("run_integrity") not in {"clean", "unknown"}
        or gates.get("blocking_count", 0) > 0
        or auditor_gate_level == "block"
        or finalize_gate_level == "block"
        or reader_clean_status == "fail"
        or claims.get("unsupported_count", 0) > 0
    ):
        return "block"
    if (
        control_integrity.get("fact_layer_status") in {"missing", "incomplete"}
        or source_evidence.get("source_pack_status") in {"missing", "not_available"}
        or auditor_gate_level in {"missing", "incomplete"}
        or finalize_gate_level in {"missing", "incomplete"}
        or reader_clean_status != "pass"
    ):
        return "incomplete"
    if (
        source_evidence.get("source_pack_status") == "invalid"
        or claims.get("claim_support_matrix_status") == "invalid"
        or auditor_gate_level == "warning"
        or finalize_gate_level == "warning"
        or gates.get("warning_count", 0) > 0
        or delivery.get("source_appendix_warning_count", 0) > 0
        or claims.get("weak_support_count", 0) > 0
        or _materiality_selection_warning_count(materiality_selection) > 0
        or _template_conformance_warning_count(report_template_conformance) > 0
        or _support_wording_warning_count(support_wording) > 0
    ):
        return "warning"
    return "pass"


def _recommended_actions(
    *,
    workflow: Mapping[str, Any],
    control_integrity: Mapping[str, Any],
    source_evidence: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
    trajectory: Mapping[str, Any],
    materiality_selection: Mapping[str, Any],
    report_template_conformance: Mapping[str, Any],
    support_wording: Mapping[str, Any],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if workflow.get("blocked"):
        actions.append({
            "action": "inspect_workflow_blocker",
            "reason": _text(workflow.get("blocking_reason")) or "workflow_blocked",
        })
    if source_evidence.get("source_pack_status") in {"missing", "not_available"}:
        actions.append({
            "action": "materialize_durable_source_evidence",
            "reason": "source_evidence_pack_missing",
        })
    elif source_evidence.get("source_pack_status") == "invalid":
        actions.append({
            "action": "repair_source_evidence_pack_manifest",
            "reason": "source_evidence_pack_invalid",
        })
    if gates.get("blocking_count", 0) > 0:
        actions.append({"action": "resolve_quality_gate_blockers", "reason": "blocking_gate_findings"})
    gate_levels = {
        "auditor": _gate_status_level(gates.get("auditor_status")),
        "finalize": _gate_status_level(gates.get("finalize_status")),
    }
    failed_gate_stages = [stage for stage, level in gate_levels.items() if level == "block"]
    if failed_gate_stages and gates.get("blocking_count", 0) == 0:
        actions.append({
            "action": "resolve_quality_gate_blockers",
            "reason": "quality_gate_status_failed",
        })
    scoped_reports_missing = any(level in {"missing", "incomplete"} for level in gate_levels.values())
    reader_clean_missing = _text(delivery.get("reader_clean_status")) in {"", "missing", "unknown", "invalid"}
    if scoped_reports_missing and not reader_clean_missing:
        actions.append({
            "action": "regenerate_scoped_gate_reports",
            "reason": "scoped_quality_gate_reports_missing",
        })
    elif scoped_reports_missing or reader_clean_missing:
        actions.append({
            "action": "complete_finalize_delivery_hygiene",
            "reason": "finalize_or_reader_clean_missing",
        })
    if claims.get("unsupported_count", 0) > 0:
        actions.append({"action": "review_claim_support_records", "reason": "unsupported_claim_support_rows"})
    if delivery.get("reader_clean_status") == "fail":
        actions.append({"action": "repair_reader_final_residue", "reason": "reader_clean_failed"})
    if control_integrity.get("run_integrity") not in {"clean", "unknown"}:
        actions.append({"action": "inspect_run_integrity", "reason": "run_integrity_not_clean"})
    materiality_counts = (
        materiality_selection.get("summary_counts")
        if isinstance(materiality_selection.get("summary_counts"), Mapping)
        else {}
    )
    if int(materiality_counts.get("human_review_recommended_count") or 0) > 0:
        actions.append({
            "action": "request_human_review",
            "reason": "materiality_or_focus_candidate_excluded_by_capacity_or_scope",
        })
    elif int(materiality_counts.get("finding_count") or 0) > 0:
        actions.append({
            "action": "review_materiality_exclusions",
            "reason": "materiality_or_focus_candidate_deprioritized",
        })
    if _template_conformance_warning_count(report_template_conformance) > 0:
        actions.append({
            "action": "review_reader_template_conformance",
            "reason": "reader_template_conformance_warning_only",
        })
    support_counts = (
        support_wording.get("summary_counts")
        if isinstance(support_wording.get("summary_counts"), Mapping)
        else {}
    )
    if int(support_counts.get("unsupported_reader_claim_count") or 0) > 0:
        actions.append({
            "action": "request_human_review",
            "reason": "unsupported_claim_present_in_reader_text",
        })
    elif _support_wording_warning_count(support_wording) > 0:
        actions.append({
            "action": "review_support_wording_warnings",
            "reason": "support_calibrated_wording_warning_only",
        })
    for item in trajectory.get("recommended_actions") or []:
        if not isinstance(item, Mapping):
            continue
        action = _text(item.get("action"))
        if action not in {"request_human_review", "block_run"}:
            continue
        actions.append({
            "action": action,
            "reason": _text(item.get("reason")) or "trajectory_regulation",
            "stage_id": _text(item.get("stage_id")) or "unknown",
        })
    return actions[:20]


def _materiality_selection_warning_count(materiality_selection: Mapping[str, Any]) -> int:
    counts = (
        materiality_selection.get("summary_counts")
        if isinstance(materiality_selection.get("summary_counts"), Mapping)
        else {}
    )
    return int(counts.get("finding_count") or 0)


def _template_conformance_warning_count(report_template_conformance: Mapping[str, Any]) -> int:
    counts = (
        report_template_conformance.get("summary_counts")
        if isinstance(report_template_conformance.get("summary_counts"), Mapping)
        else {}
    )
    return (
        int(counts.get("reader_block_warning_count") or 0)
        + int(counts.get("missing_section_count") or 0)
        + int(counts.get("out_of_order_section_count") or 0)
        + int(counts.get("extra_heading_count") or 0)
    )


def _support_wording_warning_count(support_wording: Mapping[str, Any]) -> int:
    counts = (
        support_wording.get("summary_counts")
        if isinstance(support_wording.get("summary_counts"), Mapping)
        else {}
    )
    return int(counts.get("finding_count") or 0)


def _fact_layer_status(artifacts: Mapping[str, Any], source_evidence: Mapping[str, Any]) -> str:
    claim_ledger_status = _optional_artifact_status(_artifact_record(artifacts, "claim_ledger"))
    source_pack_status = str(source_evidence.get("source_pack_status") or "")
    if claim_ledger_status == "valid" and source_pack_status == "present":
        return "complete"
    if claim_ledger_status in {"missing", "not_available"}:
        return "missing"
    return "incomplete"


def _source_pack_status(record: Mapping[str, Any] | None) -> str:
    status = _optional_artifact_status(record, not_available="not_available")
    if status == "valid":
        return "present"
    if status in {"expected", "missing", "not_available"}:
        return "missing"
    if status in {"invalid", "stale"}:
        return "invalid"
    return status


def _gate_status_level(value: Any) -> str:
    status = _text(value)
    if status == "pass":
        return "pass"
    if status in {"fail", "failed", "block", "blocked", "blocking"}:
        return "block"
    if status == "warning":
        return "warning"
    if status == "missing":
        return "missing"
    return "incomplete"


def _optional_artifact_status(
    record: Mapping[str, Any] | None,
    *,
    not_available: str = "not_available",
) -> str:
    if not isinstance(record, Mapping):
        return not_available
    status = _text(record.get("status"))
    return status or not_available


def _artifact_record(artifacts: Mapping[str, Any], artifact_id: str) -> Mapping[str, Any] | None:
    record = artifacts.get(artifact_id)
    return record if isinstance(record, Mapping) else None


def _claim_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return len(ClaimLedger._claim_items_from_json(payload))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def _matrix_rows(workspace: Path) -> list[dict[str, Any]]:
    payload = _read_json_mapping(workspace / _INTERMEDIATE / "claim_support_matrix.json")
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _quality_summary_blocking_items(
    control: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> list[str]:
    items: list[str] = []
    if _text(control.get("run_integrity")) not in {"", "clean", "unknown"}:
        items.append(f"Run integrity is `{_text(control.get('run_integrity'))}`.")
    if _intish(gates.get("blocking_count")) > 0:
        items.append(f"Quality gates report `{_intish(gates.get('blocking_count'))}` blocking finding(s).")
    if _gate_status_level(gates.get("auditor_status")) == "block":
        items.append(f"Auditor gate status is `{_text(gates.get('auditor_status'))}`.")
    if _gate_status_level(gates.get("finalize_status")) == "block":
        items.append(f"Finalize gate status is `{_text(gates.get('finalize_status'))}`.")
    if _text(delivery.get("reader_clean_status")) == "fail":
        items.append("Reader-clean status is `fail`.")
    if _intish(claims.get("unsupported_count")) > 0:
        items.append(
            "Claim-Support Matrix projection includes "
            f"`{_intish(claims.get('unsupported_count'))}` unsupported/contradicted/insufficient row(s)."
        )
    return items


def _quality_summary_warning_items(
    source: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
    materiality_selection: Mapping[str, Any],
    report_template_conformance: Mapping[str, Any],
    support_wording: Mapping[str, Any],
) -> list[str]:
    items: list[str] = []
    if _text(source.get("source_pack_status")) == "invalid":
        items.append("Durable source evidence pack manifest is invalid.")
    if _intish(gates.get("warning_count")) > 0:
        items.append(f"Quality gates report `{_intish(gates.get('warning_count'))}` warning finding(s).")
    if _gate_status_level(gates.get("auditor_status")) == "warning":
        items.append("Auditor gate status is `warning`.")
    if _gate_status_level(gates.get("finalize_status")) == "warning":
        items.append("Finalize gate status is `warning`.")
    if _text(claims.get("claim_support_matrix_status")) == "invalid":
        items.append("Claim-Support Matrix is invalid and is not interpreted as support authority.")
    if _intish(claims.get("weak_support_count")) > 0:
        items.append(f"`{_intish(claims.get('weak_support_count'))}` atom(s) have weak-support projection.")
    if _intish(delivery.get("source_appendix_warning_count")) > 0:
        items.append(
            f"Source appendix surfaces `{_intish(delivery.get('source_appendix_warning_count'))}` warning(s)."
        )
    if _materiality_selection_warning_count(materiality_selection) > 0:
        items.append(
            "Materiality selection projection found "
            f"`{_materiality_selection_warning_count(materiality_selection)}` "
            "excluded/deprioritized candidate(s) matching explicit materiality or focus terms."
        )
    if _template_conformance_warning_count(report_template_conformance) > 0:
        items.append(
            "Reader template conformance projection found "
            f"`{_template_conformance_warning_count(report_template_conformance)}` warning(s)."
        )
    if _support_wording_warning_count(support_wording) > 0:
        items.append(
            "Support-calibrated wording projection found "
            f"`{_support_wording_warning_count(support_wording)}` reader wording warning(s)."
        )
    return items


def _quality_summary_missing_items(
    control: Mapping[str, Any],
    source: Mapping[str, Any],
    gates: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> list[str]:
    items: list[str] = []
    if _text(control.get("fact_layer_status")) in {"", "missing", "incomplete"}:
        items.append(f"Fact layer status is `{_text(control.get('fact_layer_status')) or 'unknown'}`.")
    if _text(source.get("source_pack_status")) in {"", "missing", "not_available"}:
        items.append("Durable source evidence pack is missing or not available.")
    if _gate_status_level(gates.get("auditor_status")) in {"missing", "incomplete"}:
        items.append(f"Auditor gate status is `{_text(gates.get('auditor_status')) or 'unknown'}`.")
    if _gate_status_level(gates.get("finalize_status")) in {"missing", "incomplete"}:
        items.append(f"Finalize gate status is `{_text(gates.get('finalize_status')) or 'unknown'}`.")
    if gates.get("legacy_quality_gate_present") is True:
        legacy_status = _text(gates.get("legacy_quality_gate_status")) or "unknown"
        legacy_stage = _text(gates.get("legacy_quality_gate_stage")) or "unknown"
        items.append(
            "Legacy/latest quality gate report is present "
            f"with status `{legacy_status}` and stage `{legacy_stage}`; "
            "scoped gate reports are still tracked separately."
        )
    if _text(delivery.get("reader_clean_status")) != "pass":
        items.append(f"Reader-clean status is `{_text(delivery.get('reader_clean_status')) or 'unknown'}`.")
    return items


def _quality_summary_action_items(actions: list[Any]) -> list[str]:
    items: list[str] = []
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        action_name = _text(action.get("action")) or "unknown_action"
        reason = _text(action.get("reason")) or "unspecified"
        items.append(f"`{action_name}` - {reason}.")
    return items


def _quality_panel_incomplete_count(
    control: Mapping[str, Any],
    source: Mapping[str, Any],
    gates: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> int:
    count = 0
    if _text(control.get("fact_layer_status")) in {"", "missing", "incomplete"}:
        count += 1
    if _text(source.get("source_pack_status")) in {"", "missing", "not_available"}:
        count += 1
    if _gate_status_level(gates.get("auditor_status")) in {"missing", "incomplete"}:
        count += 1
    if _gate_status_level(gates.get("finalize_status")) in {"missing", "incomplete"}:
        count += 1
    if _text(delivery.get("reader_clean_status")) != "pass":
        count += 1
    return count


def _html_header_card(panel_payload: Mapping[str, Any], *, overall_status: str) -> str:
    level = _status_level(overall_status)
    run_id = _text(panel_payload.get("run_id")) or "unknown"
    generated_at = _text(panel_payload.get("generated_at")) or "unknown"
    return (
        "<header class=\"panel-hero\" data-section=\"panel-header\">\n"
        "  <div>\n"
        f"    <p class=\"eyebrow\">{_html_label('audit_attachment')}</p>\n"
        f"    <h1>{_html_label('quality_panel')}</h1>\n"
        "    <p class=\"boundary\">"
        f"<span class=\"lang-en\" lang=\"en\">{_html(QUALITY_PANEL_HTML_BOUNDARY)}</span>"
        f"<span class=\"lang-zh\" lang=\"zh-CN\">{_html(QUALITY_PANEL_HTML_BOUNDARY_ZH)}</span>"
        "</p>\n"
        "  </div>\n"
        f"  <div class=\"status-pill level-{level}\"><span>{_html_label('overall_status')}</span>"
        f"<strong>{_html_bilingual_value(overall_status)}</strong></div>\n"
        f"  <dl class=\"hero-meta\"><dt>{_html_label('run_id')}</dt><dd>{_html(run_id)}</dd>"
        f"<dt>{_html_label('generated')}</dt><dd>{_html(generated_at)}</dd></dl>\n"
        "</header>"
    )


_METRIC_KIND_LEVELS = {
    "block": "block",
    "warning": "warning",
    "incomplete": "missing",
    "action": "info",
}


def _html_metrics_grid(metrics: list[tuple[str, int, str]]) -> str:
    cards = []
    for label_key, value, kind in metrics:
        level = _METRIC_KIND_LEVELS.get(kind, "info") if value > 0 else "zero"
        cards.append(
            "  <div class=\"metric-card\">"
            f"<span>{_html_label(label_key)}</span>"
            f"<strong class=\"metric level-{level}\">{value}</strong>"
            "</div>"
        )
    legend = "".join(
        f"<span class=\"badge badge-{level}\">{_html_label(f'legend_{level}')}</span>"
        for level in ("pass", "warning", "block", "missing", "info")
    )
    return (
        "<section class=\"metric-grid\" data-section=\"metrics\" aria-label=\"Quality metrics\">\n"
        + "\n".join(cards)
        + "\n</section>\n"
        "<p class=\"color-legend\" data-section=\"color-legend\">"
        f"<span class=\"legend-title\">{_html_label('color_legend')}</span>{legend}</p>"
    )


def _html_section(section_id: str, title_key: str, rows: list[tuple[str, Any, str]]) -> str:
    items = []
    for label_key, value, kind in rows:
        items.append(
            "      <tr>"
            f"<th scope=\"row\">{_html_label(label_key)}</th>"
            f"<td>{_html_typed_value(value, kind)}</td>"
            "</tr>"
        )
    return (
        f"<section class=\"panel-section\" data-section=\"{_html(section_id)}\">\n"
        f"  <h2>{_html_label(title_key)}</h2>\n"
        "  <table>\n"
        "    <tbody>\n"
        + "\n".join(items)
        + "\n"
        "    </tbody>\n"
        "  </table>\n"
        "</section>"
    )


def _html_actions(actions: list[Any]) -> str:
    items = []
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        action_name = _text(action.get("action")) or "unknown_action"
        reason = _text(action.get("reason")) or "unspecified"
        action_zh = _QUALITY_PANEL_HTML_ACTIONS_ZH.get(action_name, action_name)
        reason_zh = _QUALITY_PANEL_HTML_REASONS_ZH.get(reason, reason)
        items.append(
            "<li>"
            "<strong>"
            f"<span class=\"lang-en\" lang=\"en\">{_html(action_name)}</span>"
            f"<span class=\"lang-zh\" lang=\"zh-CN\">{_html(action_zh)}</span>"
            "</strong>"
            "<span>"
            f"<span class=\"lang-en\" lang=\"en\">{_html(reason)}</span>"
            f"<span class=\"lang-zh\" lang=\"zh-CN\">{_html(reason_zh)}</span>"
            "</span>"
            "</li>"
        )
    if not items:
        items.append(f"<li><strong>{_html_label('none')}</strong><span>{_html_label('no_recommended_action')}</span></li>")
    return (
        "<section class=\"panel-section actions\" data-section=\"recommended-next-actions\">\n"
        f"  <h2>{_html_label('recommended_next_actions')}</h2>\n"
        "  <ul>\n"
        + "\n".join(f"    {item}" for item in items)
        + "\n"
        "  </ul>\n"
        "</section>"
    )


_COUNT_KIND_LEVELS = {
    "count_block": "block",
    "count_warning": "warning",
    "count_neutral": None,
}


def _html_typed_value(value: Any, kind: str) -> str:
    if kind == "status":
        return _html_status_badge(value)
    if kind in _COUNT_KIND_LEVELS:
        count = _intish(value)
        level = _COUNT_KIND_LEVELS[kind]
        if level is None or count == 0:
            return f"<span class=\"count-zero\">{count}</span>" if count == 0 else str(count)
        return f"<span class=\"badge badge-{level}\">{count}</span>"
    if kind == "code":
        return f"<code>{_html(_text(value))}</code>"
    return _html_inline_value(_text(value))


def _html_status_badge(value: Any) -> str:
    raw = _text(value) or "unknown"
    level = _status_level(raw)
    return (
        f"<span class=\"badge badge-{level}\" title=\"{_html(raw)}\">"
        f"{_html_bilingual_value(raw)}"
        "</span>"
    )


def _html_bilingual_value(value: Any) -> str:
    raw = _text(value) or "unknown"
    zh = _QUALITY_PANEL_HTML_VALUES_ZH.get(raw, raw)
    return (
        f"<span class=\"lang-en\" lang=\"en\">{_html(raw)}</span>"
        f"<span class=\"lang-zh\" lang=\"zh-CN\">{_html(zh)}</span>"
    )


def _status_level(value: Any) -> str:
    raw = _text(value).lower()
    return _QUALITY_PANEL_STATUS_LEVELS.get(raw, "info")


def _html_inline_value(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("`") and stripped.endswith("`"):
        return f"<code>{_html(stripped.strip('`'))}</code>"
    return _html(stripped)


def _html(value: Any) -> str:
    return _html_escape(str(value), quote=True)


def _html_label(key: str) -> str:
    en, zh = _QUALITY_PANEL_HTML_LABELS.get(key, (key, key))
    return (
        f"<span class=\"lang-en\" lang=\"en\">{_html(en)}</span>"
        f"<span class=\"lang-zh\" lang=\"zh-CN\">{_html(zh)}</span>"
    )


def _quality_panel_css() -> str:
    return """    :root {
      color-scheme: light;
      --ink: #1f2937;
      --muted: #667085;
      --line: #d0d5dd;
      --panel: #ffffff;
      --bg: #f6f7f9;
      --pass-fg: #067647;
      --pass-bg: #ecfdf3;
      --pass-line: #abefc6;
      --warning-fg: #b54708;
      --warning-bg: #fffaeb;
      --warning-line: #fedf89;
      --block-fg: #b42318;
      --block-bg: #fef3f2;
      --block-line: #fecdca;
      --missing-fg: #475467;
      --missing-bg: #f2f4f7;
      --missing-line: #d0d5dd;
      --info-fg: #175cd3;
      --info-bg: #eff8ff;
      --info-line: #b2ddff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 32px;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI",
        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    }
    .language-radio {
      position: fixed;
      opacity: 0;
      pointer-events: none;
    }
    .language-toggle {
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 2;
      display: inline-flex;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 2px 8px rgba(16, 24, 40, .08);
    }
    .language-toggle label {
      cursor: pointer;
      padding: 7px 10px;
      color: var(--muted);
      font-weight: 700;
      line-height: 1;
      border-left: 1px solid var(--line);
      user-select: none;
    }
    .language-toggle label:first-child { border-left: 0; }
    #lang-en:checked ~ .language-toggle label[for="lang-en"],
    #lang-zh:checked ~ .language-toggle label[for="lang-zh"] {
      background: var(--ink);
      color: #fff;
    }
    .lang-zh { display: none; }
    #lang-zh:checked ~ main .lang-en { display: none; }
    #lang-zh:checked ~ main .lang-zh { display: inline; }
    #lang-en:checked ~ main .lang-en { display: inline; }
    #lang-en:checked ~ main .lang-zh { display: none; }
    .panel-hero, .panel-section, .metric-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .panel-hero {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 20px;
      padding: 24px;
      margin: 0 0 16px;
    }
    .eyebrow {
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    h1, h2 { margin: 0; line-height: 1.2; }
    h1 { font-size: 28px; }
    h2 { font-size: 18px; margin-bottom: 14px; }
    .boundary { max-width: 760px; color: var(--muted); margin: 10px 0 0; }
    .status-pill {
      align-self: start;
      min-width: 112px;
      padding: 8px 14px;
      border-radius: 999px;
      text-align: center;
      font-weight: 700;
      color: #fff;
      background: var(--missing-fg);
    }
    .status-pill span { display: block; font-size: 11px; opacity: .85; }
    .status-pill strong { display: block; margin-top: 2px; }
    .status-pill.level-pass { background: var(--pass-fg); }
    .status-pill.level-warning { background: var(--warning-fg); }
    .status-pill.level-block { background: var(--block-fg); }
    .status-pill.level-missing { background: var(--missing-fg); }
    .status-pill.level-info { background: var(--info-fg); }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 2px 10px;
      border-radius: 999px;
      border: 1px solid var(--missing-line);
      background: var(--missing-bg);
      color: var(--missing-fg);
      font-weight: 600;
      font-size: 13px;
      line-height: 1.6;
    }
    .badge::before {
      content: "";
      flex: none;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
    }
    .badge-pass { background: var(--pass-bg); color: var(--pass-fg); border-color: var(--pass-line); }
    .badge-warning { background: var(--warning-bg); color: var(--warning-fg); border-color: var(--warning-line); }
    .badge-block { background: var(--block-bg); color: var(--block-fg); border-color: var(--block-line); }
    .badge-missing { background: var(--missing-bg); color: var(--missing-fg); border-color: var(--missing-line); }
    .badge-info { background: var(--info-bg); color: var(--info-fg); border-color: var(--info-line); }
    .count-zero { color: var(--muted); }
    .color-legend {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin: 0 0 16px;
      color: var(--muted);
      font-size: 12px;
    }
    .color-legend .legend-title { font-weight: 700; margin-right: 4px; }
    .color-legend .badge { font-size: 12px; }
    .hero-meta {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: max-content 1fr;
      column-gap: 10px;
      row-gap: 2px;
      margin: 0;
      color: var(--muted);
    }
    .hero-meta dt { font-weight: 700; }
    .hero-meta dd { margin: 0; overflow-wrap: anywhere; }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .metric-card { padding: 16px; }
    .metric-card span { display: block; color: var(--muted); margin-bottom: 8px; }
    .metric-card strong {
      display: inline-flex;
      min-width: 44px;
      min-height: 36px;
      padding: 4px 10px;
      border-radius: 6px;
      align-items: center;
      justify-content: center;
      font-size: 22px;
      color: var(--ink);
      background: #eef2f6;
      border: 1px solid transparent;
    }
    .metric.level-pass { background: var(--pass-bg); color: var(--pass-fg); border-color: var(--pass-line); }
    .metric.level-warning { background: var(--warning-bg); color: var(--warning-fg); border-color: var(--warning-line); }
    .metric.level-block { background: var(--block-bg); color: var(--block-fg); border-color: var(--block-line); }
    .metric.level-missing { background: var(--missing-bg); color: var(--missing-fg); border-color: var(--missing-line); }
    .metric.level-info { background: var(--info-bg); color: var(--info-fg); border-color: var(--info-line); }
    .metric.level-zero { background: #eef2f6; color: var(--muted); }
    .panel-section { padding: 20px; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 0; border-top: 1px solid #eaecf0; text-align: left; vertical-align: top; }
    th { width: 36%; color: var(--muted); font-weight: 650; padding-right: 16px; }
    code {
      padding: 2px 5px;
      border-radius: 4px;
      background: #eef2f6;
      color: #344054;
    }
    .actions ul { list-style: none; padding: 0; margin: 0; }
    .actions li {
      display: grid;
      grid-template-columns: minmax(160px, 260px) 1fr;
      gap: 12px;
      padding: 10px 0;
      border-top: 1px solid #eaecf0;
    }
    .actions span { color: var(--muted); }
    @media (max-width: 720px) {
      body { padding: 16px; }
      .language-toggle { position: static; margin-bottom: 12px; }
      .panel-hero { grid-template-columns: 1fr; }
      .actions li { grid-template-columns: 1fr; }
      th, td { display: block; width: 100%; }
      th { padding-bottom: 2px; }
      td { padding-top: 0; }
    }"""


def _extend_bullets(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("- None reported by `quality_panel.json`.")
        return
    for item in items:
        lines.append(f"- {item}")


def _inline_mapping(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "`none`"
    parts = [
        f"{_text(key) or str(key)}={_intish(count)}"
        for key, count in sorted(value.items(), key=lambda item: str(item[0]))
    ]
    return "`" + ", ".join(parts) + "`"


def _intish(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.startswith("sha256:"):
        stripped = stripped.removeprefix("sha256:")
    if len(stripped) != 64:
        return None
    lowered = stripped.lower()
    if any(ch not in "0123456789abcdef" for ch in lowered):
        return None
    return lowered


def _workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_mapping(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
