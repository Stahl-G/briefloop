"""Small input contracts; report quality is assessed by agents, not these schemas."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirements(Model):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=10000)
    audience: str = "自己"
    language: str = "中文"
    extent: Literal["compact", "balanced", "detailed"] = "balanced"
    allow_web: bool = False
    period: str = ""
    raw_input: str = ""


ROLE_NAMES = ('evaluator', 'maintainer', 'proposer')


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
    reasoning_effort: Literal['low','medium','high','xhigh','max']


class Settings(Model):
    model: str = Field(default='gpt-5.6-luna', min_length=1, max_length=100)
    reasoning_effort: Literal['low','medium','high','xhigh','max'] = 'high'
    role_models: dict[Literal['evaluator','maintainer','proposer'], RoleModel] = Field(default_factory=dict)
    search_provider: Literal['codex','tavily'] = 'codex'
    k: int = Field(default=1, ge=1, le=20)
    auto_learn: bool = True
    max_parallel: int = Field(default=4, ge=1, le=16)
    timeout_minutes: int = Field(default=30, ge=1, le=240)
    skill_targets: list[str] = Field(default_factory=lambda: ["scout", "analyst"])


    @model_validator(mode='before')
    @classmethod
    def migrate_evaluator_setting(cls, value):
        if isinstance(value,dict) and 'role_models' in value:
            return {**value,'role_models':normalize_role_models(value['role_models'])}
        return value


class Citation(Model):
    source_id: str
    locator: str = ""
    excerpt: str = ""


class BriefDraft(Model):
    title: str
    markdown: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


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
    markdown: str = Field(min_length=1)
    editor_document: dict | None = None


class Comment(Model):
    version_id: str
    text: str = Field(min_length=1, max_length=20000)


class Comparison(Model):
    verdict: Literal["better", "tie", "worse"]
    reason: str
    regressions: list[str] = Field(default_factory=list)
    cases: list[dict] = Field(default_factory=list)


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
