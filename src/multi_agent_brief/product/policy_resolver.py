"""Deterministic PolicyProfile resolver for zero-config workspace setup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class PolicyProfileResolution:
    policy_profile: str
    source: str
    input: str
    matched_rule: str
    confidence: str
    alternatives: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_profile": self.policy_profile,
            "source": self.source,
            "input": self.input,
            "matched_rule": self.matched_rule,
            "confidence": self.confidence,
            "alternatives": list(self.alternatives),
        }


_PROFILE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "solar_manufacturing_default",
        "solar_manufacturing_keywords",
        (
            "solar",
            "pv",
            "photovoltaic",
            "module",
            "wafer",
            "polysilicon",
            "光伏",
            "太阳能",
            "组件",
            "硅片",
            "多晶硅",
        ),
    ),
    (
        "manufacturing_default",
        "manufacturing_keywords",
        (
            "manufacturing",
            "manufacturer",
            "factory",
            "industrial",
            "supply chain",
            "制造",
            "制造业",
            "工厂",
            "工业",
            "供应链",
        ),
    ),
    (
        "finance_default",
        "finance_keywords",
        (
            "finance",
            "banking",
            "bank",
            "fund",
            "equity",
            "ir",
            "investor relations",
            "listed company",
            "金融",
            "银行",
            "基金",
            "股权",
            "投资者关系",
            "上市公司",
        ),
    ),
    (
        "internet_default",
        "internet_keywords",
        (
            "internet",
            "saas",
            "platform",
            "consumer app",
            "software",
            "互联网",
            "平台",
            "消费应用",
            "软件",
        ),
    ),
)


def resolve_policy_profile(
    *,
    default_policy_profile: str,
    explicit_policy_profile: str | None = None,
    industry: str | None = None,
    company: str | None = None,
    known_policy_profiles: Iterable[str] | None = None,
) -> PolicyProfileResolution:
    """Resolve a visible product PolicyProfile for a new workspace.

    This helper intentionally runs only during ReportSpec creation. Runtime
    gates consume the explicit `report_spec.yaml.policy_profile` field and do
    not re-infer a profile from natural-language industry strings.
    """

    known = {item for item in (known_policy_profiles or ()) if item}
    default_profile = _normalize_profile_id(default_policy_profile)
    explicit = _normalize_profile_id(explicit_policy_profile)
    input_text = _resolver_input(industry=industry, company=company)

    if explicit:
        if known and explicit not in known:
            raise ValueError(f"unknown policy_profile:{explicit}")
        return PolicyProfileResolution(
            policy_profile=explicit,
            source="explicit_override",
            input=input_text,
            matched_rule="explicit_policy_profile",
            confidence="explicit",
            alternatives=(),
        )

    matches = _matching_profiles(input_text, known_policy_profiles=known)
    if len(matches) == 1:
        profile_id, rule_id = matches[0]
        return PolicyProfileResolution(
            policy_profile=profile_id,
            source="industry_resolver",
            input=input_text,
            matched_rule=rule_id,
            confidence="deterministic_exact_or_keyword",
            alternatives=(),
        )

    if len(matches) > 1:
        return PolicyProfileResolution(
            policy_profile=default_profile,
            source="report_pack.default_policy_profile",
            input=input_text,
            matched_rule="ambiguous_industry_keywords",
            confidence="default_ambiguous",
            alternatives=tuple(profile_id for profile_id, _ in matches),
        )

    return PolicyProfileResolution(
        policy_profile=default_profile,
        source="report_pack.default_policy_profile",
        input=input_text,
        matched_rule="no_industry_match",
        confidence="default_no_match",
        alternatives=(),
    )


def _matching_profiles(
    input_text: str,
    *,
    known_policy_profiles: set[str],
) -> list[tuple[str, str]]:
    if not input_text.strip():
        return []
    matches: list[tuple[str, str]] = []
    for profile_id, rule_id, keywords in _PROFILE_RULES:
        if known_policy_profiles and profile_id not in known_policy_profiles:
            continue
        if any(_keyword_matches(input_text, keyword) for keyword in keywords):
            matches.append((profile_id, rule_id))
    return _prefer_specific_profiles(sorted(matches, key=lambda item: item[0]))


def _prefer_specific_profiles(matches: list[tuple[str, str]]) -> list[tuple[str, str]]:
    profile_ids = {profile_id for profile_id, _ in matches}
    if "solar_manufacturing_default" in profile_ids and "manufacturing_default" in profile_ids:
        return [item for item in matches if item[0] != "manufacturing_default"]
    return matches


def _keyword_matches(text: str, keyword: str) -> bool:
    normalized = text.lower()
    needle = keyword.lower().strip()
    if not needle:
        return False
    if _contains_cjk(needle):
        return needle in normalized
    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return re.search(pattern, normalized) is not None


def _joined_text(values: Sequence[str | None]) -> str:
    return " ".join(str(value).strip() for value in values if isinstance(value, str) and value.strip())


def _resolver_input(*, industry: str | None, company: str | None) -> str:
    # Industry is the policy hint. Company names are only a fallback when the
    # user gave no industry/theme, so an organization name cannot contaminate a
    # clear profile selection.
    industry_text = _joined_text([industry])
    if industry_text:
        return industry_text
    return _joined_text([company])


def _normalize_profile_id(value: str | None) -> str:
    return value.strip().replace("-", "_") if isinstance(value, str) else ""


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
