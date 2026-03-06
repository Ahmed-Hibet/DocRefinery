"""
Strategy A — Fast Text: pdfplumber/pymupdf.
Low cost; triggers when native_digital + single_column.
Confidence-gated: low confidence triggers escalation.
"""

from __future__ import annotations

from pathlib import Path

from src.models import (
    BoundingBox,
    DocumentProfile,
    ExtractedDocument,
    ExtractedTable,
    LayoutComplexity,
    OriginType,
    TextBlock,
)
from src.utils.pdf_analysis import (
    PageStats,
    bbox_from_pdfplumber,
    get_document_page_stats,
    get_page_stats_from_pdf,
)


# Defaults when config is not provided (should match rubric/extraction_rules.yaml)
DEFAULT_MIN_CHARS_PER_PAGE = 100
DEFAULT_MAX_IMAGE_AREA_RATIO = 0.5
DEFAULT_MIN_CONFIDENCE = 0.6


def _confidence_from_page_stats(
    page_stats_list: list[PageStats],
    min_chars_per_page: int = DEFAULT_MIN_CHARS_PER_PAGE,
    max_image_area_ratio: float = DEFAULT_MAX_IMAGE_AREA_RATIO,
) -> float:
    """
    Multi-signal confidence: character count, density, image ratio, font presence.
    Returns value in [0, 1]. Low => escalate to Layout or Vision.
    Thresholds come from config (extraction_rules.yaml) when wired via ExtractionRouter.
    """
    if not page_stats_list:
        return 0.0

    scores = []
    for s in page_stats_list:
        char_ok = 1.0 if s.char_count >= min_chars_per_page else s.char_count / max(1, min_chars_per_page)
        image_ok = 1.0 - s.image_area_ratio if s.image_area_ratio <= max_image_area_ratio else 0.2
        font_ok = 1.0 if s.has_font_metadata else 0.7
        density_ok = min(1.0, s.char_density / 2.0) if s.page_area else 0.0
        scores.append((char_ok * 0.4 + image_ok * 0.3 + font_ok * 0.15 + density_ok * 0.15))
    return sum(scores) / len(scores)


class FastTextExtractor:
    """Strategy A: pdfplumber-based extraction with confidence scoring."""

    strategy_name = "fast_text"

    def __init__(
        self,
        min_chars_per_page: int = DEFAULT_MIN_CHARS_PER_PAGE,
        max_image_area_ratio: float = DEFAULT_MAX_IMAGE_AREA_RATIO,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ):
        self.min_chars_per_page = min_chars_per_page
        self.max_image_area_ratio = max_image_area_ratio
        self.min_confidence = min_confidence

    def can_handle(self, profile: DocumentProfile) -> bool:
        return (
            profile.origin_type == OriginType.NATIVE_DIGITAL
            and profile.layout_complexity == LayoutComplexity.SINGLE_COLUMN
        )

    def extract(self, path: Path, profile: DocumentProfile) -> tuple[ExtractedDocument, float]:
        import pdfplumber

        path = Path(path)
        page_stats_list = get_document_page_stats(path)
        confidence = _confidence_from_page_stats(
            page_stats_list,
            min_chars_per_page=self.min_chars_per_page,
            max_image_area_ratio=self.max_image_area_ratio,
        )

        text_blocks: list[TextBlock] = []
        tables: list[ExtractedTable] = []
        raw_parts: list[str] = []
        pages: list[int] = []

        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                pnum = i + 1
                pages.append(pnum)
                text = page.extract_text()
                if text:
                    raw_parts.append(text)
                for char in page.chars or []:
                    # Group by lines/blocks via simple bbox; for full blocks use extract_words then merge
                    pass
                # Use words to build text blocks with bbox
                words = page.extract_words() or []
                if words:
                    x0 = min(w["x0"] for w in words)
                    top = min(w["top"] for w in words)
                    x1 = max(w["x1"] for w in words)
                    bottom = max(w["bottom"] for w in words)
                    block_text = " ".join(w.get("text", "") for w in words)
                    text_blocks.append(
                        TextBlock(
                            text=block_text,
                            bbox=BoundingBox(x0=x0, top=top, x1=x1, bottom=bottom, page=pnum),
                        )
                    )
                # Tables
                for tbl in page.find_tables() or []:
                    data = tbl.extract()
                    if data:
                        headers = [str(c) if c is not None else "" for c in data[0]]
                        rows_raw = [row for row in data[1:] if any(c is not None for c in row)]
                        # Normalize cells: None -> "" so ExtractedTable.rows validates (TableCell | str | int | float only)
                        rows = [[(c if isinstance(c, (str, int, float)) else ("" if c is None else str(c))) for c in row] for row in rows_raw]
                        bbox = tbl.bbox
                        if bbox and len(bbox) >= 4:
                            tables.append(
                                ExtractedTable(
                                    headers=headers,
                                    rows=rows,
                                    bbox=BoundingBox(
                                        x0=float(bbox[0]), top=float(bbox[1]), x1=float(bbox[2]), bottom=float(bbox[3]), page=pnum
                                    ),
                                )
                            )
                        else:
                            tables.append(
                                ExtractedTable(headers=headers, rows=rows)
                            )

        raw_text = "\n\n".join(raw_parts)
        doc = ExtractedDocument(
            doc_id=profile.doc_id,
            pages=pages,
            text_blocks=text_blocks,
            tables=tables,
            reading_order=[str(i) for i in range(len(text_blocks))],
            raw_text=raw_text,
        )
        return doc, confidence
