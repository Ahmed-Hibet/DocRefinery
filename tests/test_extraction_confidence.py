"""Unit tests for extraction confidence scoring."""

import pytest
from pathlib import Path

from src.strategies.fast_text import FastTextExtractor, _confidence_from_page_stats
from src.models import DocumentProfile, EstimatedCost, LayoutComplexity, OriginType
from src.utils.pdf_analysis import PageStats


def test_confidence_high_when_good_stats():
    stats = [
        PageStats(1, 200, 35, 0.1, True, 612, 792, 0, 0),
        PageStats(2, 250, 40, 0.05, True, 612, 792, 0, 0),
    ]
    c = _confidence_from_page_stats(stats)
    assert c >= 0.6
    assert c <= 1.0


def test_confidence_low_when_scanned_like():
    stats = [
        PageStats(1, 20, 3, 0.7, False, 612, 792, 0, 0),
        PageStats(2, 15, 2, 0.8, False, 612, 792, 0, 0),
    ]
    c = _confidence_from_page_stats(stats)
    assert c < 0.6


def test_confidence_empty_pages():
    assert _confidence_from_page_stats([]) == 0.0


def test_fast_text_extractor_can_handle():
    extractor = FastTextExtractor()
    profile_digital_single = DocumentProfile(
        doc_id="test",
        origin_type=OriginType.NATIVE_DIGITAL,
        layout_complexity=LayoutComplexity.SINGLE_COLUMN,
        estimated_extraction_cost=EstimatedCost.FAST_TEXT_SUFFICIENT,
    )
    assert extractor.can_handle(profile_digital_single) is True

    profile_scanned = DocumentProfile(
        doc_id="test2",
        origin_type=OriginType.SCANNED_IMAGE,
        layout_complexity=LayoutComplexity.SINGLE_COLUMN,
        estimated_extraction_cost=EstimatedCost.NEEDS_VISION_MODEL,
    )
    assert extractor.can_handle(profile_scanned) is False


def test_fast_text_extractor_requires_pdf():
    pytest.importorskip("pdfplumber")
    extractor = FastTextExtractor()
    profile = DocumentProfile(
        doc_id="test",
        origin_type=OriginType.NATIVE_DIGITAL,
        layout_complexity=LayoutComplexity.SINGLE_COLUMN,
        estimated_extraction_cost=EstimatedCost.FAST_TEXT_SUFFICIENT,
    )
    with pytest.raises((FileNotFoundError, OSError)):
        extractor.extract(Path("/nonexistent/file.pdf"), profile)


@pytest.mark.skipif(not Path("data").exists() or not list(Path("data").glob("*.pdf")), reason="No PDFs in data/")
def test_fast_text_extract_returns_doc_and_confidence():
    pdfs = list(Path("data").glob("*.pdf"))
    if not pdfs:
        pytest.skip("No PDFs in data/")
    profile = DocumentProfile(
        doc_id="test",
        origin_type=OriginType.NATIVE_DIGITAL,
        layout_complexity=LayoutComplexity.SINGLE_COLUMN,
        estimated_extraction_cost=EstimatedCost.FAST_TEXT_SUFFICIENT,
    )
    doc, confidence = FastTextExtractor().extract(pdfs[0], profile)
    assert doc.doc_id == profile.doc_id
    assert 0 <= confidence <= 1
    assert hasattr(doc, "text_blocks")
    assert hasattr(doc, "tables")
