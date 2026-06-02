from __future__ import annotations

import hashlib
import json
from pathlib import Path

from multi_agent_brief.core.schemas import Claim


class ClaimLedger:
    """In-memory claim store with validation helpers."""

    def __init__(self, claims: list[Claim] | None = None) -> None:
        self._claims: dict[str, Claim] = {}
        for claim in claims or []:
            self.add_claim(claim)

    def __len__(self) -> int:
        return len(self._claims)

    def __iter__(self):
        return iter(self._claims.values())

    @staticmethod
    def build_claim_id(statement: str, source_id: str) -> str:
        digest = hashlib.sha1(f"{source_id}|{statement}".encode("utf-8")).hexdigest()[:10]
        prefix = "".join(ch for ch in source_id.upper() if ch.isalnum())[:8] or "CLAIM"
        return f"{prefix}_{digest.upper()}"

    def add_claim(self, claim: Claim) -> Claim:
        if not claim.claim_id:
            claim.claim_id = self.build_claim_id(claim.statement, claim.source_id)
        self._claims[claim.claim_id] = claim
        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)

    def to_list(self) -> list[dict]:
        return [claim.to_dict() for claim in self._claims.values()]

    def export_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_list(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def import_json(cls, path: str | Path) -> "ClaimLedger":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Claim.from_dict(item) for item in data])

    def detect_missing_sources(self) -> list[Claim]:
        return [
            claim
            for claim in self._claims.values()
            if not claim.source_id or not claim.evidence_text.strip()
        ]

    def detect_duplicate_claims(self) -> list[list[Claim]]:
        buckets: dict[str, list[Claim]] = {}
        for claim in self._claims.values():
            normalized = " ".join(claim.statement.lower().split())
            buckets.setdefault(normalized, []).append(claim)
        return [claims for claims in buckets.values() if len(claims) > 1]

    def validate_claims(self) -> list[str]:
        errors: list[str] = []
        for claim in self._claims.values():
            if not claim.statement.strip():
                errors.append(f"{claim.claim_id}: missing statement")
            if claim.requires_audit and not claim.evidence_text.strip():
                errors.append(f"{claim.claim_id}: missing evidence_text")
            if not claim.source_id.strip():
                errors.append(f"{claim.claim_id}: missing source_id")
        return errors

