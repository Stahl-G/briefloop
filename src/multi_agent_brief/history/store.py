"""HistoryStore - file-backed storage for previous briefs and claim ledgers.

This module provides a minimal HistoryStore for loading and storing previous
briefs, claim ledgers, and related artifacts. It supports repeat/novelty
checks only — it does NOT provide vector search, model-based memory, or
any mechanism that would turn historical claims into current facts.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HistoryReport:
    """Summary of loaded history and repeat/novelty counts."""

    previous_brief_loaded: bool = False
    previous_ledger_loaded: bool = False
    previous_source_map_loaded: bool = False
    entity_history_loaded: bool = False
    manifest_history_loaded: bool = False

    previous_brief_hash: str = ""
    previous_ledger_hash: str = ""
    previous_source_map_hash: str = ""

    total_previous_claims: int = 0
    repeat_claims: int = 0
    novel_claims: int = 0

    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_brief_loaded": self.previous_brief_loaded,
            "previous_ledger_loaded": self.previous_ledger_loaded,
            "previous_source_map_loaded": self.previous_source_map_loaded,
            "entity_history_loaded": self.entity_history_loaded,
            "manifest_history_loaded": self.manifest_history_loaded,
            "previous_brief_hash": self.previous_brief_hash,
            "previous_ledger_hash": self.previous_ledger_hash,
            "previous_source_map_hash": self.previous_source_map_hash,
            "total_previous_claims": self.total_previous_claims,
            "repeat_claims": self.repeat_claims,
            "novel_claims": self.novel_claims,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


@dataclass
class HistoryStore:
    """File-backed storage for previous briefs and claim ledgers.

    This store loads historical artifacts from configured paths and provides
    repeat/novelty metadata for claim selection. It does NOT provide:
    - Vector search
    - Model-based memory
    - Automatic fact retrieval from history

    Historical claims are NEVER treated as current-period facts unless
    they are supported by current sources.
    """

    workspace_path: Path
    previous_brief_path: Path | None = None
    previous_ledger_path: Path | None = None
    previous_source_map_path: Path | None = None
    entity_history_path: Path | None = None
    manifest_history_path: Path | None = None

    # Loaded artifacts
    previous_brief: str = ""
    previous_ledger: dict[str, Any] = field(default_factory=dict)
    previous_source_map: str = ""
    entity_history: list[dict[str, Any]] = field(default_factory=list)
    manifest_history: list[dict[str, Any]] = field(default_factory=list)

    # Repeat/novelty tracking
    previous_claim_ids: set[str] = field(default_factory=set)
    current_claim_ids: set[str] = field(default_factory=set)

    def load(self) -> HistoryReport:
        """Load history from configured paths.

        Returns:
            HistoryReport summarizing what was loaded and any warnings.
        """
        report = HistoryReport()

        # Load previous brief
        if self.previous_brief_path and self.previous_brief_path.exists():
            try:
                self.previous_brief = self.previous_brief_path.read_text(encoding="utf-8")
                report.previous_brief_loaded = True
                report.previous_brief_hash = self._compute_hash(self.previous_brief)
            except Exception as e:
                report.warnings.append(f"Failed to load previous brief: {e}")
        elif self.previous_brief_path:
            report.warnings.append(f"Previous brief not found: {self.previous_brief_path}")

        # Load previous ledger
        if self.previous_ledger_path and self.previous_ledger_path.exists():
            try:
                ledger_text = self.previous_ledger_path.read_text(encoding="utf-8")
                self.previous_ledger = json.loads(ledger_text)
                report.previous_ledger_loaded = True
                report.previous_ledger_hash = self._compute_hash(ledger_text)

                # Extract claim IDs for repeat/novelty tracking
                claims = self.previous_ledger.get("claims", [])
                self.previous_claim_ids = {
                    c.get("claim_id", "") for c in claims if c.get("claim_id")
                }
                report.total_previous_claims = len(self.previous_claim_ids)
            except Exception as e:
                report.warnings.append(f"Failed to load previous ledger: {e}")
        elif self.previous_ledger_path:
            report.warnings.append(f"Previous ledger not found: {self.previous_ledger_path}")

        # Load previous source map
        if self.previous_source_map_path and self.previous_source_map_path.exists():
            try:
                self.previous_source_map = self.previous_source_map_path.read_text(encoding="utf-8")
                report.previous_source_map_loaded = True
                report.previous_source_map_hash = self._compute_hash(self.previous_source_map)
            except Exception as e:
                report.warnings.append(f"Failed to load previous source map: {e}")
        elif self.previous_source_map_path:
            report.warnings.append(f"Previous source map not found: {self.previous_source_map_path}")

        # Load entity history
        if self.entity_history_path and self.entity_history_path.exists():
            try:
                self.entity_history = self._load_jsonl(self.entity_history_path)
                report.entity_history_loaded = True
            except Exception as e:
                report.warnings.append(f"Failed to load entity history: {e}")
        elif self.entity_history_path:
            report.warnings.append(f"Entity history not found: {self.entity_history_path}")

        # Load manifest history
        if self.manifest_history_path and self.manifest_history_path.exists():
            try:
                self.manifest_history = self._load_jsonl(self.manifest_history_path)
                report.manifest_history_loaded = True
            except Exception as e:
                report.warnings.append(f"Failed to load manifest history: {e}")
        elif self.manifest_history_path:
            report.warnings.append(f"Manifest history not found: {self.manifest_history_path}")

        return report

    def mark_current_claims(self, current_claim_ids: set[str]) -> None:
        """Mark current-period claim IDs for repeat/novelty tracking.

        Args:
            current_claim_ids: Set of claim IDs from the current period.
        """
        self.current_claim_ids = current_claim_ids

    def get_repeat_claims(self) -> set[str]:
        """Get claim IDs that appear in both previous and current periods.

        Returns:
            Set of claim IDs that are repeats from previous period.
        """
        return self.previous_claim_ids & self.current_claim_ids

    def get_novel_claims(self) -> set[str]:
        """Get claim IDs that are new in the current period.

        Returns:
            Set of claim IDs that are novel (not in previous period).
        """
        return self.current_claim_ids - self.previous_claim_ids

    def update_report_with_counts(self, report: HistoryReport) -> HistoryReport:
        """Update report with repeat/novelty counts.

        Args:
            report: The history report to update.

        Returns:
            Updated report with repeat/novelty counts.
        """
        report.repeat_claims = len(self.get_repeat_claims())
        report.novel_claims = len(self.get_novel_claims())
        return report

    def get_previous_brief(self) -> str:
        """Get the previous brief markdown.

        Returns:
            Previous brief markdown or empty string if not loaded.
        """
        return self.previous_brief

    def get_previous_ledger(self) -> dict[str, Any]:
        """Get the previous claim ledger.

        Returns:
            Previous ledger dict or empty dict if not loaded.
        """
        return self.previous_ledger

    def get_previous_source_map(self) -> str:
        """Get the previous source map.

        Returns:
            Previous source map markdown or empty string if not loaded.
        """
        return self.previous_source_map

    def get_entity_history(self) -> list[dict[str, Any]]:
        """Get entity history.

        Returns:
            List of entity history records.
        """
        return self.entity_history

    def get_manifest_history(self) -> list[dict[str, Any]]:
        """Get manifest history.

        Returns:
            List of manifest history records.
        """
        return self.manifest_history

    def save_history_report(self, output_path: Path, report: HistoryReport) -> None:
        """Save history report to JSON file.

        Args:
            output_path: Path to write the report.
            report: The history report to save.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _compute_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content.

        Args:
            content: Content to hash.

        Returns:
            Hex digest of SHA-256 hash.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """Load JSONL file.

        Args:
            path: Path to JSONL file.

        Returns:
            List of parsed JSON objects.
        """
        records = []
        for line_num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON at {path}:{line_num}: {e}")
        return records
