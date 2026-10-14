"""Carry only evidence whose complete report block survives a prose revision."""
import re


def invalidate_changed_evidence(detail, before, after, base_version, *, previous_citations=None):
    """Preserve source locations, but do not reuse claim bindings after rewriting.

    The old version remains the record of discarded bindings. Exact paragraph
    identity is a conservative location check, never a semantic fact verdict.
    """
    old_blocks = [block.strip() for block in re.split(r"\n\s*\n", before) if block.strip()]
    new_blocks = {block.strip() for block in re.split(r"\n\s*\n", after) if block.strip()}

    def survives(quote):
        if not isinstance(quote, str) or not quote.strip():
            return False
        if before.count(quote) != 1 or after.count(quote) != 1:
            return False
        return any(quote in block and block in new_blocks for block in old_blocks)

    removed = {}
    for field, quote_field in (("number_bindings", "report_quote"), ("temporal_claims", "statement")):
        records = detail.get(field, [])
        retained = [record for record in records if survives(record.get(quote_field))]
        removed[field] = len(records) - len(retained)
        detail[field] = retained
    count = 0
    for field in ("citations", "content_citations"):
        for ref in detail.get(field, []):
            newly_supplied = previous_citations is not None and ref not in previous_citations
            if ref.get("report_quote") and not newly_supplied and not survives(ref["report_quote"]):
                ref.pop("report_quote")
                if field == "citations":
                    count += 1
    removed["citation_claim_links"] = count
    detail["evidence_needs_review"] = True
    detail["evidence_revision"] = {"base_version": base_version, "invalidated": removed}
