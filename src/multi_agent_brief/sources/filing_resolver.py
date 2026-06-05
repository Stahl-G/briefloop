"""Filing Resolver provider — integrates disclosure-filing-resolver for SEC filings and XBRL data."""
from __future__ import annotations

import hashlib
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery, _utc_now_iso


class FilingResolverProvider(SourceProvider):
    """Collect SEC filings and XBRL facts via disclosure-filing-resolver."""

    name = "filing_resolver"
    source_type = "filing_resolver"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("enabled", False):
            return errors
        tickers = config.get("tickers", [])
        if not tickers:
            errors.append("filing_resolver: 'tickers' list is required when enabled.")
            return errors
        for i, entry in enumerate(tickers):
            if not any(entry.get(k) for k in ("ticker", "company_name", "cik")):
                errors.append(
                    f"filing_resolver: tickers[{i}] must have at least one of "
                    "'ticker', 'company_name', or 'cik'."
                )
        # Check optional dependency
        try:
            import disclosure_filing_resolver  # noqa: F401
        except ImportError:
            errors.append(
                "filing_resolver: 'disclosure-filing-resolver' package is not installed. "
                "Install it with: pip install disclosure-filing-resolver"
            )
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled", False):
            return []

        tickers = config.get("tickers", [])
        if not tickers:
            return []

        include_xbrl = config.get("include_xbrl", False)
        download = config.get("download", True)
        out_dir = config.get("out_dir", "")

        items: list[SourceItem] = []
        for entry in tickers:
            try:
                items.extend(self._resolve_one(
                    entry, include_xbrl=include_xbrl, download=download, out_dir=out_dir,
                ))
            except Exception as exc:
                items.append(self._error_item(
                    f"Failed to resolve {entry.get('ticker', entry.get('company_name', '?'))}",
                    str(exc)[:200],
                ))
        return items

    def _resolve_one(
        self,
        entry: dict[str, Any],
        include_xbrl: bool,
        download: bool,
        out_dir: str,
    ) -> list[SourceItem]:
        from disclosure_filing_resolver import resolve_disclosure, evidence_to_sources

        ticker = entry.get("ticker")
        company_name = entry.get("company_name")
        cik = entry.get("cik")
        intent = entry.get("intent", "quarterly")
        period = entry.get("period", "latest")
        form = entry.get("form")

        evidence = resolve_disclosure(
            ticker=ticker,
            company_name=company_name,
            cik=cik,
            intent=intent,
            period=period,
            form=form,
            download=download,
            out_dir=out_dir or None,
        )

        sources = evidence_to_sources(evidence)
        items: list[SourceItem] = []
        now = _utc_now_iso()

        for src in sources:
            item = self._source_to_item(src, now)
            items.append(item)

        # Add XBRL observations as separate structured items
        if include_xbrl and evidence.observations:
            for obs in evidence.observations:
                items.append(self._observation_to_item(obs, evidence.entity.legal_name, now))

        return items

    def _source_to_item(self, src: dict[str, Any], now: str) -> SourceItem:
        metadata = dict(src.get("metadata", {}))
        metadata["source_tier"] = "T1"  # SEC official = highest tier

        title = src.get("title", "")
        content = src.get("content", "")
        if not content:
            content = f"SEC {metadata.get('form', 'filing')} filing: {title}"

        source_id = self._make_id(src.get("url", ""), metadata.get("filename", ""))

        return SourceItem(
            source_id=source_id,
            source_name=f"SEC EDGAR {metadata.get('form', '')}".strip(),
            source_type="filing_resolver",
            title=title,
            content=content,
            url=src.get("url", ""),
            published_at=src.get("date", ""),
            retrieved_at=now,
            reliability="high",
            dedupe_key=source_id,
            metadata=metadata,
        )

    def _observation_to_item(self, obs: Any, entity_name: str, now: str) -> SourceItem:
        category = obs.category.replace("_", " ")
        value_str = self._format_value(obs.value, obs.unit)
        period = obs.period or "unknown period"
        form = obs.provenance.get("form", "")
        filed = obs.provenance.get("filed", "")

        statement = f"{entity_name} reported {category} of {value_str} for period ending {period}"
        if form:
            statement += f" (SEC {form}"
            if filed:
                statement += f", filed {filed}"
            statement += ")"

        provenance = obs.provenance
        accession = provenance.get("accession", "")
        source_id = self._make_id(f"xbrl:{obs.key}", accession)

        return SourceItem(
            source_id=source_id,
            source_name=f"SEC XBRL {obs.category}",
            source_type="filing_resolver",
            title=f"{entity_name} — XBRL: {category} = {value_str} ({period})",
            content=statement,
            url="",
            published_at=provenance.get("filed", ""),
            retrieved_at=now,
            reliability="high",
            dedupe_key=source_id,
            metadata={
                "source_tier": "T1",
                "claim_type": "number",
                "observation_category": obs.category,
                "observation_key": obs.key,
                "observation_value": obs.value,
                "observation_unit": obs.unit,
                "observation_period": obs.period,
                "taxonomy": provenance.get("taxonomy", ""),
                "form": form,
                "filed": filed,
                "fiscal_year": provenance.get("fiscal_year", ""),
                "fiscal_period": provenance.get("fiscal_period", ""),
            },
        )

    @staticmethod
    def _format_value(value: Any, unit: str | None) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            if abs(value) >= 1_000_000:
                return f"${value / 1_000_000:,.1f}M"
            if abs(value) >= 1_000:
                return f"${value / 1_000:,.1f}K"
            if unit and "shares" in unit.lower():
                return f"${value:.2f}/share"
            return f"${value:,.0f}"
        return str(value)

    @staticmethod
    def _make_id(*parts: str) -> str:
        raw = "|".join(str(p) for p in parts)
        return f"FR_{hashlib.sha1(raw.encode()).hexdigest()[:12]}"

    @staticmethod
    def _error_item(title: str, detail: str) -> SourceItem:
        return SourceItem(
            source_id=f"FR_ERROR_{hashlib.sha1(title.encode()).hexdigest()[:8]}",
            source_name="Filing Resolver",
            source_type="filing_resolver_error",
            title=title,
            content=detail,
            metadata={"error_type": "FilingResolverError"},
        )
