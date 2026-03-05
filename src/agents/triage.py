"""
Triage Agent: Document classifier that produces DocumentProfile.
Governs which extraction strategy downstream stages use.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.config import get_extraction_config, load_config
from src.models import (
    DocumentProfile,
    DomainHint,
    EstimatedCost,
    LayoutComplexity,
    OriginType,
)
from src.utils.pdf_analysis import PageStats, get_document_page_stats


def _doc_id_from_path(path: Path) -> str:
    """Stable doc_id from file path (e.g. for .refinery/profiles/{doc_id}.json)."""
    name = path.name
    h = hashlib.sha256(path.resolve().as_posix().encode()).hexdigest()[:16]
    return f"{path.stem}_{h}"


def _classify_origin_type(
    page_stats_list: list[PageStats],
    min_chars_for_digital: int = 100,
    max_image_ratio_for_digital: float = 0.5,
) -> OriginType:
    """
    Classify origin: native_digital vs scanned_image vs mixed.
    Uses character density, image area ratio, and font metadata.
    Thresholds from config (extraction.fast_text) when run_triage receives config.
    """
    if not page_stats_list:
        return OriginType.SCANNED_IMAGE

    digital_signals = 0
    scanned_signals = 0
    total_pages = len(page_stats_list)

    for s in page_stats_list:
        # Native digital: meaningful char stream, fonts, low image area
        has_chars = s.char_count > min_chars_for_digital
        low_image = s.image_area_ratio < max_image_ratio_for_digital
        has_fonts = s.has_font_metadata

        if has_chars and (low_image or has_fonts):
            digital_signals += 1
        elif s.char_count < 50 and s.image_area_ratio > 0.3:
            scanned_signals += 1
        elif s.char_count < 20:
            scanned_signals += 1

    digital_ratio = digital_signals / total_pages
    scanned_ratio = scanned_signals / total_pages

    if digital_ratio >= 0.8:
        return OriginType.NATIVE_DIGITAL
    if scanned_ratio >= 0.8:
        return OriginType.SCANNED_IMAGE
    return OriginType.MIXED


def _classify_layout_complexity(page_stats_list: list[PageStats]) -> LayoutComplexity:
    """
    Layout complexity: single_column vs multi_column vs table_heavy vs figure_heavy vs mixed.
    Heuristics: table count, image area, curve count (structure).
    """
    if not page_stats_list:
        return LayoutComplexity.SINGLE_COLUMN

    total_tables = sum(s.table_count for s in page_stats_list)
    total_pages = len(page_stats_list)
    avg_tables = total_tables / total_pages
    avg_image_ratio = sum(s.image_area_ratio for s in page_stats_list) / total_pages
    avg_curves = sum(s.curve_count for s in page_stats_list) / total_pages

    table_heavy = avg_tables >= 1.5
    figure_heavy = avg_image_ratio >= 0.35 and avg_tables < 0.5

    if table_heavy and figure_heavy:
        return LayoutComplexity.MIXED
    if table_heavy:
        return LayoutComplexity.TABLE_HEAVY
    if figure_heavy:
        return LayoutComplexity.FIGURE_HEAVY
    # Multi-column is hard without layout model; use tables/curves as proxy for complexity
    if avg_tables >= 0.5 or avg_curves > 5:
        return LayoutComplexity.MULTI_COLUMN
    return LayoutComplexity.SINGLE_COLUMN


# Domain keywords (pluggable: can swap for VLM later)
DOMAIN_KEYWORDS: dict[DomainHint, list[str]] = {
    DomainHint.FINANCIAL: [
        "revenue", "balance sheet", "income statement", "fiscal", "audit",
        "financial statements", "assets", "liabilities", "equity", "expenditure",
        "tax", "ministry of finance", "annual report", "auditor",
    ],
    DomainHint.LEGAL: [
        "whereas", "hereby", "hereinafter", "pursuant", "jurisdiction",
        "plaintiff", "defendant", "court", "legal", "agreement", "contract",
    ],
    DomainHint.TECHNICAL: [
        "implementation", "assessment", "methodology", "framework", "api",
        "system", "technical", "specification", "architecture",
    ],
    DomainHint.MEDICAL: [
        "patient", "clinical", "diagnosis", "treatment", "medical",
        "health", "therapy", "drug", "symptom",
    ],
}


def _classify_domain_hint(path: Path, sample_text: str) -> DomainHint:
    """
    Simple keyword-based domain hint from filename and optional text sample.
    Pluggable: replace with VLM classifier for production.
    """
    combined = f"{path.stem} {path.name} {sample_text}".lower()
    scores: dict[DomainHint, int] = {
        d: 0 for d in DomainHint if d is not DomainHint.GENERAL
    }

    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                scores[domain] += 1

    best = max(scores, key=scores.get)  # type: ignore
    return best if scores[best] > 0 else DomainHint.GENERAL


def _estimate_extraction_cost(
    origin: OriginType,
    layout: LayoutComplexity,
) -> EstimatedCost:
    """Map profile to cost tier: fast_text_sufficient | needs_layout_model | needs_vision_model."""
    if origin == OriginType.SCANNED_IMAGE:
        return EstimatedCost.NEEDS_VISION_MODEL
    if origin == OriginType.MIXED:
        return EstimatedCost.NEEDS_LAYOUT_MODEL
    if layout in (LayoutComplexity.TABLE_HEAVY, LayoutComplexity.MULTI_COLUMN, LayoutComplexity.MIXED):
        return EstimatedCost.NEEDS_LAYOUT_MODEL
    if layout == LayoutComplexity.FIGURE_HEAVY:
        return EstimatedCost.NEEDS_LAYOUT_MODEL
    return EstimatedCost.FAST_TEXT_SUFFICIENT


def run_triage(path: Path, config: dict[str, Any] | None = None) -> DocumentProfile:
    """
    Run the Triage Agent on a document path.
    Returns a DocumentProfile that governs extraction strategy selection.
    When config is provided (e.g. from load_config()), origin-detection thresholds
    use extraction.fast_text.min_chars_per_page and max_image_area_ratio.
    """
    path = Path(path)
    doc_id = _doc_id_from_path(path)
    page_stats_list = get_document_page_stats(path)

    ft = get_extraction_config(config or load_config()).get("fast_text", {})
    min_chars = int(ft.get("min_chars_per_page", 100))
    max_image = float(ft.get("max_image_area_ratio", 0.5))
    origin_type = _classify_origin_type(
        page_stats_list,
        min_chars_for_digital=min_chars,
        max_image_ratio_for_digital=max_image,
    )
    layout_complexity = _classify_layout_complexity(page_stats_list)

    # Sample text from first few pages for domain hint (optional: could use fast text extract)
    sample_text = ""
    if page_stats_list:
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for i in range(min(3, len(pdf.pages))):
                    p = pdf.pages[i]
                    sample_text += (p.extract_text() or "") + " "
        except Exception:
            pass
    domain_hint = _classify_domain_hint(path, sample_text)
    estimated_cost = _estimate_extraction_cost(origin_type, layout_complexity)

    return DocumentProfile(
        doc_id=doc_id,
        origin_type=origin_type,
        layout_complexity=layout_complexity,
        language="en",
        language_confidence=0.9,
        domain_hint=domain_hint,
        estimated_extraction_cost=estimated_cost,
        metadata={
            "source_path": str(path.resolve()),
            "page_count": len(page_stats_list),
            "filename": path.name,
        },
    )
