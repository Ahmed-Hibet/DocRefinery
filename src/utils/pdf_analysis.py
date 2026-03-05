"""PDF analysis utilities for triage and confidence scoring (pdfplumber)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.models import BoundingBox


@dataclass
class PageStats:
    """Per-page statistics for triage and confidence."""

    page_num: int
    char_count: int
    word_count: int
    image_area_ratio: float  # 0..1
    has_font_metadata: bool
    width: float
    height: float
    table_count: int = 0
    curve_count: int = 0  # lines/curves as proxy for structure

    @property
    def page_area(self) -> float:
        return self.width * self.height

    @property
    def char_density(self) -> float:
        """Characters per 1000 pt² (points squared)."""
        if self.page_area <= 0:
            return 0.0
        return (self.char_count / self.page_area) * 1000

    @property
    def whitespace_ratio(self) -> float:
        """Approximate: low char density => high whitespace."""
        if self.page_area <= 0:
            return 1.0
        # Heuristic: assume ~6pt² per char; remaining is "whitespace"
        used = self.char_count * 36
        return max(0, 1 - used / self.page_area)


def get_page_stats_from_pdf(path: Path, page_num: int) -> PageStats | None:
    """
    Extract page-level stats using pdfplumber for one page.
    Returns None if page doesn't exist or library unavailable.
    """
    try:
        import pdfplumber
    except ImportError:
        return None

    path = Path(path)
    if not path.exists():
        return None

    with pdfplumber.open(path) as pdf:
        if page_num < 1 or page_num > len(pdf.pages):
            return None
        page = pdf.pages[page_num - 1]
        width = float(page.width or 0)
        height = float(page.height or 0)
        chars = page.chars or []
        words = page.extract_words() or []
        images = page.images or []
        tables = page.find_tables() or []
        curves = page.curves or []

        char_count = len(chars)
        word_count = len(words)
        page_area = width * height
        image_area = 0.0
        for im in images:
            x0, top, x1, bottom = im.get("x0", 0), im.get("top", 0), im.get("x1", 0), im.get("bottom", 0)
            image_area += (x1 - x0) * (bottom - top)
        image_area_ratio = image_area / page_area if page_area > 0 else 0.0
        has_font = any(c.get("fontname") for c in chars) if chars else False

        return PageStats(
            page_num=page_num,
            char_count=char_count,
            word_count=word_count,
            image_area_ratio=min(1.0, image_area_ratio),
            has_font_metadata=has_font,
            width=width,
            height=height,
            table_count=len(tables),
            curve_count=len(curves),
        )
    return None


def get_document_page_stats(path: Path) -> list[PageStats]:
    """Get PageStats for every page in the PDF."""
    try:
        import pdfplumber
    except ImportError:
        return []

    path = Path(path)
    if not path.exists():
        return []

    result: list[PageStats] = []
    with pdfplumber.open(path) as pdf:
        for i in range(len(pdf.pages)):
            stats = get_page_stats_from_pdf(path, i + 1)
            if stats:
                result.append(stats)
    return result


def bbox_from_pdfplumber(rect: dict[str, Any], page: int) -> BoundingBox:
    """Build BoundingBox from pdfplumber rect (x0, top, x1, bottom)."""
    return BoundingBox(
        x0=float(rect.get("x0", 0)),
        top=float(rect.get("top", 0)),
        x1=float(rect.get("x1", 0)),
        bottom=float(rect.get("bottom", 0)),
        page=page,
    )
