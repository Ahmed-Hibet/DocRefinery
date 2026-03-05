"""
ExtractionRouter: strategy pattern with confidence-gated escalation.
Selects Fast Text / Layout / Vision from DocumentProfile and escalates when confidence is low.
"""

from __future__ import annotations

import time
from pathlib import Path

from src.models import DocumentProfile, EstimatedCost, ExtractedDocument
from src.models.ledger import ExtractionLedgerEntry
from src.strategies.fast_text import FastTextExtractor
from src.strategies.layout import LayoutExtractor
from src.strategies.vision import VisionExtractor


# Default confidence threshold below which we escalate (Strategy A -> B, or B -> C)
DEFAULT_ESCALATION_THRESHOLD = 0.6


class ExtractionRouter:
    """
    Routes to the appropriate extractor based on DocumentProfile.
    Implements escalation guard: if Strategy A returns low confidence, retry with B (then C if needed).
    """

    def __init__(
        self,
        escalation_threshold: float = DEFAULT_ESCALATION_THRESHOLD,
        ledger_path: Path | None = None,
    ):
        self.escalation_threshold = escalation_threshold
        self.ledger_path = Path(ledger_path) if ledger_path else Path(".refinery/extraction_ledger.jsonl")
        self.fast_text = FastTextExtractor()
        self.layout = LayoutExtractor()
        self.vision = VisionExtractor()

    def _strategy_for_profile(self, profile: DocumentProfile) -> str:
        """Decide which strategy to try first from profile."""
        if profile.estimated_extraction_cost == EstimatedCost.NEEDS_VISION_MODEL:
            return "vision"
        if profile.estimated_extraction_cost == EstimatedCost.NEEDS_LAYOUT_MODEL:
            return "layout"
        return "fast_text"

    def _get_extractor(self, strategy: str):
        if strategy == "fast_text":
            return self.fast_text
        if strategy == "layout":
            return self.layout
        return self.vision

    def _append_ledger(self, entry: ExtractionLedgerEntry) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def extract(
        self,
        path: Path,
        profile: DocumentProfile,
    ) -> tuple[ExtractedDocument, str, float, float]:
        """
        Run extraction with escalation. Returns (doc, strategy_used, confidence, cost_estimate).
        """
        path = Path(path)
        strategy = self._strategy_for_profile(profile)
        escalated_from: str | None = None
        total_time = 0.0
        cost_estimate = 0.0

        # Try primary strategy
        extractor = self._get_extractor(strategy)
        if not extractor.can_handle(profile):
            if strategy == "fast_text":
                strategy = "layout"
                extractor = self.layout
            elif strategy == "layout":
                strategy = "vision"
                extractor = self.vision

        t0 = time.perf_counter()
        doc, confidence = extractor.extract(path, profile)
        total_time += time.perf_counter() - t0

        # Cost estimates (relative; can be refined with actual API costs)
        if strategy == "fast_text":
            cost_estimate = 0.0
        elif strategy == "layout":
            cost_estimate = 0.01
        else:
            cost_estimate = 0.05

        # Escalation guard: low confidence -> retry with next tier
        if strategy == "fast_text" and confidence < self.escalation_threshold:
            escalated_from = "fast_text"
            strategy = "layout"
            extractor = self.layout
            t0 = time.perf_counter()
            doc, confidence = extractor.extract(path, profile)
            total_time += time.perf_counter() - t0
            cost_estimate = 0.01
        if strategy == "layout" and confidence < self.escalation_threshold:
            escalated_from = escalated_from or "layout"
            strategy = "vision"
            extractor = self.vision
            t0 = time.perf_counter()
            doc, confidence = extractor.extract(path, profile)
            total_time += time.perf_counter() - t0
            cost_estimate = 0.05

        page_count = len(doc.pages) if doc.pages else 0
        entry = ExtractionLedgerEntry(
            doc_id=profile.doc_id,
            strategy_used=strategy,
            confidence_score=round(confidence, 4),
            cost_estimate=cost_estimate,
            processing_time_seconds=round(total_time, 2),
            page_count=page_count,
            escalated_from=escalated_from,
        )
        self._append_ledger(entry)

        return doc, strategy, confidence, cost_estimate
