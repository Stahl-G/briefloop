"""Load, save, and merge competitor_universe.yaml and competitor_candidates.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]

from multi_agent_brief.analysis_modules.market_competitor.schemas import CompetitorEntity, CompetitorUniverse


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def load_competitor_universe(path: str | Path) -> CompetitorUniverse:
    """Load a CompetitorUniverse from a YAML file.

    Returns a valid (possibly empty) CompetitorUniverse even when the file
    does not exist — the caller checks ``universe.enabled`` and
    ``universe.entities`` to decide whether to activate.
    """
    p = Path(path)
    data = _load_yaml(p)
    if not data:
        return CompetitorUniverse(
            target=CompetitorEntity(entity_id="", name=""),
            enabled=False,
        )

    target_data = data.get("target", {}) or {}
    target = CompetitorEntity(
        entity_id=target_data.get("entity_id", ""),
        name=target_data.get("name", ""),
        aliases=target_data.get("aliases", []),
        relation=target_data.get("relation", "direct_competitor"),
        priority=target_data.get("priority", "primary"),
        geographies=target_data.get("geographies", []),
        technologies=target_data.get("technologies", []),
    )

    entities: list[CompetitorEntity] = []
    for ent in data.get("entities", []) or []:
        entities.append(CompetitorEntity(
            entity_id=ent.get("entity_id", ""),
            name=ent.get("name", ""),
            aliases=ent.get("aliases", []),
            relation=ent.get("relation", "direct_competitor"),
            priority=ent.get("priority", "primary"),
            geographies=ent.get("geographies", []),
            technologies=ent.get("technologies", []),
        ))

    # Deduplicate by entity_id
    seen: set[str] = set()
    deduped: list[CompetitorEntity] = []
    if target.entity_id:
        seen.add(target.entity_id)
    for ent in entities:
        if ent.entity_id not in seen:
            deduped.append(ent)
            seen.add(ent.entity_id)

    return CompetitorUniverse(
        target=target,
        market_scope=data.get("market_scope", {}),
        entities=deduped,
        mode=data.get("mode", "weekly_monitor"),
        enabled=bool(data.get("enabled", False)),
    )


def save_competitor_universe(universe: CompetitorUniverse, path: str | Path) -> None:
    """Serialize a CompetitorUniverse to YAML."""
    _save_yaml(Path(path), universe.to_dict())


def load_competitor_candidates(path: str | Path) -> list[dict[str, Any]]:
    """Load candidate competitors from competitor_candidates.yaml.

    Returns the ``candidates`` list, or an empty list if the file does
    not exist / is empty.
    """
    data = _load_yaml(Path(path))
    return data.get("candidates", []) or []


def save_competitor_candidates(candidates: list[dict[str, Any]], path: str | Path) -> None:
    """Write candidate competitors to competitor_candidates.yaml."""
    _save_yaml(Path(path), {"candidates": candidates})


def merge_candidates_to_universe(
    candidates_path: str | Path,
    universe_path: str | Path,
) -> int:
    """Merge approved candidates (``approved: true``) from
    competitor_candidates.yaml into competitor_universe.yaml.

    Returns the number of entities added.
    """
    candidates = load_competitor_candidates(candidates_path)
    universe = load_competitor_universe(universe_path)

    existing_ids = {universe.target.entity_id} if universe.target.entity_id else set()
    for ent in universe.entities:
        existing_ids.add(ent.entity_id)

    added = 0
    for c in candidates:
        if not c.get("approved", False):
            continue
        entity_id = c.get("entity_id", "").strip()
        if not entity_id:
            continue
        if entity_id in existing_ids:
            continue

        universe.entities.append(CompetitorEntity(
            entity_id=entity_id,
            name=c.get("name", entity_id),
            aliases=c.get("aliases", []),
            relation=c.get("relation", "direct_competitor"),
            priority=c.get("priority", "primary"),
            geographies=c.get("market_overlap", {}).get("geography", [])
                       if isinstance(c.get("market_overlap"), dict)
                       else [],
            technologies=c.get("market_overlap", {}).get("product", [])
                        if isinstance(c.get("market_overlap"), dict)
                        else [],
        ))
        existing_ids.add(entity_id)
        added += 1

    if added > 0:
        save_competitor_universe(universe, universe_path)

    return added


# ── Template generators ─────────────────────────────────────────────────────


def generate_universe_template(target_entity: CompetitorEntity | None = None) -> dict[str, Any]:
    """Generate the default competitor_universe.yaml template."""
    tgt = target_entity or CompetitorEntity(entity_id="", name="")
    return {
        "target": {
            "entity_id": tgt.entity_id,
            "name": tgt.name,
            "aliases": tgt.aliases,
            "relation": tgt.relation,
            "priority": tgt.priority,
            "geographies": tgt.geographies,
            "technologies": tgt.technologies,
        },
        "market_scope": {
            "geographies": [],
            "products": [],
            "customer_segments": [],
            "value_chain_positions": [],
        },
        "entities": [],
        "mode": "weekly_monitor",
        "enabled": False,
    }


def generate_candidates_template() -> dict[str, Any]:
    """Generate the default competitor_candidates.yaml template."""
    return {"candidates": []}
