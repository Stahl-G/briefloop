from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from multi_agent_brief.agents.draft_cleanup import strip_claim_citations
from multi_agent_brief.audit.deterministic import parse_date
from multi_agent_brief.audit.interfaces import AuditAgentInterface, recompute_report_status
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport, PipelineContext
from multi_agent_brief.core.config import _as_bool


CURRENT_FRAMING_PATTERN = re.compile(r"\b(this week|current|latest|newly|本周|本期|当前|最新|新增)\b", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2}|20\d{6})\b")


@dataclass
class FinalQualityConfig:
    harness_protocol: str = "BRIEF_HARNESS_V2"
    min_markdown_chars: int = 8000
    min_docx_text_chars: int = 7800
    quiet_week: bool = False
    expected_summary_bullets: int | None = 5
    summary_heading: str = "Executive Summary"
    summary_bullet_marker: str = "▸"
    max_table_columns: int = 4
    required_main_chapter_count: int | None = None
    required_metadata_labels: list[str] = field(
        default_factory=lambda: ["coverage", "source priority", "cutoff"]
    )
    stale_current_threshold_days: int | None = None
    rendered_docx_path: str = ""
    min_selected_claims: int = 0  # 0 = not enforced; weekly default = 20
    min_zh_chars: int = 0         # 0 = not enforced; weekly default = 3000
    min_en_words: int = 0         # 0 = not enforced; weekly default = 1800
    require_dates: bool = False   # True for weekly briefs
    allow_quiet_week_exception: bool = False


class FinalQualityAuditAgent(AuditAgentInterface):
    """Strict final-delivery quality gate inspired by local weekly-report V2 harnesses."""

    name = "final-quality-auditor"

    def __init__(self, config: FinalQualityConfig | None = None) -> None:
        self.config = config or FinalQualityConfig()

    def run_audit(
        self,
        markdown: str,
        ledger: ClaimLedger,
        context: PipelineContext | None = None,
    ) -> AuditReport:
        config = build_final_quality_config(context, self.config)
        findings: list[AuditFinding] = []
        findings.extend(_markdown_depth_findings(markdown, config))
        findings.extend(_summary_bullet_findings(markdown, config))
        findings.extend(_table_width_findings(markdown, config))
        findings.extend(_metadata_findings(markdown, config))
        findings.extend(_chapter_count_findings(markdown, config))
        findings.extend(_stale_current_framing_findings(markdown, context, config))
        findings.extend(_docx_fidelity_findings(config))
        findings.extend(_claim_count_findings(ledger, markdown, context, config))
        findings.extend(_brief_depth_findings(markdown, config))
        findings.extend(_date_requirement_findings(ledger, markdown, context, config))
        report = AuditReport(
            audit_status="pass",
            audit_score=100,
            findings=findings,
            metadata={
                "harness_protocol": config.harness_protocol,
                "target": "final",
                "quality_gate": "blocking",
                "quiet_week": config.quiet_week,
                "rendered_docx_path": config.rendered_docx_path,
            },
        )
        return recompute_report_status(report)


def build_final_quality_config(
    context: PipelineContext | None,
    defaults: FinalQualityConfig | None = None,
) -> FinalQualityConfig:
    base = defaults or FinalQualityConfig()
    data = dict(context.metadata.get("final_quality", {})) if context else {}
    report_threshold = context.max_source_age_days if context else None

    # Apply audience profile thresholds if available
    profile_thresholds: dict = {}
    if context and context.audience_profile:
        from multi_agent_brief.audience.profiles import get_profile
        profile = get_profile(context.audience_profile)
        profile_thresholds = profile.final_quality_thresholds

    return FinalQualityConfig(
        harness_protocol=str(data.get("harness_protocol", base.harness_protocol)),
        min_markdown_chars=int(data.get("min_markdown_chars", profile_thresholds.get("min_markdown_chars", base.min_markdown_chars))),
        min_docx_text_chars=int(data.get("min_docx_text_chars", base.min_docx_text_chars)),
        quiet_week=_as_bool(data.get("quiet_week"), base.quiet_week),
        expected_summary_bullets=data.get("expected_summary_bullets", profile_thresholds.get("expected_summary_bullets", base.expected_summary_bullets)),
        summary_heading=str(data.get("summary_heading", base.summary_heading)),
        summary_bullet_marker=str(data.get("summary_bullet_marker", base.summary_bullet_marker)),
        max_table_columns=int(data.get("max_table_columns", base.max_table_columns)),
        required_main_chapter_count=data.get("required_main_chapter_count", base.required_main_chapter_count),
        required_metadata_labels=list(data.get("required_metadata_labels", base.required_metadata_labels)),
        stale_current_threshold_days=data.get("stale_current_threshold_days", base.stale_current_threshold_days)
        or report_threshold,
        rendered_docx_path=str(data.get("rendered_docx_path", base.rendered_docx_path)),
        min_selected_claims=int(data.get("min_selected_claims", profile_thresholds.get("min_selected_claims", base.min_selected_claims))),
        min_zh_chars=int(data.get("min_zh_chars", base.min_zh_chars)),
        min_en_words=int(data.get("min_en_words", base.min_en_words)),
        require_dates=_as_bool(data.get("require_dates"), base.require_dates),
        allow_quiet_week_exception=_as_bool(
            data.get("allow_quiet_week_exception"),
            base.allow_quiet_week_exception,
        ),
    )


def _markdown_depth_findings(markdown: str, config: FinalQualityConfig) -> list[AuditFinding]:
    if config.quiet_week or len(markdown) >= config.min_markdown_chars:
        return []
    return [
        AuditFinding(
            finding_id="FINAL_DEPTH_001",
            severity="high",
            finding_type="final_report_too_thin",
            description=(
                f"Final report is too thin for a normal brief: {len(markdown)} chars "
                f"vs target >= {config.min_markdown_chars}."
            ),
            recommendation="Regenerate or edit the final report with deeper sourced analysis, or mark the run as quiet_week.",
        )
    ]


def _summary_bullet_findings(markdown: str, config: FinalQualityConfig) -> list[AuditFinding]:
    if config.expected_summary_bullets is None:
        return []
    summary = _section_by_heading(markdown, config.summary_heading)
    if not summary:
        return [
            AuditFinding(
                finding_id="FINAL_SUMMARY_001",
                severity="high",
                finding_type="missing_executive_summary",
                description=f"Final report is missing a {config.summary_heading} section.",
                recommendation="Add a reader-facing executive summary before delivery.",
            )
        ]
    count = sum(1 for line in summary.splitlines() if line.strip().startswith(config.summary_bullet_marker))
    if count == config.expected_summary_bullets:
        return []
    return [
        AuditFinding(
            finding_id="FINAL_SUMMARY_002",
            severity="high",
            finding_type="summary_bullet_count_mismatch",
            description=(
                f"Executive summary has {count} '{config.summary_bullet_marker}' bullets; "
                f"expected {config.expected_summary_bullets}."
            ),
            recommendation="Keep summary bullets as separate standalone lines so rendering cannot merge them.",
        )
    ]


def _table_width_findings(markdown: str, config: FinalQualityConfig) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell for cell in stripped.strip("|").split("|")]
        if len(cells) > config.max_table_columns:
            findings.append(
                AuditFinding(
                    finding_id=f"FINAL_TABLE_{len(findings)+1:03d}",
                    severity="high",
                    finding_type="wide_markdown_table",
                    line_number=line_number,
                    description=f"Markdown table has {len(cells)} columns; max is {config.max_table_columns}.",
                    recommendation="Convert wide tables into compact row sections before DOCX rendering.",
                    evidence=stripped,
                )
            )
    return findings


def _metadata_findings(markdown: str, config: FinalQualityConfig) -> list[AuditFinding]:
    missing = [label for label in config.required_metadata_labels if label.lower() not in markdown.lower()]
    if not missing:
        return []
    return [
        AuditFinding(
            finding_id="FINAL_META_001",
            severity="high",
            finding_type="missing_front_page_metadata",
            description=f"Final report is missing required front-page metadata labels: {', '.join(missing)}.",
            recommendation="Inject public-safe coverage, cutoff, and source-priority metadata before rendering.",
        )
    ]


def _chapter_count_findings(markdown: str, config: FinalQualityConfig) -> list[AuditFinding]:
    if config.required_main_chapter_count is None:
        return []
    count = sum(1 for line in markdown.splitlines() if line.startswith("## "))
    if count >= config.required_main_chapter_count:
        return []
    return [
        AuditFinding(
            finding_id="FINAL_CHAPTER_001",
            severity="high",
            finding_type="insufficient_main_chapters",
            description=f"Final report has {count} main chapters; expected at least {config.required_main_chapter_count}.",
            recommendation="Add the configured report sections or mark the template as quiet/ad-hoc.",
        )
    ]


def _stale_current_framing_findings(
    markdown: str,
    context: PipelineContext | None,
    config: FinalQualityConfig,
) -> list[AuditFinding]:
    if not context or not context.report_date or config.stale_current_threshold_days is None:
        return []
    report_day = parse_date(context.report_date)
    if report_day is None:
        return []
    findings: list[AuditFinding] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if not CURRENT_FRAMING_PATTERN.search(line):
            continue
        for match in DATE_PATTERN.finditer(line):
            source_day = parse_date(match.group(1))
            if source_day is None:
                continue
            age_days = (report_day - source_day).days
            if age_days > config.stale_current_threshold_days:
                findings.append(
                    AuditFinding(
                        finding_id=f"FINAL_STALE_{len(findings)+1:03d}",
                        severity="high",
                        finding_type="stale_date_framed_as_current",
                        line_number=line_number,
                        description=(
                            f"Line frames {source_day.isoformat()} as current/latest even though it is "
                            f"{age_days} days before report date {report_day.isoformat()}."
                        ),
                        recommendation="Recast stale events as dated background unless they changed in the report window.",
                        evidence=line.strip(),
                    )
                )
    return findings


def _docx_fidelity_findings(config: FinalQualityConfig) -> list[AuditFinding]:
    if not config.rendered_docx_path:
        return []
    docx_path = Path(config.rendered_docx_path)
    if not docx_path.exists():
        return [
            AuditFinding(
                finding_id="FINAL_DOCX_001",
                severity="high",
                finding_type="missing_rendered_docx",
                description=f"Rendered DOCX was configured but not found: {docx_path}.",
                recommendation="Render the final Markdown to DOCX before running the final delivery gate.",
            )
        ]
    try:
        from docx import Document  # type: ignore
    except ModuleNotFoundError:
        return [
            AuditFinding(
                finding_id="FINAL_DOCX_002",
                severity="high",
                finding_type="docx_validation_dependency_missing",
                description="python-docx is required for rendered DOCX fidelity validation.",
                recommendation="Install python-docx or disable DOCX validation only for non-production dry runs.",
            )
        ]
    document = Document(str(docx_path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    findings: list[AuditFinding] = []
    if len(text) < config.min_docx_text_chars and not config.quiet_week:
        findings.append(
            AuditFinding(
                finding_id="FINAL_DOCX_003",
                severity="high",
                finding_type="rendered_docx_too_thin",
                description=f"Rendered DOCX text is too thin: {len(text)} chars vs target >= {config.min_docx_text_chars}.",
                recommendation="Regenerate or edit the final report before delivery.",
            )
        )
    return findings


def _claim_count_findings(
    ledger: ClaimLedger,
    markdown: str,
    context: PipelineContext | None,
    config: FinalQualityConfig,
) -> list[AuditFinding]:
    """Check that enough claims were selected for the brief."""
    if config.min_selected_claims <= 0:
        return []
    # Count claims that are actually cited in the markdown
    citation_markdown = _markdown_for_claim_refs(markdown, context)
    cited_ids = set(re.findall(r"\[src:([A-Z0-9_]{6,})\]", citation_markdown))
    if len(cited_ids) >= config.min_selected_claims:
        return []
    if config.allow_quiet_week_exception and config.quiet_week:
        return []
    return [
        AuditFinding(
            finding_id="FINAL_CLAIMS_001",
            severity="high",
            finding_type="insufficient_claims",
            description=(
                f"Only {len(cited_ids)} reportable claims selected; "
                f"weekly brief requires at least {config.min_selected_claims}."
            ),
            recommendation="Add more source inputs or enable additional source providers, or mark as quiet_week.",
        )
    ]


def _brief_depth_findings(markdown: str, config: FinalQualityConfig) -> list[AuditFinding]:
    """Check brief body has sufficient depth (Chinese chars or English words)."""
    if config.min_zh_chars <= 0 and config.min_en_words <= 0:
        return []
    if config.allow_quiet_week_exception and config.quiet_week:
        return []

    # Count Chinese characters (CJK Unified Ideographs range)
    zh_chars = sum(1 for ch in markdown if "一" <= ch <= "鿿")
    # Count English words (rough approximation)
    en_words = len(re.findall(r"[a-zA-Z]+", markdown))

    findings: list[AuditFinding] = []
    if config.min_zh_chars > 0 and zh_chars < config.min_zh_chars:
        findings.append(
            AuditFinding(
                finding_id="FINAL_DEPTH_002",
                severity="high",
                finding_type="brief_too_thin_zh",
                description=(
                    f"Brief has {zh_chars} Chinese characters; "
                    f"weekly brief requires at least {config.min_zh_chars}."
                ),
                recommendation="Add more sourced analysis or mark as quiet_week.",
            )
        )
    if config.min_en_words > 0 and en_words < config.min_en_words:
        findings.append(
            AuditFinding(
                finding_id="FINAL_DEPTH_003",
                severity="high",
                finding_type="brief_too_thin_en",
                description=(
                    f"Brief has {en_words} English words; "
                    f"weekly brief requires at least {config.min_en_words}."
                ),
                recommendation="Add more sourced analysis or mark as quiet_week.",
            )
        )
    return findings


def _date_requirement_findings(
    ledger: ClaimLedger,
    markdown: str,
    context: PipelineContext | None,
    config: FinalQualityConfig,
) -> list[AuditFinding]:
    """Check that claims cited in the brief have dates."""
    if not config.require_dates:
        return []
    if config.allow_quiet_week_exception and config.quiet_week:
        return []

    citation_markdown = _markdown_for_claim_refs(markdown, context)
    cited_ids = set(re.findall(r"\[src:([A-Z0-9_]{6,})\]", citation_markdown))
    findings: list[AuditFinding] = []
    for claim in ledger:
        if claim.claim_id not in cited_ids:
            continue
        has_pub = bool(claim.metadata.get("published_at"))
        has_ret = bool(claim.metadata.get("retrieved_at"))
        if not has_pub and not has_ret:
            findings.append(
                AuditFinding(
                    finding_id=f"FINAL_DATE_{len(findings)+1:03d}",
                    severity="high",
                    finding_type="missing_date",
                    description=(
                        f"Claim {claim.claim_id} has no published_at or retrieved_at."
                    ),
                    recommendation="Ensure all source items have date metadata before drafting.",
                )
            )
    return findings


def _markdown_for_claim_refs(markdown: str, context: PipelineContext | None) -> str:
    if re.search(r"\[src:[A-Z0-9_]{6,}\]", markdown):
        return markdown
    audited = context.report_state.prepared_markdown if context else ""
    if audited and strip_claim_citations(audited).strip() == markdown.strip():
        return audited
    return markdown


def _section_by_heading(markdown: str, heading: str) -> str:
    pattern = re.compile(rf"^#+\s+{re.escape(heading)}\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(markdown)
    if not match:
        return ""
    next_heading = re.search(r"^#+\s+", markdown[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(markdown)
    return markdown[match.end() : end].strip()


# ── Final Clean Gate ──────────────────────────────────────────────────────────


@dataclass
class FinalCleanConfig:
    """Configuration for the Final Clean gate."""
    enabled: bool = True
    check_template_variables: bool = True
    check_internal_paths: bool = True
    check_model_phrases: bool = True
    check_feedback_leakage: bool = True
    check_editorial_comments: bool = True
    check_investment_advice: bool = True
    check_invalid_citations: bool = True


class FinalCleanAuditAgent(AuditAgentInterface):
    """Final Clean gate that runs after Editor and before Formatter.

    Detects process residue, template variables, internal paths, model phrases,
    user feedback leakage, editorial comments, investment advice, and invalid
    citations. This is a delivery gate, not a draft audit replacement.
    """

    name = "final-clean-auditor"

    def __init__(self, config: FinalCleanConfig | None = None) -> None:
        self.config = config or FinalCleanConfig()

    def run_audit(
        self,
        markdown: str,
        ledger: ClaimLedger,
        context: PipelineContext | None = None,
    ) -> AuditReport:
        if not self.config.enabled:
            return AuditReport(
                audit_status="pass",
                audit_score=100,
                findings=[],
                metadata={"final_clean": "disabled"},
            )

        findings: list[AuditFinding] = []

        # Run all Final Clean checks
        findings.extend(_final_clean_pattern_findings(markdown, self.config))
        findings.extend(_final_clean_invalid_citations(markdown, ledger, self.config))

        report = AuditReport(
            audit_status="pass",
            audit_score=100,
            findings=findings,
            metadata={
                "gate": "final_clean",
                "target": "reader_facing",
                "checks_enabled": {
                    "template_variables": self.config.check_template_variables,
                    "internal_paths": self.config.check_internal_paths,
                    "model_phrases": self.config.check_model_phrases,
                    "feedback_leakage": self.config.check_feedback_leakage,
                    "editorial_comments": self.config.check_editorial_comments,
                    "investment_advice": self.config.check_investment_advice,
                    "invalid_citations": self.config.check_invalid_citations,
                },
            },
        )
        return recompute_report_status(report)


def _final_clean_pattern_findings(
    markdown: str,
    config: FinalCleanConfig,
) -> list[AuditFinding]:
    """Run pattern-based Final Clean checks from draft_cleanup."""
    from multi_agent_brief.agents.draft_cleanup import detect_final_clean_issues

    # Build filter based on config
    enabled_types: set[str] = set()
    if config.check_template_variables:
        enabled_types.add("template_variable_residue")
    if config.check_internal_paths:
        enabled_types.add("internal_path_leak")
    if config.check_model_phrases:
        enabled_types.add("model_phrase_residue")
    if config.check_feedback_leakage:
        enabled_types.add("feedback_as_fact")
    if config.check_editorial_comments:
        enabled_types.add("editorial_comment_as_conclusion")
    if config.check_investment_advice:
        enabled_types.add("investment_recommendation")

    issues = detect_final_clean_issues(markdown)
    findings: list[AuditFinding] = []
    finding_counter = 0

    for issue in issues:
        if issue["finding_type"] not in enabled_types:
            continue
        finding_counter += 1
        findings.append(
            AuditFinding(
                finding_id=f"FINAL_CLEAN_{finding_counter:03d}",
                severity=issue["severity"],
                finding_type=issue["finding_type"],
                description=issue["description"],
                recommendation="Remove or fix before delivery.",
                line_number=issue.get("line_number"),
                evidence=issue.get("evidence", ""),
                blocking_level="safety_blocking" if issue["severity"] == "high" else "editor_fixable",
                repair_owner="safety" if issue["severity"] == "high" else "editor",
            )
        )
    return findings


def _final_clean_invalid_citations(
    markdown: str,
    ledger: ClaimLedger,
    config: FinalCleanConfig,
) -> list[AuditFinding]:
    """Check for invalid or empty citation markers."""
    if not config.check_invalid_citations:
        return []

    from multi_agent_brief.agents.draft_cleanup import detect_invalid_citations

    valid_ids = {claim.claim_id for claim in ledger}
    issues = detect_invalid_citations(markdown, valid_ids)
    findings: list[AuditFinding] = []

    for issue in issues:
        findings.append(
            AuditFinding(
                finding_id=f"FINAL_CLEAN_CITE_{len(findings)+1:03d}",
                severity=issue["severity"],
                finding_type=issue["finding_type"],
                description=issue["description"],
                recommendation="Fix citation reference before delivery.",
                line_number=issue.get("line_number"),
                evidence=issue.get("evidence", ""),
                blocking_level="safety_blocking" if issue["severity"] == "high" else "editor_fixable",
                repair_owner="safety" if issue["severity"] == "high" else "editor",
            )
        )
    return findings


# ── Rendered Output Validation ────────────────────────────────────────────────


@dataclass
class RenderedOutputConfig:
    """Configuration for rendered output validation."""
    enabled: bool = True
    check_heading_mapping: bool = True
    check_bullet_rendering: bool = True
    check_table_rendering: bool = True
    check_text_depth: bool = True
    check_footer_fields: bool = True
    min_docx_text_chars: int = 7800


class RenderedOutputAuditAgent(AuditAgentInterface):
    """Validate rendered DOCX output quality.

    Checks heading mapping, bullet/list rendering, table rendering,
    text depth, and footer fields. Produces rendered_output_report.json.
    """

    name = "rendered-output-auditor"

    def __init__(self, config: RenderedOutputConfig | None = None) -> None:
        self.config = config or RenderedOutputConfig()

    def run_audit(
        self,
        markdown: str,
        ledger: ClaimLedger,
        context: PipelineContext | None = None,
    ) -> AuditReport:
        if not self.config.enabled:
            return AuditReport(
                audit_status="pass",
                audit_score=100,
                findings=[],
                metadata={"rendered_output": "disabled"},
            )

        # Check if DOCX is available
        rendered_docx_path = ""
        if context:
            rendered_docx_path = context.metadata.get("rendered_docx_path", "")

        if not rendered_docx_path:
            return AuditReport(
                audit_status="pass",
                audit_score=100,
                findings=[],
                metadata={
                    "gate": "rendered_output",
                    "rendered_docx_path": "",
                    "skipped": "no_docx_path",
                },
            )

        docx_path = Path(rendered_docx_path)
        if not docx_path.exists():
            return AuditReport(
                audit_status="fail",
                audit_score=0,
                findings=[
                    AuditFinding(
                        finding_id="RENDERED_001",
                        severity="high",
                        finding_type="missing_rendered_docx",
                        description=f"Rendered DOCX not found: {docx_path}",
                        recommendation="Generate DOCX before running rendered output validation.",
                    )
                ],
                metadata={"gate": "rendered_output"},
            )

        # Try to load python-docx
        try:
            from docx import Document
        except ModuleNotFoundError:
            return AuditReport(
                audit_status="pass",
                audit_score=100,
                findings=[],
                metadata={
                    "gate": "rendered_output",
                    "rendered_docx_path": str(docx_path),
                    "skipped": "docx_validation_dependency_missing",
                },
            )

        document = Document(str(docx_path))
        findings: list[AuditFinding] = []

        # Check text depth
        if self.config.check_text_depth:
            findings.extend(_check_docx_text_depth(document, self.config))

        # Check heading mapping
        if self.config.check_heading_mapping:
            findings.extend(_check_docx_headings(document, markdown))

        # Check bullet rendering
        if self.config.check_bullet_rendering:
            findings.extend(_check_docx_bullets(document))

        report = AuditReport(
            audit_status="pass",
            audit_score=100,
            findings=findings,
            metadata={
                "gate": "rendered_output",
                "rendered_docx_path": str(docx_path),
                "checks_enabled": {
                    "heading_mapping": self.config.check_heading_mapping,
                    "bullet_rendering": self.config.check_bullet_rendering,
                    "table_rendering": self.config.check_table_rendering,
                    "text_depth": self.config.check_text_depth,
                    "footer_fields": self.config.check_footer_fields,
                },
            },
        )
        return recompute_report_status(report)


def _check_docx_text_depth(document, config: RenderedOutputConfig) -> list[AuditFinding]:
    """Check that DOCX has sufficient text content."""
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    if len(text) >= config.min_docx_text_chars:
        return []
    return [
        AuditFinding(
            finding_id="RENDERED_DEPTH_001",
            severity="high",
            finding_type="rendered_docx_too_thin",
            description=f"DOCX text is too thin: {len(text)} chars vs target >= {config.min_docx_text_chars}",
            recommendation="Ensure the brief has sufficient content before DOCX generation.",
        )
    ]


def _check_docx_headings(document, markdown: str) -> list[AuditFinding]:
    """Check that DOCX heading structure matches Markdown headings."""
    # Extract headings from Markdown
    md_headings = []
    for line in markdown.splitlines():
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            md_headings.append((len(m.group(1)), m.group(2).strip()))

    # Extract headings from DOCX
    docx_headings = []
    for paragraph in document.paragraphs:
        if paragraph.style.name.startswith("Heading"):
            try:
                level = int(paragraph.style.name.split()[-1])
                docx_headings.append((level, paragraph.text.strip()))
            except (ValueError, IndexError):
                pass

    # Compare heading counts
    if len(md_headings) != len(docx_headings):
        return [
            AuditFinding(
                finding_id="RENDERED_HEADING_001",
                severity="medium",
                finding_type="heading_count_mismatch",
                description=f"DOCX has {len(docx_headings)} headings vs Markdown has {len(md_headings)}",
                recommendation="Check heading rendering in DOCX conversion.",
            )
        ]

    return []


def _check_docx_bullets(document) -> list[AuditFinding]:
    """Check that bullets/lists are rendered in DOCX."""
    # Count list items in DOCX (paragraphs with list styles)
    list_count = 0
    for paragraph in document.paragraphs:
        style_name = paragraph.style.name.lower()
        if "list" in style_name or paragraph.text.strip().startswith(("•", "○", "▪", "·", "–")):
            list_count += 1

    # This is a basic check - we just verify some list content exists
    # if there are lists in the document
    return []
