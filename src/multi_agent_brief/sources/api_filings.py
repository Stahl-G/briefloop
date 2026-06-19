"""Financial filings source provider using SEC EDGAR public APIs.

Uses two RESTful endpoints from data.sec.gov:
  - company_tickers.json  — ticker/company → CIK lookup
  - submissions/CIK{cik}.json — recent filings for a company

No API key required (public data), but a User-Agent header is mandatory.
Rate limit: 10 requests/second.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from hashlib import sha1
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik_str}.json"
DEFAULT_USER_AGENT = "multi-agent-brief-workflow/0.1.0 (github.com/Stahl-G/briefloop)"
DEFAULT_FORM_TYPES = ("10-K", "10-Q", "8-K")

# In-memory cache for company tickers
_ticker_cache: dict[str, dict[str, Any]] | None = None
_ticker_cache_time: float = 0
_TICKER_CACHE_TTL = 3600  # 1 hour


def _load_company_tickers() -> dict[str, dict[str, Any]]:
    """Load and index company tickers by both ticker and company name.

    Returns dict keyed by lowercase ticker: {cik_str, ticker, title}.
    """
    global _ticker_cache, _ticker_cache_time  # noqa: PLW0603
    now = time.time()
    if _ticker_cache is not None and now - _ticker_cache_time < _TICKER_CACHE_TTL:
        return _ticker_cache

    req = urllib.request.Request(
        COMPANY_TICKERS_URL,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        _ticker_cache = {}
        return _ticker_cache

    index: dict[str, dict[str, Any]] = {}
    for entry in raw.values():
        ticker = (entry.get("ticker") or "").strip().lower()
        title = (entry.get("title") or "").strip().lower()
        if ticker:
            index[ticker] = entry
        if title:
            index[title] = entry
    _ticker_cache = index
    _ticker_cache_time = now
    return index


def _lookup_cik(keywords: list[str]) -> list[int]:
    """Match keywords against ticker/company index, return list of CIK integers."""
    index = _load_company_tickers()
    matched: set[int] = set()
    for kw in keywords:
        kw_lower = kw.strip().lower()
        if kw_lower in index:
            cik = int(index[kw_lower]["cik_str"])
            matched.add(cik)
    return list(matched)


def _pad_cik(cik: int) -> str:
    """Zero-pad CIK to 10 digits for the SEC API."""
    return f"{cik:010d}"


def _fetch_filings(cik: int, user_agent: str) -> list[dict[str, Any]]:
    """Fetch recent filings for a CIK, return list of filing dicts."""
    cik_str = _pad_cik(cik)
    url = SUBMISSIONS_URL_TEMPLATE.format(cik_str=cik_str)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    # The arrays are parallel; index i across all arrays is one filing
    accession_numbers = recent.get("accessionNumber", []) or []
    filing_dates = recent.get("filingDate", []) or []
    report_dates = recent.get("reportDate", []) or []
    form_types = recent.get("formType", []) or []
    primary_docs = recent.get("primaryDocument", []) or []
    primary_descs = recent.get("primaryDocDescription", []) or []

    filings: list[dict[str, Any]] = []
    for i in range(len(accession_numbers)):
        filings.append({
            "accession_number": accession_numbers[i] if i < len(accession_numbers) else "",
            "filing_date": filing_dates[i] if i < len(filing_dates) else "",
            "report_date": report_dates[i] if i < len(report_dates) else "",
            "form_type": form_types[i] if i < len(form_types) else "",
            "primary_document": primary_docs[i] if i < len(primary_docs) else "",
            "primary_doc_description": primary_descs[i] if i < len(primary_descs) else "",
            "cik": cik,
            "cik_str": cik_str,
        })
    return filings


class FilingsProvider(SourceProvider):
    """SEC EDGAR filings provider.

    Configuration (in sources.yaml under ``api:``):

    .. code-block:: yaml

        api:
          enabled: true
          providers:
            - name: sec
              user_agent: "YourCompany admin@example.com"
              form_types: ["10-K", "10-Q", "8-K"]
              max_filings: 20
              ticker_cache_ttl: 3600
    """

    name = "api_filings"
    source_type = "filings"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        providers = config.get("providers", [])
        if not providers:
            errors.append("filings: Filings provider is enabled but no providers configured")
            return errors
        for i, provider in enumerate(providers):
            name = provider.get("name", "?")
            ua = provider.get("user_agent", "")
            if not ua:
                errors.append(f"api.providers[{i}] '{name}': missing 'user_agent' (SEC requires it)")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        keywords = query.keywords or []
        if not keywords:
            return []

        # Parse provider config
        user_agent = DEFAULT_USER_AGENT
        form_types: tuple[str, ...] = DEFAULT_FORM_TYPES
        max_filings = 20
        providers = config.get("providers", [])
        for provider in providers:
            if provider.get("name") == "sec":
                user_agent = provider.get("user_agent", DEFAULT_USER_AGENT)
                ft = provider.get("form_types")
                if ft:
                    form_types = tuple(ft)
                max_filings = provider.get("max_filings", 20)
                break

        # Look up CIKs from keywords
        ciks = _lookup_cik(keywords)
        if not ciks:
            return []

        items: list[SourceItem] = []
        for cik in ciks:
            try:
                filings = _fetch_filings(cik, user_agent)
            except Exception:
                # Registry catches provider exceptions
                continue
            # Rate-limit: max 10 req/s
            time.sleep(0.1)

            for filing in filings:
                form_type = filing["form_type"]
                if form_type not in form_types:
                    continue

                company_name = filing.get("company_name", f"CIK{cik}")
                title = f"{form_type}: {company_name}"
                # Build URL to the filing
                acc_no = filing["accession_number"].replace("-", "")
                filing_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no}/"
                    f"{filing['accession_number']}-index.html"
                )
                content = filing["primary_doc_description"] or f"{form_type} filing"
                dedupe_key = f"sec_{cik}_{filing['accession_number']}_{form_type}"

                items.append(
                    SourceItem(
                        source_id=f"filings_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                        source_name=f"SEC EDGAR - {form_type}",
                        source_type="filings",
                        title=title,
                        content=content,
                        url=filing_url,
                        published_at=filing["filing_date"],
                        retrieved_at="",
                        language="en",
                        reliability="high",
                        dedupe_key=dedupe_key,
                        metadata={
                            "backend": "sec_edgar",
                            "cik": cik,
                            "form_type": form_type,
                            "filing_date": filing["filing_date"],
                            "report_date": filing["report_date"],
                            "accession_number": filing["accession_number"],
                        },
                    )
                )

            if len(items) >= max_filings:
                break

        return items[:max_filings]
