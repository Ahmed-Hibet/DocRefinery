"""
ExtractionRouter: strategy pattern with confidence-gated escalation.
Selects Fast Text / Layout / Vision from DocumentProfile and escalates when confidence is low.
Thresholds and budgets are loaded from config (rubric/extraction_rules.yaml).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.config import get_extraction_config, load_config
from src.models import DocumentProfile, EstimatedCost, ExtractedDocument
from src.models.ledger import ExtractionLedgerEntry
from src.strategies.fast_text import FastTextExtractor
from src.strategies.layout import LayoutExtractor
from src.strategies.vision import VisionExtractor


class ExtractionRouter:
    """
    Routes to the appropriate extractor based on DocumentProfile.
    Implements escalation guard: if Strategy A returns low confidence, retry with B (then C if needed).
    When final confidence remains below threshold after all strategies, sets review_required for audit.
    """

    def __init__(
        self,
        escalation_threshold: float | None = None,
        ledger_path: Path | None = None,
        config: dict[str, Any] | None = None,
    ):
        self._config = config if config is not None else load_config()
        ex = get_extraction_config(self._config)
        self.escalation_threshold = (
            float(escalation_threshold)
            if escalation_threshold is not None
            else float(ex.get("escalation", {}).get("confidence_threshold", 0.6))
        )
        self.ledger_path = Path(ledger_path) if ledger_path else Path(".refinery/extraction_ledger.jsonl")
        ft = ex.get("fast_text", {})
        v = ex.get("vision", {})
        self.fast_text = FastTextExtractor(
            min_chars_per_page=int(ft.get("min_chars_per_page", 100)),
            max_image_area_ratio=float(ft.get("max_image_area_ratio", 0.5)),
            min_confidence=float(ft.get("min_confidence_to_accept", 0.6)),
        )
        self.layout = LayoutExtractor()
        self.vision = VisionExtractor(
            budget_usd_per_doc=float(v.get("budget_usd_per_doc", 0.50)),
            max_pages_per_doc=int(v.get("max_pages_per_doc", 20)),
        )

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
        review_required = confidence < self.escalation_threshold
        entry = ExtractionLedgerEntry(
            doc_id=profile.doc_id,
            strategy_used=strategy,
            confidence_score=round(confidence, 4),
            cost_estimate=cost_estimate,
            processing_time_seconds=round(total_time, 2),
            page_count=page_count,
            escalated_from=escalated_from,
            review_required=review_required,
        )
        self._append_ledger(entry)

        return doc, strategy, confidence, cost_estimate
