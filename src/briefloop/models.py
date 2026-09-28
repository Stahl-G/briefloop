"""Small input contracts; report quality is assessed by agents, not these schemas."""
from typing import Literal, get_args
from datetime import date
from .industry_data import IndustryData
from pydantic import BaseModel, Field, ConfigDict, ValidationError, model_validator, field_validator


class Model(BaseModel):
    # extra="forbid" is right for request bodies: the caller is still there and can
    # retry. It is wrong for an artifact an agent hands over at the end of a long
    # run, where the same strictness costs the whole run. Read those through
    # prune_unknown/describe_invalid instead of validating the raw file.
    model_config = ConfigDict(extra="forbid")


LENGTH_PRESETS = {'quick':(350,500),'compact':(800,1000),'balanced':(1500,2000),'detailed':(2000,2500)}
# English counts one unit per word, so the same reading length needs fewer
# units than Chinese characters (about 0.65 words per character).
LENGTH_PRESETS_EN = {'quick':(250,350),'compact':(500,650),'balanced':(1000,1300),'detailed':(1300,1600)}
DEEP_LENGTH = {'zh':(10000,12000),'en':(6500,8000)}
INDUSTRY_LENGTH = {'zh':(5000,5500),'en':(3200,3600)}


def report_language(value):
    """Map a saved or typed report language to 'zh'/'en'; None when unrecognized.

    Runs saved before the enum stored free text such as "中文" or "English";
    readers of raw requirements JSON call this instead of comparing strings.
    """
    if value is None:
        return 'zh'
    text = str(value).strip().lower().replace('_', '-')
    if text in ('', 'zh', '中文', '汉语', '简体中文', '简体', 'chinese', 'simplified chinese') or text.startswith('zh-'):
        return 'zh'
    if text in ('en', 'english', '英文', '英语') or text.startswith('en-') or text.startswith('english'):
        return 'en'
    return None


def length_presets(language='zh'):
    return LENGTH_PRESETS_EN if report_language(language) == 'en' else LENGTH_PRESETS


RESEARCH_BUDGET_PRESETS = {
    'weekly':{'search_requests':30,'candidate_urls':150,'source_pages':60},
    'monthly':{'search_requests':80,'candidate_urls':400,'source_pages':150},
}


class ResearchBudget(Model):
    search_requests: int = Field(default=30, ge=0)
    candidate_urls: int = Field(default=150, ge=0)
    source_pages: int = Field(default=60, ge=0)


class SearchPolicy(Model):
    primary_provider: Literal['native','tavily','duckduckgo','bocha','zhipu'] = 'native'
    supplemental_providers: list[Literal['tavily','duckduckgo','bocha','zhipu']] = Field(default_factory=list)
    zhipu_engine: Literal['search_std','search_pro','search_pro_sogou','search_pro_quark'] = 'search_std'
    native_search_enabled: bool = False
    coverage_mode: Literal['primary_only','on_gap','coverage'] = 'coverage'
    market_scope: str = Field(default='', max_length=500)
    platform_scope: list[Literal['wechat','xiaohongshu']] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_channels(self):
        self.supplemental_providers=list(dict.fromkeys(p for p in self.supplemental_providers if p!=self.primary_provider))
        return self


class ReportSection(Model):
    section_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    mode: Literal['required','optional','manual'] = 'required'
    purpose: str = ''
    placeholder: str = '待填充'


class LengthRequirement(Model):
    """Recorded source of an explicit limit, not a separate authorization grant."""
    kind: Literal['user_selection', 'user_quote'] = Field(description='user_selection 由用户明确选择网页严格选项时记录；Agent 整理用户口述只用 user_quote，不虚构界面点击。')
    text: str = Field(min_length=1, max_length=1000, description='user_quote 为 objective、raw_input 或 writing_preferences 中连续逐字原话；来源记录本身不证明语义或授权。')


class Requirements(Model):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=10000)
    report_profile: Literal["brief", "industry_periodic"] = "brief"
    workflow_id: str | None = Field(default=None, max_length=80)
    workflow_variant: str | None = Field(default=None, max_length=80)
    # Store replaces caller-provided snapshots when creating a run. Consumers of
    # saved runs keep the actual method content even after an application update.
    workflow_snapshot: dict | None = None
    report_date: str = ""
    organization: str = ""
    industry: str = ""
    reference_source_ids: list[str] = Field(default_factory=list)
    template_id: str | None = None
    writing_mode: Literal['general','internal_report'] = 'general'
    sections: list[ReportSection] = Field(default_factory=list)
    manual_sections: list[str] = Field(default_factory=list)
    key_questions: list[str] = Field(default_factory=list)
    writing_preferences: list[str] = Field(default_factory=list)
    company_context_revision: str | None = None
    company_context_required: bool = False
    audience: str = "自己"
    # Report body language. The interface, internal records and review notes
    # stay Chinese; only the report text and its length presets follow this.
    language: Literal["zh", "en"] = "zh"
    extent: Literal["quick", "compact", "balanced", "detailed"] = "balanced"
    allow_web: bool = True
    search_policy: SearchPolicy | None = None
    period: str = ""
    period_start: str = ""
    period_end: str = ""
    report_timezone: str = ""
    time_context: dict | None = None
    target_minutes: int | None = Field(default=None, ge=0, le=240)
    hard_timeout_minutes: int | None = Field(default=None, ge=0, le=1440)
    raw_input: str = ""
    # Research tier chosen at task creation; stored on the run so pause/resume and
    # a later plan freeze read the same choice. research_plan.PRESETS is the value.
    research_tier: Literal["quick", "standard", "deep"] = "standard"
    research_budget: ResearchBudget = Field(default_factory=ResearchBudget)
    # Independent fact-check switch chosen at task creation; None follows the
    # workspace default, which create_run resolves to a concrete bool on the run
    # so pause/resume and later phases read one stored choice.
    fact_check: bool | None = None
    target_words: int | None = Field(default=None, ge=1)
    max_words: int | None = Field(default=None, ge=1)
    length_mode: Literal['soft', 'strict'] = Field(default='soft', description='默认篇幅建议；仅用户明确选择或要求严格上限时用 strict，并提供 max_words 和 length_requirement。')
    length_requirement: LengthRequirement | None = None

    @model_validator(mode='before')
    @classmethod
    def workflow_profile_compatibility(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for key in ('workflow_id', 'workflow_variant'):
            if value.get(key) == '':
                value[key] = None
        if value.get('workflow_id'):
            value['report_profile'] = ('industry_periodic' if value['workflow_id'] == 'business_report'
                                       and value.get('workflow_variant') == 'industry_periodic' else 'brief')
        return value

    @field_validator('language', mode='before')
    @classmethod
    def known_language(cls, value):
        language = report_language(value)
        if language is None:
            raise ValueError('报告语言只支持中文（zh）或英文（en）')
        return language

    @field_validator('report_date')
    @classmethod
    def valid_report_date(cls, value):
        if value and (len(value)!=10 or date.fromisoformat(value).isoformat()!=value):
            raise ValueError('报告日期应为 YYYY-MM-DD')
        return value

    @model_validator(mode='after')
    def fill_length_preferences(self):
        if self.length_mode == 'strict':
            if self.max_words is None or not self.length_requirement or not self.length_requirement.text.strip():
                raise ValueError('严格篇幅需要明确上限和用户要求来源；仅有旧 max_words 不构成严格限制')
            if self.length_requirement.kind == 'user_quote':
                originals = [self.objective, self.raw_input, *self.writing_preferences]
                if not any(self.length_requirement.text in original for original in originals):
                    raise ValueError('严格篇幅的 user_quote 必须逐字出现在 objective、raw_input 或 writing_preferences 中')
        target,maximum=(DEEP_LENGTH[self.language] if self.research_tier=="deep" else INDUSTRY_LENGTH[self.language]
                        if self.report_profile=="industry_periodic" else length_presets(self.language)[self.extent])
        if self.length_mode == 'strict':target=min(target,self.max_words)
        if self.target_words is None:self.target_words=target
        if self.max_words is None:self.max_words=max(maximum,self.target_words)
        if self.max_words<self.target_words:
            raise ValueError('长度上限不能小于目标长度')
        return self


ROLE_NAMES = ('evaluator', 'maintainer', 'proposer')


def normalize_search_provider(value):
    # 'codex' was the original name for backend-native search; it now reads 'native'.
    if value in (None, '', 'codex'):
        return 'native'
    if value not in ('native', 'tavily', 'duckduckgo', 'bocha', 'zhipu'):
        raise ValueError('无效搜索来源')
    return value


def normalize_role_models(roles):
    # Settings/new-job projection only; never rewrite frozen historical jobs.
    if not isinstance(roles,dict):
        return roles
    result=dict(roles)
    if 'evaluator' not in result:
        for previous in ('scorer','assessor'):
            if previous in result:
                result['evaluator']=result[previous]
                break
    result.pop('scorer',None)
    result.pop('assessor',None)
    return result


class RoleModel(Model):
    service_tier: Literal['fast', 'default'] | None = None
    model: str = Field(min_length=1, max_length=100)
    model_provider: str | None = Field(default=None, max_length=100)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=100)
    model_variant: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator('model_provider', 'reasoning_effort', 'model_variant', mode='before')
    @classmethod
    def optional_override(cls, value, info):
        if isinstance(value, str):
            value = value.strip()
            if not value or info.field_name == 'reasoning_effort' and value.lower() == 'none':
                return None
        return value


def runtime_fields(value, backend='codex'):
    if backend not in ('codex','opencode','briefloop-native'):
        # A legacy workspace's Codex default must not silently become a bridge
        # override. Settings store bridge choices per host; frozen runs/roles
        # carry the selected effort directly.
        effort = value.get('runtime_efforts', {}).get(backend) if 'runtime_efforts' in value else value.get('reasoning_effort', value.get('effort'))
        selected = RoleModel.model_validate({'model': value['model'], 'reasoning_effort': effort})
        return {'model': selected.model, **({'reasoning_effort': selected.reasoning_effort} if selected.reasoning_effort is not None else {})}
    if backend in ('opencode','briefloop-native'):
        # Opencode models are provider/model in one string; effort is expressed
        # as an optional variant. Codex-only keys are dropped, never sent.
        from .backends.opencode_server import split_model
        validated = RoleModel.model_validate(
            {key: value[key] for key in ('model', 'model_variant') if key in value})
        split_model(validated.model)
        selected = {'model': validated.model}
        if validated.model_variant is not None:
            selected['model_variant'] = validated.model_variant
        return selected
    # Provider names/model IDs are opaque Codex configuration, not a model catalog.
    selected = RoleModel.model_validate(
        {key: value[key] for key in ('model', 'reasoning_effort', 'model_provider', 'service_tier') if key in value}).model_dump()
    if selected['model_provider'] is None:
        selected.pop('model_provider')
    selected.pop('model_variant', None)
    if selected.get('service_tier') is None:
        selected.pop('service_tier', None)
    return selected


class ReviewRuntime(RoleModel):
    """The backend and model the independent Reviewer runs on, chosen apart
    from the main chain. Mode-specific capability admission decides whether
    this backend can provide standard or strict review."""
    backend: Literal['codex', 'opencode', 'briefloop-native']
    model: str = Field(min_length=1, max_length=100)
    model_variant: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator('model', 'model_variant', mode='before')
    @classmethod
    def strip(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode='after')
    def model_shape(self):
        runtime_fields(self.model_dump(exclude_none=True), self.backend)
        return self


class Settings(RoleModel):
    model: str = Field(default='gpt-5.6-luna', max_length=100)
    reasoning_effort: str | None = Field(default='high', min_length=1, max_length=100)
    agent_backend: Literal['codex', 'opencode','briefloop-native','claude','kimi','hermes','reasonix','mimo','codebuddy','kilo','kiro','vibe','deepseek-harness','antigravity','pi','zcode'] = 'codex'
    runtime_efforts: dict[str, str | None] = Field(default_factory=dict)

    @field_validator('runtime_efforts')
    @classmethod
    def normalize_runtime_efforts(cls, values):
        from .backends import validate_backend
        return {validate_backend(backend): RoleModel(model='default', reasoning_effort=effort).reasoning_effort
                for backend, effort in values.items()}
    model_selection_required: bool = True
    role_models: dict[Literal['evaluator','maintainer','proposer'], RoleModel] = Field(default_factory=dict)
    # None: the Reviewer follows agent_backend and the Evaluator model.
    review_runtime: ReviewRuntime | None = None
    review_mode: Literal['standard', 'strict'] = 'standard'
    chat_allow_web: bool = True
    search_provider: Literal['native','tavily','duckduckgo','bocha','zhipu'] = 'tavily'
    search_policy: SearchPolicy | None = None
    k: int = Field(default=1, ge=1, le=20)
    # Saving feedback is free; automatic learning starts paid validation and needs
    # a recorded confirmation of its upper bound (learning_budget, #727).
    auto_learn: bool = False
    auto_learn_authorized_rounds: int | None = Field(default=None, ge=1, le=20)
    auto_learn_authorized_plan: str | None = Field(default=None, min_length=64, max_length=64)
    max_reports: int = Field(default=4, ge=1, le=16)
    max_parallel: int = Field(default=4, ge=1, le=16)
    # Legacy settings key now means a soft planning target, never a deadline.
    timeout_minutes: int = Field(default=60, ge=0, le=240)
    hard_timeout_minutes: int = Field(default=0, ge=0, le=1440)
    skill_targets: list[str] = Field(default_factory=lambda: ["scout", "analyst"])
    auto_revision: bool = True
    default_template_id: str | None = None
    company_context_enabled: bool | None = None
    # Workspace-wide default for the per-task fact_check switch; tasks may override.
    fact_checker: bool = False
    # Workspace-wide optional local file quality checks via officecli; the
    # switch has no effect while the binary is not installed.
    officecli_enabled: bool = False
    research_tier: Literal['quick','standard','deep'] = 'standard'


    @model_validator(mode='after')
    def selected_model_required(self):
        if not self.model.strip() and not self.model_selection_required:raise ValueError('请选择模型')
        return self

    @model_validator(mode='before')
    @classmethod
    def migrate_evaluator_setting(cls, value):
        if isinstance(value,dict):
            value=dict(value)
            if 'role_models' in value:
                value['role_models']=normalize_role_models(value['role_models'])
            if value.get('search_provider','native') not in ('native','tavily','duckduckgo','bocha','zhipu'):
                value['search_provider']=normalize_search_provider(value.get('search_provider'))
        return value


class Citation(Model):
    source_id: str
    locator: str = ""
    excerpt: str = ""


class NumberBinding(Model):
    """One important number traced to its origin; the program converts units.

    value/unit describe the original. Exact source and body spans prevent
    matching a different fact elsewhere. Legacy bindings remain readable but
    are marked unchecked until these spans are supplied.
    """
    label: str = ""
    value: float | None = None
    unit: str = ""
    period: str = ""
    entity: str = ""
    source_id: str = ""
    locator: str = ""

    report_quote: str = ""
    number_text: str = ""
    source_excerpt: str = ""


class GapRecord(Model):
    """One delivery-affecting gap. Tool failures stay in execution records instead."""
    related: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    action: str = ""
    status: Literal["open", "addressed", "review_needed", "resolved", "unresolved"] = "open"


class TemporalClaim(Model):
    statement: str
    event_date: str = ""
    published_at: str = ""
    fetched_at: str = ""
    source_id: str = ""
    locator: str = ""
    usage: Literal["current", "background"] = "current"
    news_basis: Literal["event", "first_disclosure", "new_development"] = "event"
    news_date: str = ""
    news_note: str = ""


class BriefDraft(Model):
    temporal_claims: list[TemporalClaim] = Field(default_factory=list)
    figures: list[str] = Field(default_factory=list)
    report_data: IndustryData | None = None
    title: str
    markdown: str = ''
    editor_document: dict | None = None
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    gap_records: list[GapRecord] = Field(default_factory=list)
    number_bindings: list[NumberBinding] = Field(default_factory=list)
    research_notes: list[dict] = Field(default_factory=list)
    reader_contract: dict | None = None
    reconciliation_id: str | None = None

    @model_validator(mode='after')
    def normalize_content(self):
        if self.editor_document is not None:
            from .document_model import normalize_document, document_markdown
            self.editor_document = normalize_document(self.editor_document)
            self.markdown = document_markdown(self.editor_document)
        if not self.markdown.strip():raise ValueError('报告正文不能为空')
        return self


class Finding(Model):
    dimension: Literal["evidence", "coverage", "analysis", "expression"]
    severity: Literal["minor", "major"]
    description: str
    report_quote: str = ""
    requirement: str = ""
    source_id: str | None = None
    locator: str = ""
    evidence: str = ""
    suggestion: str = ""
    # Optional links to this assessment's checks; findings describe remaining issues.
    check_ids: list[str] = Field(default_factory=list)
    # Tolerated drift: scoring findings occasionally reuse review-finding keys.
    kind: str | None = None
    block_ids: list[str] = Field(default_factory=list)


class Assessment(Model):
    brief_hash: str
    status: Literal["complete", "incomplete"] = "complete"
    summary: str
    overall: Literal["达到要求", "建议修改", "存在重大问题", "评估未完成"]
    evidence: int | None = Field(default=None, ge=1, le=5)
    coverage: int | None = Field(default=None, ge=1, le=5)
    analysis: int | None = Field(default=None, ge=1, le=5)
    expression: int | None = Field(default=None, ge=1, le=5)
    checks: list[dict] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_has_scores(self):
        if self.status == "complete" and any(getattr(self, x) is None for x in ("evidence", "coverage", "analysis", "expression")):
            raise ValueError("Complete assessment needs four grades; unfinished checks are not a zero score")
        self.checks = assessment_checks(self.checks, self.findings)
        return self


def assessment_checks(checks, findings, expected=()):
    """Record explicit check/finding relationships, never infer from prose.

    Old checks remain readable. A missing new check is observable, not another
    generation retry or a fabricated failure; aggregate scores stay the agent's.
    """
    result = [dict(check) for check in checks]
    for item in expected:
        matches = [check for check in result if check.get('id') == item['id']]
        if not matches:
            result.append({**item, 'status': 'not_checked',
                           'reason': '本次评价未返回这一项的复核结果。'})
        elif len(matches) > 1:
            for check in matches:
                check.setdefault('model_status', check.get('status'))
                check['status'] = 'not_checked'
                check['consistency_note'] = '这一项返回了重复结果，不能判定复核完成。'
        else:
            check = matches[0]
            # Labels and prior-assessment identity are supplied by the runner.
            check.update({key: value for key, value in item.items() if key != 'id'})
            if check.get('status') not in ('passed', 'needs_attention', 'not_checked', 'disputed') or not str(check.get('reason') or '').strip():
                check.setdefault('model_status', check.get('status'))
                check['status'] = 'not_checked'
                check['consistency_note'] = '缺少有效状态或具体复核依据，保留为未核对。'
    linked = {}
    for index, finding in enumerate(findings):
        data = finding if isinstance(finding, dict) else finding.model_dump()
        for identity in data.get('check_ids', []):
            linked.setdefault(identity, []).append(index)
    for check in result:
        identity = check.get('id')
        indices = linked.get(identity) if isinstance(identity, str) else None
        if indices:
            check['finding_indices'] = indices
            if check.get('status') == 'passed':
                check['model_status'] = 'passed'
                check['status'] = 'needs_attention'
                check['consistency_note'] = '评价仍列出与此项关联的问题，不能同时显示为全部通过；不改变总评或问题严重程度。'
    return result


# Expression anchor 2 means a reader must do real editing before the body is usable
# (repeated noise, internal checklists, mechanical labels). At or below it the body is
# "must fix": the existing single revision is triggered even when the overall verdict
# would otherwise look passing.
MUST_FIX_EXPRESSION = 2


def missing_findings(assessment) -> str | None:
    """A verdict that asks for changes must say what to change, one finding per
    problem; problems named only in the summary give revision nothing to act on.
    Enforced where the host can re-ask in the same run (the native engine's
    submit); stated in the shared contract for every host."""
    data = assessment if isinstance(assessment, dict) else assessment.model_dump()
    if data.get('status', 'complete') == 'complete' and data.get('overall') in ('建议修改', '存在重大问题') and not data.get('findings'):
        return f"结论为「{data['overall']}」时，每个需要修改的问题都要写成 findings（report_quote、依据、来源定位）；摘要中提到的问题也要逐条写入，摘要不能代替 findings。"
    return None


def must_fix(assessment) -> bool:
    data = assessment if isinstance(assessment, dict) else assessment.model_dump()
    if data.get('status') != 'complete':
        return False
    score = data.get('expression')
    return isinstance(score, int) and score <= MUST_FIX_EXPRESSION


def overall_inconsistent(assessment) -> bool:
    """A must-fix body cannot be summarised as '达到要求'; flag the self-contradiction
    instead of silently letting a low expression score pass as complete."""
    data = assessment if isinstance(assessment, dict) else assessment.model_dump()
    return data.get('overall') == '达到要求' and must_fix(data)


class SaveRevision(Model):
    base_version: str
    markdown: str = ''
    editor_document: dict | None = None
    allow_markdown_conversion: bool = Field(default=False, strict=True)


class Comment(Model):
    learning_intent: Literal['feedback','explicit_requirement'] = 'feedback'
    version_id: str
    text: str = Field(min_length=1, max_length=20000)


class ScoutEvidence(Model):
    source_id: str
    locator: str = ""
    excerpt: str = ""
    facts: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    coverage_status: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)


class ResearchGapEvidence(Model):
    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    source_hash: str | None = None


class ResearchGapUpdate(Model):
    gap_id: str = Field(min_length=1)
    status: Literal['open', 'partial', 'resolved']
    reason: str = Field(min_length=1)
    remaining_question: str = ''
    evidence: list[ResearchGapEvidence] = Field(default_factory=list)

    @model_validator(mode='after')
    def explicit_change(self):
        if not self.reason.strip():
            raise ValueError('缺口状态更新需要具体 reason')
        if self.status != 'open' and not self.evidence:
            raise ValueError('partial/resolved 必须至少给一条 evidence')
        if self.status == 'partial' and not self.remaining_question.strip():
            raise ValueError('partial 必须写 remaining_question，说明仍需核对什么')
        if self.status != 'partial' and self.remaining_question:
            raise ValueError('remaining_question 仅用于 partial')
        return self


class ResearchGap(Model):
    gap_id: str
    description: str
    status: Literal['open', 'partial', 'resolved'] = 'open'
    reason: str = ''
    remaining_question: str = ''
    evidence: list[ResearchGapEvidence] = Field(default_factory=list)
    round_id: str | None = None
    round_index: int | None = None
    validation_error: str | None = None


class ScoutResult(Model):
    sources: list[ScoutEvidence]
    gaps: list[str] = Field(default_factory=list)
    gap_records: list[ResearchGap] = Field(default_factory=list)
    gap_history: list[ResearchGap] = Field(default_factory=list)
    search_summary: str = ""
    retrieval_notes: list[dict] = Field(default_factory=list)


def _contract_model(annotation):
    """The single contract model a field holds, or None for free-form values."""
    found = []
    stack = [annotation]
    while stack:
        current = stack.pop()
        arguments = get_args(current)
        if arguments:
            stack.extend(arguments)
        elif isinstance(current, type) and issubclass(current, BaseModel):
            found.append(current)
    return found[0] if len(found) == 1 else None


def prune_unknown(value, model, path=''):
    """Drop keys the contract does not define and report each dropped path.

    A key an agent invented or misspelled must not cost a finished run. Dropping
    it is recorded, never silent, and free-form fields (editor_document,
    research_notes, reader_contract) keep everything they were given.
    """
    if not isinstance(value, dict):
        return value, []
    dropped = []
    result = {}
    for key, item in value.items():
        if key not in model.model_fields:
            dropped.append(f'{path}.{key}'.lstrip('.'))
            continue
        nested = _contract_model(model.model_fields[key].annotation)
        if nested is not None:
            if isinstance(item, list):
                entries = []
                for index, entry in enumerate(item):
                    # Same path notation Pydantic uses, so dropped keys and validation
                    # errors read the same way in one report.
                    cleaned, lost = prune_unknown(entry, nested, f'{path}.{key}.{index}'.lstrip('.'))
                    entries.append(cleaned)
                    dropped.extend(lost)
                item = entries
            else:
                item, lost = prune_unknown(item, nested, f'{path}.{key}'.lstrip('.'))
                dropped.extend(lost)
        result[key] = item
    return result, dropped


def describe_invalid(error):
    """Name the offending fields instead of handing a raw Pydantic dump to the user."""
    parts = []
    for detail in error.errors():
        location = '.'.join(str(x) for x in detail['loc']) or '(顶层)'
        parts.append(f"{location}：{detail['msg']}")
    return '；'.join(dict.fromkeys(parts))


def check_artifact(value, model):
    """Self-check an agent artifact before the host reads it, in the host's own terms."""
    pruned, dropped = prune_unknown(value, model)
    report = {'status': 'ok', 'unknown_fields': dropped, 'errors': []}
    if dropped:
        report['note'] = '这些键不在契约内，发布时会被丢弃并记入任务日志；若其中有必需内容，请改放到契约字段。'
    try:
        model.model_validate(pruned)
    except ValidationError as exc:
        report['status'] = 'invalid'
        report['errors'] = [{'field': '.'.join(str(x) for x in detail['loc']), 'message': detail['msg']}
                            for detail in exc.errors()]
        report['message'] = describe_invalid(exc)
    return report
