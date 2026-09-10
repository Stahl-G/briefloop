"""Small input contracts; report quality is assessed by agents, not these schemas."""
from typing import Literal
from datetime import date
from .industry_data import IndustryData
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


LENGTH_PRESETS = {'compact':(800,1000),'balanced':(1500,2000),'detailed':(2000,2500)}


RESEARCH_BUDGET_PRESETS = {
    'weekly':{'search_requests':12,'candidate_urls':60,'source_pages':18},
    'monthly':{'search_requests':30,'candidate_urls':200,'source_pages':45},
}


class ResearchBudget(Model):
    search_requests: int = Field(default=12, ge=0)
    candidate_urls: int = Field(default=60, ge=0)
    source_pages: int = Field(default=18, ge=0)


class ReportSection(Model):
    section_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    mode: Literal['required','optional','manual'] = 'required'
    purpose: str = ''
    placeholder: str = '待填充'


class Requirements(Model):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=10000)
    report_profile: Literal["brief", "industry_periodic"] = "brief"
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
    language: str = "中文"
    extent: Literal["compact", "balanced", "detailed"] = "balanced"
    allow_web: bool = False
    period: str = ""
    raw_input: str = ""
    research_budget: ResearchBudget = Field(default_factory=ResearchBudget)
    target_words: int | None = Field(default=None, ge=1)
    max_words: int | None = Field(default=None, ge=1)

    @field_validator('report_date')
    @classmethod
    def valid_report_date(cls, value):
        if value and (len(value)!=10 or date.fromisoformat(value).isoformat()!=value):
            raise ValueError('报告日期应为 YYYY-MM-DD')
        return value

    @model_validator(mode='after')
    def fill_length_preferences(self):
        target,maximum=(5000,5500) if self.report_profile=="industry_periodic" else LENGTH_PRESETS[self.extent]
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
    if value not in ('native', 'tavily'):
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
    if backend == 'opencode':
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
        {key: value[key] for key in ('model', 'reasoning_effort', 'model_provider') if key in value}).model_dump()
    if selected['model_provider'] is None:
        selected.pop('model_provider')
    selected.pop('model_variant', None)
    return selected


class Settings(RoleModel):
    model: str = Field(default='gpt-5.6-luna', max_length=100)
    reasoning_effort: str | None = Field(default='high', min_length=1, max_length=100)
    agent_backend: Literal['codex', 'opencode'] = 'codex'
    model_selection_required: bool = False
    role_models: dict[Literal['evaluator','maintainer','proposer'], RoleModel] = Field(default_factory=dict)
    search_provider: Literal['native','tavily'] = 'native'
    k: int = Field(default=1, ge=1, le=20)
    auto_learn: bool = True
    max_parallel: int = Field(default=4, ge=1, le=16)
    timeout_minutes: int = Field(default=30, ge=1, le=240)
    skill_targets: list[str] = Field(default_factory=lambda: ["scout", "analyst"])
    auto_revision: bool = True
    default_template_id: str | None = None
    company_context_enabled: bool | None = None


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
            if value.get('search_provider','native') not in ('native','tavily'):
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


class BriefDraft(Model):
    figures: list[str] = Field(default_factory=list)
    report_data: IndustryData | None = None
    title: str
    markdown: str = ''
    editor_document: dict | None = None
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    number_bindings: list[NumberBinding] = Field(default_factory=list)
    research_notes: list[dict] = Field(default_factory=list)
    reader_contract: dict | None = None

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
        return self


class SaveRevision(Model):
    base_version: str
    markdown: str = ''
    editor_document: dict | None = None


class Comment(Model):
    version_id: str
    text: str = Field(min_length=1, max_length=20000)


class ScoutEvidence(Model):
    source_id: str
    locator: str = ""
    excerpt: str = ""
    facts: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    coverage_status: str = Field(min_length=1)


class ScoutResult(Model):
    sources: list[ScoutEvidence]
    gaps: list[str] = Field(default_factory=list)
