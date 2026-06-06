"""Build AnalysisBlocks from a ClaimLedger.

Classification rules (from v0.5.3 PR 1 acceptance criteria):
- observed + direct        → Fact
- analogous                → Case
- hypothesis / interpreted → Interpretation
- action + direct evidence → Action
- action without evidence  → To Verify (downgraded)
- hypothesis               → also To Verify (pending validation)
- any claim with limitations → Limitation
"""
from __future__ import annotations

from collections import defaultdict

from multi_agent_brief.analysis_blocks.schemas import AnalysisBlock, CaseApplicability
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def build_analysis_blocks(ledger: ClaimLedger) -> list[AnalysisBlock]:
    """Group ledger claims into AnalysisBlocks by topic.

    Each topic becomes one block. Claims are classified into epistemic
    buckets based on their epistemic_type and evidence_relation.
    """
    # Group claims by topic
    by_topic: dict[str, list[Claim]] = defaultdict(list)
    for claim in ledger:
        topic = claim.metadata.get("topic") or "general"
        by_topic[topic].append(claim)

    blocks: list[AnalysisBlock] = []
    for topic, claims in by_topic.items():
        block = _build_block(topic, claims)
        blocks.append(block)

    return blocks


def _build_block(topic: str, claims: list[Claim]) -> AnalysisBlock:
    block_id = topic.replace(" ", "_").lower()
    block = AnalysisBlock(block_id=block_id, title=topic.replace("_", " ").title())

    for claim in claims:
        _classify_claim(block, claim)

    block.confidence = _compute_confidence(block, claims)
    return block


def _classify_claim(block: AnalysisBlock, claim: Claim) -> None:
    """Classify a single claim into the appropriate epistemic bucket."""
    ep_type = claim.epistemic_type
    ev_rel = claim.evidence_relation
    has_evidence = bool(claim.evidence_text.strip())
    has_limitations = bool(claim.limitations)

    # 1. observed + direct → Fact
    if ep_type == "observed" and ev_rel == "direct":
        block.fact_claim_ids.append(claim.claim_id)
        if has_limitations:
            block.limitation_claim_ids.append(claim.claim_id)
        return

    # 2. analogous → Case
    if ev_rel == "analogous":
        block.case_claim_ids.append(claim.claim_id)
        # Populate case_applicability from claim metadata if available
        _populate_case_applicability(block, claim)
        if has_limitations:
            block.limitation_claim_ids.append(claim.claim_id)
        return

    # 3. hypothesis / interpreted → Interpretation
    if ep_type in ("hypothesis", "interpreted"):
        block.interpretation_claim_ids.append(claim.claim_id)
        # hypothesis also goes to To Verify
        if ep_type == "hypothesis":
            block.to_verify_claim_ids.append(claim.claim_id)
        if has_limitations:
            block.limitation_claim_ids.append(claim.claim_id)
        return

    # 4. action — gate on evidence
    if ep_type == "action":
        if has_evidence and ev_rel == "direct":
            block.action_claim_ids.append(claim.claim_id)
        else:
            # Downgrade: action without direct evidence → To Verify
            block.to_verify_claim_ids.append(claim.claim_id)
        if has_limitations:
            block.limitation_claim_ids.append(claim.claim_id)
        return

    # 5. observed + indirect/inferred → Interpretation (not strong enough for Fact)
    if ep_type == "observed" and ev_rel in ("indirect", "inferred"):
        block.interpretation_claim_ids.append(claim.claim_id)
        block.to_verify_claim_ids.append(claim.claim_id)
        if has_limitations:
            block.limitation_claim_ids.append(claim.claim_id)
        return

    # Fallback: anything else goes to Interpretation + To Verify
    block.interpretation_claim_ids.append(claim.claim_id)
    block.to_verify_claim_ids.append(claim.claim_id)
    if has_limitations:
        block.limitation_claim_ids.append(claim.claim_id)


def _populate_case_applicability(block: AnalysisBlock, claim: Claim) -> None:
    """Extract case applicability from claim metadata (PR 3)."""
    meta = claim.metadata
    if not meta:
        return

    comparable = meta.get("comparable_dimensions", [])
    non_comparable = meta.get("non_comparable_dimensions", [])
    market_ctx = meta.get("market_context", "")
    stage_ctx = meta.get("stage_context", "")
    audience_fit = meta.get("price_band_or_audience_fit", "")
    needs_local = meta.get("local_verification_needed", False)
    applicability = claim.applicability_reason or meta.get("applicability_reason", "")

    if any([comparable, non_comparable, market_ctx, stage_ctx, audience_fit, needs_local, applicability]):
        if block.case_applicability is None:
            block.case_applicability = CaseApplicability()
        if comparable:
            block.case_applicability.comparable_dimensions.extend(comparable)
        if non_comparable:
            block.case_applicability.non_comparable_dimensions.extend(non_comparable)
        if market_ctx:
            block.case_applicability.market_context = market_ctx
        if stage_ctx:
            block.case_applicability.stage_context = stage_ctx
        if audience_fit:
            block.case_applicability.price_band_or_audience_fit = audience_fit
        if needs_local:
            block.case_applicability.local_verification_needed = True
        if applicability and not block.applicability_note:
            block.applicability_note = applicability


def _compute_confidence(block: AnalysisBlock, claims: list[Claim]) -> float:
    """Compute block confidence from claim evidence quality."""
    if not claims:
        return 0.0

    score = 0.0
    for claim in claims:
        if claim.epistemic_type == "observed" and claim.evidence_relation == "direct":
            score += 1.0
        elif claim.evidence_relation == "analogous":
            score += 0.4
        elif claim.epistemic_type == "hypothesis":
            score += 0.2
        elif claim.epistemic_type == "interpreted":
            score += 0.5
        else:
            score += 0.3

    return round(min(score / len(claims), 1.0), 2)
