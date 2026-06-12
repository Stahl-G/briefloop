"""Filing Resolver provider — integrates disclosure-filing-resolver for SEC filings and XBRL data."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery, _utc_now_iso

# Key XBRL concepts to extract from Inline XBRL when enrichment provider returns 0 observations.
# Maps us-gaap tag names to human-readable category labels.
_KEY_XBRL_TAGS: dict[str, str] = {
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "us-gaap:Revenues": "revenue",
    "us-gaap:SalesRevenueNet": "revenue",
    "us-gaap:NetIncomeLoss": "net_income",
    "us-gaap:GrossProfit": "gross_profit",
    "us-gaap:OperatingIncomeLoss": "operating_income",
    "us-gaap:EarningsPerShareBasic": "eps_basic",
    "us-gaap:EarningsPerShareDiluted": "eps_diluted",
    "us-gaap:CashAndCashEquivalentsAtCarryingValue": "cash",
    "us-gaap:Assets": "total_assets",
    "us-gaap:Liabilities": "total_liabilities",
    "us-gaap:StockholdersEquity": "stockholders_equity",
    "us-gaap:NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "us-gaap:NetCashProvidedByUsedInInvestingActivities": "investing_cash_flow",
    "us-gaap:NetCashProvidedByUsedInFinancingActivities": "financing_cash_flow",
}


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
            normalized = _normalize_ticker_entry(entry)
            if normalized is None:
                errors.append(
                    f"filing_resolver: tickers[{i}] must be a mapping or a non-empty string."
                )
                continue
            if not any(normalized.get(k) for k in ("ticker", "company_name", "cik")):
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
            normalized = _normalize_ticker_entry(entry)
            if normalized is None:
                items.append(self._error_item(
                    "Invalid filing_resolver ticker entry",
                    "tickers entries must be mappings or non-empty strings.",
                ))
                continue
            try:
                items.extend(self._resolve_one(
                    normalized, include_xbrl=include_xbrl, download=download, out_dir=out_dir,
                ))
            except Exception as exc:
                items.append(self._error_item(
                    f"Failed to resolve {normalized.get('ticker', normalized.get('company_name', '?'))}",
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

        # Add XBRL observations as separate structured items.
        # First try the enrichment provider's observations; if empty,
        # fall back to extracting iXBRL facts from downloaded HTML files.
        if include_xbrl:
            entity_name = evidence.entity.legal_name
            if evidence.observations:
                for obs in evidence.observations:
                    items.append(self._observation_to_item(obs, entity_name, now))
            elif out_dir:
                ixbrl_items = self._extract_ixbrl_from_html(out_dir, entity_name, now)
                items.extend(ixbrl_items)

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

    def _extract_ixbrl_from_html(
        self, out_dir: str, entity_name: str, now: str,
    ) -> list[SourceItem]:
        """Extract key financial facts from Inline XBRL tags in downloaded HTML files.

        This is a fallback when the enrichment provider returns 0 observations.
        It reads HTML files from the output directory, parses ix:nonFraction tags,
        and extracts the most important financial metrics.
        """
        items: list[SourceItem] = []
        html_dir = Path(out_dir)
        if not html_dir.is_dir():
            return items

        # Parse all HTML files for iXBRL facts
        facts: dict[str, dict[str, Any]] = {}  # key: "tag_name|contextRef" → fact
        contexts: dict[str, dict[str, str]] = {}  # contextRef → date info

        for html_file in html_dir.glob("*.htm*"):
            try:
                html_text = html_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # Skip files without XBRL markers
            if "ix:nonfraction" not in html_text.lower() and "ix:nonNumeric" not in html_text.lower():
                continue

            # Extract context date info
            for ctx_match in re.finditer(
                r'<(?:xbrli:)?context\s+id="([^"]+)">(.*?)</(?:xbrli:)?context>',
                html_text, re.DOTALL | re.IGNORECASE,
            ):
                ctx_id = ctx_match.group(1)
                ctx_body = ctx_match.group(2)
                start = re.search(r'<(?:xbrli:)?startDate>([^<]+)<', ctx_body)
                end = re.search(r'<(?:xbrli:)?endDate>([^<]+)<', ctx_body)
                instant = re.search(r'<(?:xbrli:)?instant>([^<]+)<', ctx_body)
                contexts[ctx_id] = {
                    "start": start.group(1) if start else "",
                    "end": end.group(1) if end else "",
                    "instant": instant.group(1) if instant else "",
                }

            # Extract ix:nonFraction tags (opening tag + value + closing tag)
            for tag_match in re.finditer(
                r'<ix:nonFraction\s+([^>]+)>([^<]*)</ix:nonFraction>',
                html_text, re.IGNORECASE,
            ):
                attrs_str = tag_match.group(1)
                val_text = tag_match.group(2).strip()

                name_m = re.search(r'name="([^"]+)"', attrs_str)
                ctx_m = re.search(r'contextRef="([^"]+)"', attrs_str)
                scale_m = re.search(r'scale="([^"]+)"', attrs_str)
                sign_m = re.search(r'sign="([^"]+)"', attrs_str)
                unit_m = re.search(r'unitRef="([^"]+)"', attrs_str)

                if not name_m or not ctx_m:
                    continue

                raw_name = name_m.group(1)
                tag_name_lower = raw_name.lower()
                if tag_name_lower not in _KEY_XBRL_TAGS:
                    continue

                ctx_ref = ctx_m.group(1)

                if not val_text:
                    continue

                try:
                    value = float(val_text.replace(",", "").replace("(", "-").replace(")", ""))
                except ValueError:
                    continue

                scale = scale_m.group(1) if scale_m else "0"
                if scale and scale != "0":
                    value *= 10 ** int(scale)
                if sign_m and sign_m.group(1) == "-":
                    value = -value

                fact_key = f"{tag_name_lower}|{ctx_ref}"
                if fact_key not in facts:
                    facts[fact_key] = {
                        "tag": raw_name,
                        "category": _KEY_XBRL_TAGS[tag_name_lower],
                        "value": value,
                        "unit": unit_m.group(1) if unit_m else "",
                        "context_ref": ctx_ref,
                        "source_file": html_file.name,
                    }

        # Convert facts to SourceItems
        for fact in facts.values():
            ctx = contexts.get(fact["context_ref"], {})
            period = ctx.get("end") or ctx.get("instant") or ""
            value_str = self._format_value(fact["value"], fact.get("unit"))

            statement = f"{entity_name} reported {fact['category']} of {value_str}"
            if period:
                statement += f" for period ending {period}"

            source_id = self._make_id(f"ixbrl:{fact['category']}", fact["context_ref"])
            items.append(SourceItem(
                source_id=source_id,
                source_name=f"SEC iXBRL {fact['category']}",
                source_type="filing_resolver",
                title=f"{entity_name} — iXBRL: {fact['category']} = {value_str} ({period})",
                content=statement,
                url="",
                published_at=period,
                retrieved_at=now,
                reliability="high",
                dedupe_key=source_id,
                metadata={
                    "source_tier": "T1",
                    "claim_type": "number",
                    "ixbrl_tag": fact["tag"],
                    "ixbrl_category": fact["category"],
                    "ixbrl_value": fact["value"],
                    "ixbrl_unit": fact.get("unit", ""),
                    "ixbrl_period": period,
                    "ixbrl_context_ref": fact["context_ref"],
                    "source_file": fact["source_file"],
                    "extraction_method": "inline_xbrl_fallback",
                },
            ))

        return items

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


def _normalize_ticker_entry(entry: Any) -> dict[str, Any] | None:
    """Return canonical filing resolver ticker config.

    Older source-decider output used bare strings as a shorthand. Keep accepting
    that shape at read time, but writer paths should emit mappings.
    """
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, str):
        value = entry.strip()
        if not value:
            return None
        if _looks_like_ticker(value):
            return {"ticker": value}
        return {"company_name": value}
    return None


def _looks_like_ticker(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,9}", value))
