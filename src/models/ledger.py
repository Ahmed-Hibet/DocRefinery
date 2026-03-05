"""Extraction ledger entry schema for .refinery/extraction_ledger.jsonl."""

from pydantic import BaseModel, Field


class ExtractionLedgerEntry(BaseModel):
    """One line in extraction_ledger.jsonl — log every extraction for audit."""

    doc_id: str
    strategy_used: str = Field(..., description="fast_text | layout | vision")
    confidence_score: float = Field(..., ge=0, le=1)
    cost_estimate: float = Field(default=0.0, ge=0, description="Estimated cost in USD or token-equivalent")
    processing_time_seconds: float = Field(default=0.0, ge=0)
    page_count: int = Field(default=0, ge=0)
    escalated_from: str | None = Field(default=None, description="Previous strategy if escalation occurred")
    review_required: bool = Field(
        default=False,
        description="True when final confidence remained below threshold after all strategies; output should be flagged for human review.",
    )
