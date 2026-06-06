"""Local Signal Planner: generate local-language, local-platform consumer signal discovery tasks.

Deterministic, no external APIs, no LLM calls.
Reads source_discovery config and produces LocalSignalTask list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Market Platform Hints ────────────────────────────────────────────
# Editable hints, not authoritative facts. Users can override in sources.yaml.

MARKET_PLATFORM_HINTS: dict[str, dict[str, Any]] = {
    "vietnam": {
        "languages": ["vi"],
        "platform_groups": {
            "ecommerce": ["Shopee", "Lazada", "TikTok Shop"],
            "social": ["Facebook", "TikTok"],
            "video": ["YouTube", "TikTok"],
            "forum": ["Tinhte", "Webtretho"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} đánh giá",
            "social": "{company} đánh giá người dùng",
            "video": "{company} review trải nghiệm",
            "forum": "{company} kinh nghiệm đánh giá",
        },
    },
    "japan": {
        "languages": ["ja"],
        "platform_groups": {
            "ecommerce": ["Rakuten", "Amazon Japan", "Yahoo Shopping"],
            "social": ["X", "Instagram"],
            "video": ["YouTube", "TikTok"],
            "forum": ["5ch", "Yahoo知恵袋"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} レビュー 口コミ",
            "social": "{company} 評判 ユーザー",
            "video": "{company} レビュー 体験談",
            "forum": "{company} 口コミ 評価",
        },
    },
    "china": {
        "languages": ["zh"],
        "platform_groups": {
            "ecommerce": ["天猫", "京东", "抖音商城"],
            "social": ["小红书", "微博", "抖音"],
            "video": ["B站", "抖音", "快手"],
            "forum": ["知乎", "贴吧"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} 评价 评测",
            "social": "{company} 用户评价 口碑",
            "video": "{company} 测评 体验",
            "forum": "{company} 怎么样 评价",
        },
    },
    "indonesia": {
        "languages": ["id"],
        "platform_groups": {
            "ecommerce": ["Shopee", "Tokopedia", "Lazada"],
            "social": ["Instagram", "TikTok", "Facebook"],
            "video": ["YouTube", "TikTok"],
            "forum": ["Kaskus"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} ulasan review",
            "social": "{company} review pengguna",
            "video": "{company} review pengalaman",
            "forum": "{company} pengalaman review",
        },
    },
    "thailand": {
        "languages": ["th"],
        "platform_groups": {
            "ecommerce": ["Shopee", "Lazada"],
            "social": ["Facebook", "TikTok", "Instagram"],
            "video": ["YouTube", "TikTok"],
            "forum": ["Pantip"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} รีวิว ความคิดเห็น",
            "social": "{company} รีวิว ผู้ใช้",
            "video": "{company} รีวิว ประสบการณ์",
            "forum": "{company} รีวิว ความคิดเห็น",
        },
    },
    "brazil": {
        "languages": ["pt"],
        "platform_groups": {
            "ecommerce": ["Mercado Livre", "Amazon Brazil"],
            "social": ["Instagram", "TikTok"],
            "video": ["YouTube"],
            "forum": ["Reddit"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} avaliação review",
            "social": "{company} avaliação usuários",
            "video": "{company} review experiência",
            "forum": "{company} opinião avaliação",
        },
    },
    "mexico": {
        "languages": ["es"],
        "platform_groups": {
            "ecommerce": ["Mercado Libre", "Amazon Mexico"],
            "social": ["Facebook", "TikTok"],
            "video": ["YouTube"],
            "forum": ["Reddit"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} reseña opinión",
            "social": "{company} opiniones usuarios",
            "video": "{company} reseña experiencia",
            "forum": "{company} opiniones reseña",
        },
    },
    "germany": {
        "languages": ["de"],
        "platform_groups": {
            "ecommerce": ["Amazon Germany", "Otto"],
            "social": ["Instagram", "TikTok"],
            "video": ["YouTube"],
            "forum": ["Reddit"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} Bewertung Test",
            "social": "{company} Erfahrungen Nutzer",
            "video": "{company} Test Erfahrung",
            "forum": "{company} Erfahrungen Bewertung",
        },
    },
    "korea": {
        "languages": ["ko"],
        "platform_groups": {
            "ecommerce": ["Coupang", "Naver Shopping"],
            "social": ["Instagram", "Naver Blog"],
            "video": ["YouTube"],
            "forum": ["Naver Cafe", "DC Inside"],
        },
        "query_templates": {
            "ecommerce": "{company} {product} 리뷰 후기",
            "social": "{company} 사용자 리뷰",
            "video": "{company} 리뷰 경험",
            "forum": "{company} 후기 리뷰",
        },
    },
}

# ── Consumer Signal Goals ────────────────────────────────────────────

CONSUMER_SIGNAL_GOAL_DEFAULTS = [
    "complaints",
    "purchase_barriers",
    "price_sensitivity",
    "channel_availability",
    "product_comparison",
]

GOAL_TO_SIGNAL_TYPE: dict[str, str] = {
    "complaints": "consumer_discussion",
    "purchase_barriers": "consumer_discussion",
    "price_sensitivity": "consumer_discussion",
    "channel_availability": "platform_data",
    "product_comparison": "consumer_discussion",
    "brand_mentions": "consumer_discussion",
    "creator_content": "consumer_discussion",
    "sales_ranking": "platform_data",
    "engagement_metrics": "platform_data",
    "trend_analysis": "external_trend_inference",
}

GOAL_TO_EXPECTED_FINDINGS: dict[str, list[str]] = {
    "complaints": ["common complaints", "negative reviews", "product issues"],
    "purchase_barriers": ["purchase barriers", "hesitation factors", "competitor preference reasons"],
    "price_sensitivity": ["price comments", "value perception", "discount expectations"],
    "channel_availability": ["availability by channel", "shipping experience", "stock status"],
    "product_comparison": ["competitor comparisons", "feature differences", "switching reasons"],
    "brand_mentions": ["brand mentions", "brand sentiment", "brand association"],
    "creator_content": ["influencer reviews", "creator opinions", "viral content"],
    "sales_ranking": ["sales rankings", "best seller status", "category position"],
    "engagement_metrics": ["likes", "comments", "shares", "follower growth"],
    "trend_analysis": ["market trends", "demand shifts", "category growth"],
}

# ── Platform Group Defaults ──────────────────────────────────────────

PLATFORM_GROUP_DEFAULTS = ["ecommerce", "social", "video", "forum"]

# ── Dataclass ────────────────────────────────────────────────────────


@dataclass
class LocalSignalTask:
    """A deterministic local signal discovery task."""

    task_id: str
    market: str
    language: str
    platform_group: str
    suggested_platforms: list[str]
    query: str
    signal_type: str
    execution_mode: list[str]
    expected_findings: list[str]
    expected_raw_inputs: list[str] = field(default_factory=lambda: ["text_export", "screenshot"])
    expected_output_schema: str = "local_signal_sample_v1"
    may_support_current_fact: bool = False
    requires_current_source: bool = True
    requires_sample_metadata: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "market": self.market,
            "language": self.language,
            "platform_group": self.platform_group,
            "suggested_platforms": self.suggested_platforms,
            "query": self.query,
            "signal_type": self.signal_type,
            "execution_mode": self.execution_mode,
            "expected_findings": self.expected_findings,
            "expected_raw_inputs": self.expected_raw_inputs,
            "expected_output_schema": self.expected_output_schema,
            "evidence_limit": {
                "may_support_current_fact": self.may_support_current_fact,
                "requires_current_source": self.requires_current_source,
                "requires_sample_metadata": self.requires_sample_metadata,
            },
        }


# ── Main Entry Point ─────────────────────────────────────────────────


def build_local_signal_tasks(discovery: dict[str, Any]) -> list[LocalSignalTask]:
    """Build local signal tasks from source_discovery config.

    Returns empty list if local_signal_discovery is not enabled or
    no target_markets are configured.
    """
    lsd = discovery.get("local_signal_discovery", {})
    if not lsd.get("enabled", False):
        return []

    target_markets = lsd.get("target_markets", [])
    if not target_markets:
        # Try to infer from discovery language
        return []

    company = discovery.get("company", "")
    industry = discovery.get("industry", "")
    platform_groups = lsd.get("platform_groups", PLATFORM_GROUP_DEFAULTS)
    consumer_goals = lsd.get("consumer_signal_goals", CONSUMER_SIGNAL_GOAL_DEFAULTS)
    execution_modes = lsd.get("execution_modes", [
        "public_web_search",
        "manual_review",
        "authorized_browser_collection",
        "opencli_local_extraction",
    ])

    tasks: list[LocalSignalTask] = []
    seq_counter: dict[str, int] = {}

    for market_entry in target_markets:
        # Support both string and dict format
        if isinstance(market_entry, str):
            market_name = market_entry
            market_languages: list[str] = []
        else:
            market_name = market_entry.get("market", "")
            market_languages = market_entry.get("local_languages", [])

        if not market_name:
            continue

        market_key = market_name.lower().strip()
        market_cfg = MARKET_PLATFORM_HINTS.get(market_key, {})

        # Resolve languages: explicit config > hints > discovery language
        languages = market_languages or market_cfg.get("languages", [])
        if not languages:
            lang = discovery.get("language", "en")
            languages = [lang]

        # Resolve platform groups for this market
        market_platform_groups = market_cfg.get("platform_groups", {})

        for lang in languages:
            for pg in platform_groups:
                platforms = market_platform_groups.get(pg, [])
                queries = _generate_queries(
                    company=company,
                    industry=industry,
                    market_key=market_key,
                    platform_group=pg,
                    language=lang,
                    market_cfg=market_cfg,
                )

                for goal in consumer_goals:
                    seq_key = f"{market_key}_{lang}"
                    seq_counter[seq_key] = seq_counter.get(seq_key, 0) + 1
                    seq = seq_counter[seq_key]

                    # Pick the best query for this goal
                    query = queries[0] if queries else f"{company} {industry}"

                    signal_type = GOAL_TO_SIGNAL_TYPE.get(goal, "consumer_discussion")
                    expected = list(GOAL_TO_EXPECTED_FINDINGS.get(goal, [goal]))

                    tasks.append(LocalSignalTask(
                        task_id=f"LS_{market_key[:2].upper()}_{seq:03d}",
                        market=market_name,
                        language=lang,
                        platform_group=pg,
                        suggested_platforms=platforms,
                        query=query,
                        signal_type=signal_type,
                        execution_mode=list(execution_modes),
                        expected_findings=expected,
                    ))

    return tasks


# ── Helpers ───────────────────────────────────────────────────────────


def _generate_queries(
    *,
    company: str,
    industry: str,
    market_key: str,
    platform_group: str,
    language: str,
    market_cfg: dict[str, Any],
) -> list[str]:
    """Generate local-language queries for a market + platform group."""
    templates = market_cfg.get("query_templates", {})
    template = templates.get(platform_group, "{company} {industry} review")

    product = industry or company
    query = template.format(company=company, product=product, industry=industry).strip()

    # Deduplicate whitespace
    while "  " in query:
        query = query.replace("  ", " ")

    return [query] if query else []


def get_execution_modes_for_market(market: str) -> list[str]:
    """Get default execution modes for a market."""
    return [
        "public_web_search",
        "manual_review",
        "authorized_browser_collection",
        "opencli_local_extraction",
    ]


# ── Collector Tasks ──────────────────────────────────────────────────


def generate_collector_tasks(discovery: dict[str, Any]) -> dict[str, Any]:
    """Generate collector_tasks.json from local signal tasks.

    This is an execution plan for humans, authorized browser tools,
    or OpenCLI adapters. It is NOT a source artifact.
    """
    tasks = build_local_signal_tasks(discovery)
    if not tasks:
        return {"status": "no_tasks", "tasks": []}

    collector_tasks = []
    for task in tasks:
        collector_tasks.append({
            "task_id": task.task_id,
            "collector_type": "authorized_browser_or_manual",
            "market": task.market,
            "language": task.language,
            "platform_group": task.platform_group,
            "suggested_platforms": task.suggested_platforms,
            "query": task.query,
            "signal_type": task.signal_type,
            "execution_mode": task.execution_mode,
            "instructions": [
                "Search using the local-language query.",
                "Collect only public or user-authorized accessible results.",
                "Record platform, URL, timestamp, access condition, sample size, and raw file path.",
                "Do not collect private messages or personal identifiers.",
            ],
            "expected_output_schema": "local_signal_sample_v1",
            "expected_raw_inputs": task.expected_raw_inputs,
            "privacy_rules": {
                "do_not_collect_private_messages": True,
                "redact_user_identifiers": True,
            },
        })

    return {
        "status": "ready",
        "tasks": collector_tasks,
    }


def write_collector_tasks_json(
    discovery: dict[str, Any],
    output_path: Path,
) -> dict[str, Any] | None:
    """Generate and write collector_tasks.json. Returns tasks dict or None."""
    tasks = generate_collector_tasks(discovery)
    if not tasks.get("tasks"):
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return tasks


# ── Local Signal Samples Parser ──────────────────────────────────────

SAMPLE_REQUIRED_FIELDS = {
    "sample_id", "task_id", "platform", "market", "language",
    "collected_at", "access_level", "sample_type", "contains_personal_data", "collector",
}


def parse_local_signal_samples(samples_path: Path) -> list[dict[str, Any]]:
    """Parse local_signal_samples.jsonl into source-like records.

    Each line should follow the local_signal_sample_v1 schema.
    Returns list of dicts ready for CLI provider ingestion.
    Skips invalid lines with warnings.
    """
    if not samples_path.exists():
        return []

    try:
        text = samples_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = samples_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    warnings: list[str] = []

    for line_num, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"Line {line_num}: invalid JSON: {exc}")
            continue

        # Check required fields
        missing = SAMPLE_REQUIRED_FIELDS - set(sample.keys())
        if missing:
            warnings.append(f"Line {line_num}: missing required fields: {missing}")
            continue

        # Check privacy
        if sample.get("contains_personal_data", False):
            warnings.append(
                f"Line {line_num}: sample {sample.get('sample_id')} contains personal data "
                f"and will be excluded from brief. Set contains_personal_data=false after redaction."
            )
            continue

        # Convert to source-like record
        records.append(_sample_to_source(sample))

    return records


def _sample_to_source(sample: dict[str, Any]) -> dict[str, Any]:
    """Convert a local signal sample to a source-like dict for CLI provider."""
    market = sample.get("market", "")
    platform = sample.get("platform", "")
    signal_type = sample.get("signal_type", "consumer_discussion")
    sample_type = sample.get("sample_type", "text_export")

    title = f"Local signal: {platform} {market} ({signal_type})"
    content = sample.get("text_excerpt", "")
    if not content and sample.get("collector_notes"):
        content = sample["collector_notes"]

    return {
        "title": title,
        "content": content,
        "url": sample.get("raw_file", ""),
        "published_at": sample.get("collected_at", "")[:10],  # date only
        "source_name": f"{platform} local signal",
        "source_type": "local_signal",
        "metadata": {
            "source_family": "local_signal",
            "collector": sample.get("collector", "unknown"),
            "collector_task_id": sample.get("task_id", ""),
            "platform_group": sample.get("platform_group", ""),
            "platform": platform,
            "market": market,
            "language": sample.get("language", ""),
            "signal_type": signal_type,
            "sample_type": sample_type,
            "access_level": sample.get("access_level", "unknown"),
            "contains_personal_data": False,
            "sample_size": sample.get("sample_size", 0),
            "raw_file": sample.get("raw_file", ""),
            "collector_notes": sample.get("collector_notes", ""),
        },
    }


# ── Local Signal Report ──────────────────────────────────────────────


def generate_local_signal_report(
    discovery: dict[str, Any],
    tasks: list[LocalSignalTask],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate local_signal_report.json from tasks and collected samples.

    Distinguishes consumer_discussion, platform_data, external_trend_inference.
    If direct samples are unavailable, outputs data gaps instead of inventing conclusions.
    """
    lsd = discovery.get("local_signal_discovery", {})
    target_markets = []
    for m in lsd.get("target_markets", []):
        if isinstance(m, str):
            target_markets.append(m)
        else:
            target_markets.append(m.get("market", ""))

    languages = lsd.get("local_languages", [])
    if not languages:
        lang = discovery.get("language", "en")
        languages = [lang]

    # Count tasks by signal type
    tasks_by_type: dict[str, int] = {}
    for task in tasks:
        tasks_by_type[task.signal_type] = tasks_by_type.get(task.signal_type, 0) + 1

    # Process samples into signals
    signals_found = []
    samples_by_task: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        meta = sample.get("metadata", {})
        task_id = meta.get("collector_task_id", "")
        if task_id:
            samples_by_task.setdefault(task_id, []).append(sample)

    signal_counter = 0
    for task_id, task_samples in samples_by_task.items():
        signal_counter += 1
        # Find the matching task
        matching_task = next((t for t in tasks if t.task_id == task_id), None)
        signal_type = matching_task.signal_type if matching_task else "consumer_discussion"
        market = matching_task.market if matching_task else ""
        language = matching_task.language if matching_task else ""
        platform_group = matching_task.platform_group if matching_task else ""

        # Aggregate sample info
        total_size = sum(
            s.get("metadata", {}).get("sample_size", 0) for s in task_samples
        )
        source_ids = [s.get("title", "") for s in task_samples]

        signals_found.append({
            "signal_id": f"LSR_{signal_counter:03d}",
            "signal_type": signal_type,
            "market": market,
            "language": language,
            "platform_group": platform_group,
            "summary": f"Collected {len(task_samples)} sample(s) from {platform_group}.",
            "source_ids": source_ids,
            "confidence": "low",
            "sample_size": total_size,
        })

    # Generate data gaps for tasks without samples
    data_gaps = []
    gap_counter = 0
    sampled_task_ids = set(samples_by_task.keys())
    for task in tasks:
        if task.task_id not in sampled_task_ids:
            gap_counter += 1
            data_gaps.append({
                "gap_id": f"GAP_{gap_counter:03d}",
                "market": task.market,
                "language": task.language,
                "platform_group": task.platform_group,
                "missing_data_type": f"direct {task.signal_type} samples",
                "reason": (
                    "No accessible public comment-level source was available "
                    "through configured providers."
                ),
                "recommended_access": [
                    "authorized browser sample",
                    "platform analytics export",
                    "manual sample collection",
                    "authorized social listening provider",
                ],
            })

    # Determine status
    if not tasks:
        status = "no_tasks"
    elif not samples:
        status = "no_samples"
    elif len(signals_found) < len(tasks):
        status = "partial"
    else:
        status = "complete"

    return {
        "status": status,
        "target_markets": target_markets,
        "languages": languages,
        "tasks_generated": len(tasks),
        "tasks_with_samples": len(sampled_task_ids),
        "signals_found": signals_found,
        "data_gaps": data_gaps,
    }
