"""Pure adapters from one frozen Store source plan to packaged providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import stat
from typing import Callable

from multi_agent_brief.contracts.v2 import (
    RunDirection,
    RuntimeCachedPackageAcquisitionSpec,
    RuntimeNewsApiAcquisitionSpec,
    RuntimeSourcePlanBinding,
    RuntimeSourceRouteBinding,
    RuntimeWebSearchAcquisitionSpec,
    SourceProposal,
)
from multi_agent_brief.control_store.serialization import (
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.core_run_v2.service import _derive_runtime_source_plan
from multi_agent_brief.sources.api_news import NewsApiProvider
from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery
from multi_agent_brief.sources.cached_package import CachedPackageProvider
from multi_agent_brief.sources.web_search import WebSearchProvider

from .errors import RuntimeHostError


@dataclass(frozen=True)
class FrozenSourceMaterial:
    proposal: SourceProposal
    content: bytes
    raw_payload: bytes


ProviderFactory = Callable[[str], SourceProvider]


def derive_runtime_source_plan(
    content: bytes,
    *,
    run_id: str,
    sources_config_sha256: str,
    run_direction: RunDirection | None = None,
    workspace_root: Path | None = None,
) -> RuntimeSourcePlanBinding:
    return _derive_runtime_source_plan(
        content,
        run_id=run_id,
        sources_config_sha256=sources_config_sha256,
        run_direction=run_direction,
        workspace_root=workspace_root,
    )


def collect_frozen_source(
    workspace: Path,
    *,
    run_id: str,
    invocation_id: str,
    route: RuntimeSourceRouteBinding,
    provider_factory: ProviderFactory | None = None,
) -> FrozenSourceMaterial:
    """Execute exactly one frozen deterministic route without mutable config."""

    spec = route.acquisition_spec
    if route.execution_owner != "deterministic" or spec is None:
        raise RuntimeHostError("runtime_source_plan_invalid")
    factory = provider_factory or _provider
    items: list[SourceItem] = []
    if isinstance(spec, RuntimeWebSearchAcquisitionSpec):
        provider = factory("web_search")
        for request in spec.requests:
            items.extend(
                provider.collect(
                    SourceQuery(
                        keywords=[],
                        max_results=request.max_results,
                        recency_days=request.recency_days or 0,
                    ),
                    {
                        "enabled": True,
                        "mode": "external_api",
                        "backend": spec.provider_id,
                        "max_results": request.max_results,
                        "recency_days": request.recency_days,
                        "search_tasks": [
                            {
                                "query": request.query,
                                "domains": request.domains,
                            }
                        ],
                    },
                )
            )
    elif isinstance(spec, RuntimeNewsApiAcquisitionSpec):
        provider = factory("newsapi")
        items = provider.collect(
            SourceQuery(
                keywords=[spec.query],
                start_date=spec.start_date or "",
                end_date=spec.end_date or "",
                max_results=spec.max_results,
            ),
            {
                "enabled": True,
                "providers": [{"name": "newsapi", "api_key_env": "NEWSAPI_API_KEY"}],
                "query": spec.query,
                "max_results": spec.max_results,
                "sort_by": spec.sort_by,
                "language": spec.language,
                "domains": spec.domains,
            },
        )
    elif isinstance(spec, RuntimeCachedPackageAcquisitionSpec):
        provider = factory("cached_package")
        absolute_paths = _validated_cached_paths(workspace, list(spec.paths))
        items = provider.collect(
            SourceQuery(),
            {
                "enabled": True,
                "paths": [str(item) for item in absolute_paths],
                "formats": list(spec.formats),
            },
        )
    else:  # pragma: no cover - discriminated strict contract is total
        raise RuntimeHostError("runtime_source_plan_invalid")
    if not items:
        raise RuntimeHostError("runtime_source_acquisition_failed")
    item = sorted(
        items,
        key=lambda value: (
            value.url,
            value.source_id,
            value.title,
            sha256_hex(value.content.encode("utf-8")),
        ),
    )[0]
    return _material_from_item(
        workspace=workspace,
        run_id=run_id,
        invocation_id=invocation_id,
        route=route,
        item=item,
    )


def _provider(kind: str) -> SourceProvider:
    if kind == "web_search":
        return WebSearchProvider()
    if kind == "newsapi":
        return NewsApiProvider()
    if kind == "cached_package":
        return CachedPackageProvider()
    raise RuntimeHostError("runtime_source_plan_invalid")


def _validated_cached_paths(workspace: Path, paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for relative in paths:
        current = workspace
        for part in Path(relative).parts:
            current = current / part
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise RuntimeHostError("runtime_source_acquisition_failed") from exc
            if current.is_symlink() or not (
                stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
            ):
                raise RuntimeHostError("runtime_source_acquisition_failed")
        result.append(current)
    return result


def _material_from_item(
    *,
    workspace: Path,
    run_id: str,
    invocation_id: str,
    route: RuntimeSourceRouteBinding,
    item: SourceItem,
) -> FrozenSourceMaterial:
    content = item.content.encode("utf-8")
    if not content:
        raise RuntimeHostError("runtime_source_acquisition_failed")
    raw_payload = canonical_json_bytes(item.to_dict())
    source_id = derived_id(
        "SRC-HOST",
        route.route_fingerprint,
        item.source_id,
        sha256_hex(content),
    )
    proposal_id = derived_id("PROP-SOURCE-HOST", invocation_id, source_id)
    is_cached = route.route_kind == "cached_package"
    is_newsapi = route.route_id == "api"
    published_at = _published_date(item.published_at)
    locator: dict[str, str]
    if is_cached:
        spec = route.acquisition_spec
        if not isinstance(spec, RuntimeCachedPackageAcquisitionSpec):
            raise RuntimeHostError("runtime_source_plan_invalid")
        observed_path = item.metadata.get("path")
        if not isinstance(observed_path, str):
            raise RuntimeHostError("runtime_source_acquisition_failed")
        try:
            selected = Path(observed_path)
            selected_relative = selected.relative_to(workspace).as_posix()
        except ValueError as exc:
            raise RuntimeHostError("runtime_source_acquisition_failed") from exc
        roots = [Path(logical) for logical in spec.paths]
        selected_path = Path(selected_relative)
        if not any(
            selected_path == root or root in selected_path.parents for root in roots
        ):
            raise RuntimeHostError("runtime_source_acquisition_failed")
        _validated_cached_paths(workspace, [selected_relative])
        locator = {"kind": "file", "path": selected_relative}
    else:
        if not item.url:
            raise RuntimeHostError("runtime_source_acquisition_failed")
        locator = {"kind": "web", "url": item.url}
    proposal = SourceProposal.model_validate(
        {
            "schema_version": SourceProposal.schema_id,
            "proposal_id": proposal_id,
            "run_id": run_id,
            "source_id": source_id,
            "origin_type": (
                "cached_provider_response"
                if is_cached
                else "provider_response"
                if is_newsapi
                else "search_snippet_only"
            ),
            "acquisition_method": (
                "cached_provider_response"
                if is_cached
                else "provider_extract"
                if is_newsapi
                else "provider_search"
            ),
            "material_kind": (
                "full_content"
                if is_cached
                else "partial_extract"
                if is_newsapi
                else "search_snippet"
            ),
            "provider": route.provider_id or "cached_package",
            "locator": locator,
            "title": item.title.strip()
            or item.source_name.strip()
            or "Collected source",
            "publisher": item.source_name.strip() or None,
            "published_at": published_at,
            "retrieved_at": item.retrieved_at,
            "source_category": "other" if is_cached else "news_media",
            "retrieval_source_type": "local_file" if is_cached else "news_media",
            "underlying_evidence_type": "unknown" if is_cached else "media_report",
            "raw_underlying_evidence_type": (
                "cached_package" if is_cached else "provider-response"
            ),
            "content_sha256": sha256_hex(content),
            "content_media_type": "text/plain",
            "raw_payload_sha256": sha256_hex(raw_payload),
            "raw_payload_media_type": "application/json",
        },
        strict=True,
    )
    return FrozenSourceMaterial(
        proposal=proposal,
        content=content,
        raw_payload=raw_payload,
    )


def _published_date(value: str) -> str | None:
    candidate = value[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


__all__ = [
    "FrozenSourceMaterial",
    "collect_frozen_source",
    "derive_runtime_source_plan",
]
