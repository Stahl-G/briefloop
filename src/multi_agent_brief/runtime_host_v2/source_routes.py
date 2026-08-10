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
    RuntimeWebSearchAcquisitionSpecV3,
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
from multi_agent_brief.sources.tavily_acquisition import (
    TavilyAcquisitionBundleError,
    TavilyAcquisitionObservation,
    TavilyMultiAcquisitionObservation,
    parse_tavily_acquisition_bundle,
    tavily_observation_matches_spec,
)
from multi_agent_brief.sources.web_search import WebSearchProvider

from .errors import RuntimeHostError
from .submission import MAX_SOURCE_PACK_MEMBERS


@dataclass(frozen=True)
class FrozenSourceMaterial:
    proposal: SourceProposal
    content: bytes
    raw_payload: bytes


@dataclass(frozen=True)
class FrozenSourceCollection:
    materials: tuple[FrozenSourceMaterial, ...]
    provider_response: bytes | None
    provider_status_code: int | None
    result_count: int
    durable_content_count: int
    material_validation_failed: bool = False


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


def collect_frozen_sources(
    workspace: Path,
    *,
    run_id: str,
    invocation_id: str,
    route: RuntimeSourceRouteBinding,
    provider_factory: ProviderFactory | None = None,
) -> tuple[FrozenSourceMaterial, ...]:
    """Execute one frozen route and retain every deterministically ordered result."""

    return collect_frozen_source_pack(
        workspace,
        run_id=run_id,
        invocation_id=invocation_id,
        route=route,
        provider_factory=provider_factory,
    ).materials


def collect_frozen_source_pack(
    workspace: Path,
    *,
    run_id: str,
    invocation_id: str,
    route: RuntimeSourceRouteBinding,
    provider_factory: ProviderFactory | None = None,
    retrieved_at: str | None = None,
) -> FrozenSourceCollection:
    """Execute one route and retain its exact provider response when available."""

    spec = route.acquisition_spec
    if route.execution_owner != "deterministic" or spec is None:
        raise RuntimeHostError("runtime_source_plan_invalid")
    factory = provider_factory or _provider
    items: list[SourceItem] = []
    provider_response: bytes | None = None
    provider_status_code: int | None = None
    tavily_observation: (
        TavilyAcquisitionObservation | TavilyMultiAcquisitionObservation | None
    ) = None
    if isinstance(spec, RuntimeWebSearchAcquisitionSpecV3):
        if route.provider_id != "tavily":
            raise RuntimeHostError("runtime_source_plan_invalid")
        provider = factory("web_search")
        if not isinstance(provider, WebSearchProvider):
            raise RuntimeHostError("runtime_source_plan_invalid")
        collected = provider.collect_with_response(
            SourceQuery(keywords=[], max_results=800, recency_days=7),
            {
                "enabled": True,
                "mode": "external_api",
                "backend": "tavily",
                "_workspace_dir": str(workspace),
                "acquisition_mode": "multi_search_batch_extract",
                "max_unique_urls": spec.max_unique_urls,
                "extract_batch_size": spec.extract_batch_size,
                "search_tasks": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in spec.tasks
                ],
            },
        )
        provider_response = collected.raw_response
        provider_status_code = collected.status_code
        items.extend(collected.items)
    elif isinstance(spec, RuntimeWebSearchAcquisitionSpec):
        if route.provider_id == "tavily":
            raise RuntimeHostError("runtime_source_plan_invalid")
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
                        "_workspace_dir": str(workspace),
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
    if not items and route.provider_id != "tavily":
        raise RuntimeHostError("source_pack_empty")
    result_count = len(items)
    durable_content_count = 0
    material_validation_failed = False
    if route.provider_id == "tavily":
        if (
            provider_response is None
            or not provider_response
            or provider_status_code != 200
            or (
                isinstance(spec, RuntimeWebSearchAcquisitionSpec)
                and len(spec.requests) != 1
            )
        ):
            raise RuntimeHostError("source_provider_result_invalid")
        try:
            tavily_observation = parse_tavily_acquisition_bundle(provider_response)
        except TavilyAcquisitionBundleError:
            material_validation_failed = True
        else:
            result_count = tavily_observation.result_count
            durable_content_count = tavily_observation.durable_content_count
            material_validation_failed = not tavily_observation_matches_spec(
                tavily_observation, spec
            ) or not _items_match_tavily_observation(
                items,
                tavily_observation,
            )
    try:
        canonical_items = (
            []
            if material_validation_failed
            else _canonical_source_items(
                items,
                max_members=(
                    800
                    if isinstance(spec, RuntimeWebSearchAcquisitionSpecV3)
                    else MAX_SOURCE_PACK_MEMBERS
                ),
            )
        )
        ordered = sorted(
            canonical_items,
            key=lambda value: (
                value.url,
                value.source_id,
                value.title,
                sha256_hex(value.content.encode("utf-8")),
            ),
        )
        materials = tuple(
            _material_from_item(
                workspace=workspace,
                run_id=run_id,
                invocation_id=invocation_id,
                route=route,
                item=item,
                retrieved_at=retrieved_at,
            )
            for item in ordered
        )
    except RuntimeHostError:
        if route.provider_id != "tavily":
            raise
        materials = ()
        material_validation_failed = True
    return FrozenSourceCollection(
        materials=materials,
        provider_response=provider_response,
        provider_status_code=provider_status_code,
        result_count=result_count,
        durable_content_count=durable_content_count,
        material_validation_failed=material_validation_failed,
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
    logical_paths = [Path(value) for value in paths]
    for position, left in enumerate(logical_paths):
        for right in logical_paths[position + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise RuntimeHostError("runtime_source_pack_invalid")
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


def _canonical_source_items(
    items: list[SourceItem], *, max_members: int = MAX_SOURCE_PACK_MEMBERS
) -> list[SourceItem]:
    """Close count and duplicate identity before any authoritative mutation."""

    if len(items) > max_members:
        raise RuntimeHostError("runtime_source_pack_invalid")
    by_identity: dict[str, tuple[bytes, SourceItem]] = {}
    for item in items:
        payload = canonical_json_bytes(item.to_dict())
        previous = by_identity.get(item.source_id)
        if previous is None:
            by_identity[item.source_id] = (payload, item)
        elif previous[0] != payload:
            raise RuntimeHostError("runtime_source_pack_invalid")
    canonical = [value[1] for value in by_identity.values()]
    if len(canonical) > max_members:
        raise RuntimeHostError("runtime_source_pack_invalid")
    return canonical


def _material_from_item(
    *,
    workspace: Path,
    run_id: str,
    invocation_id: str,
    route: RuntimeSourceRouteBinding,
    item: SourceItem,
    retrieved_at: str | None = None,
) -> FrozenSourceMaterial:
    content = item.content.encode("utf-8")
    if not content:
        raise RuntimeHostError("runtime_source_acquisition_failed")
    provider_projection = item.metadata.get("provider_projection")
    raw_payload = (
        canonical_json_bytes(provider_projection)
        if route.provider_id == "tavily" and isinstance(provider_projection, dict)
        else canonical_json_bytes(item.to_dict())
    )
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
    has_durable_tavily_content = route.provider_id == "tavily" and (
        _has_durable_tavily_content(item)
    )
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
                if is_newsapi or has_durable_tavily_content
                else "search_snippet_only"
            ),
            "acquisition_method": (
                "cached_provider_response"
                if is_cached
                else "provider_extract"
                if is_newsapi or has_durable_tavily_content
                else "provider_search"
            ),
            "material_kind": (
                "full_content"
                if is_cached
                else "partial_extract"
                if is_newsapi or has_durable_tavily_content
                else "search_snippet"
            ),
            "provider": route.provider_id or "cached_package",
            "locator": locator,
            "title": item.title.strip()
            or item.source_name.strip()
            or "Collected source",
            "publisher": item.source_name.strip() or None,
            "published_at": published_at,
            "retrieved_at": retrieved_at or item.retrieved_at,
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


def _has_durable_tavily_content(item: SourceItem) -> bool:
    return (
        item.source_type == "web_search"
        and item.metadata.get("backend") == "tavily"
        and item.metadata.get("content_shape") == "provider_extract_content"
        and item.metadata.get("has_raw_content") is True
        and item.metadata.get("evidence_quality") == "partial_extract"
        and bool(item.content.strip())
    )


def _items_match_tavily_observation(
    items: list[SourceItem],
    observation: TavilyAcquisitionObservation | TavilyMultiAcquisitionObservation,
) -> bool:
    """Bind every and only successful Extract item to its frozen projection."""

    expected = {item.url: item for item in observation.sources}
    if len(items) != len(expected) or len(items) != len(
        {item.source_id for item in items}
    ):
        return False
    observed_urls: set[str] = set()
    for item in items:
        expected_item = expected.get(item.url)
        projection = item.metadata.get("provider_projection")
        if (
            expected_item is None
            or item.url in observed_urls
            or not _has_durable_tavily_content(item)
            or not isinstance(projection, dict)
            or canonical_json_bytes(projection) != expected_item.projection
            or item.content.encode("utf-8") != expected_item.content
        ):
            return False
        observed_urls.add(item.url)
    return observed_urls == set(expected)


def _published_date(value: str) -> str | None:
    candidate = value[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


__all__ = [
    "FrozenSourceCollection",
    "FrozenSourceMaterial",
    "collect_frozen_source_pack",
    "collect_frozen_sources",
    "derive_runtime_source_plan",
]
