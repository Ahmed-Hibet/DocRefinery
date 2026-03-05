"""Unit tests for Triage Agent classification."""

import pytest
from pathlib import Path

from src.agents.triage import (
    _classify_domain_hint,
    _classify_layout_complexity,
    _classify_origin_type,
    _doc_id_from_path,
    _estimate_extraction_cost,
    run_triage,
)
from src.models import (
    DomainHint,
    EstimatedCost,
    LayoutComplexity,
    OriginType,
)
from src.utils.pdf_analysis import PageStats


def test_doc_id_from_path():
    p = Path("data/CBE_ANNUAL_REPORT_2023_24.pdf")
    doc_id = _doc_id_from_path(p)
    assert doc_id.startswith("CBE_ANNUAL_REPORT_2023_24_")
    assert len(doc_id) > 20


def test_classify_origin_type_native_digital():
    stats = [
        PageStats(1, 500, 80, 0.1, True, 612, 792, 0, 0),
        PageStats(2, 600, 95, 0.05, True, 612, 792, 0, 0),
    ]
    assert _classify_origin_type(stats) == OriginType.NATIVE_DIGITAL


def test_classify_origin_type_scanned():
    stats = [
        PageStats(1, 5, 1, 0.8, False, 612, 792, 0, 0),
        PageStats(2, 10, 2, 0.7, False, 612, 792, 0, 0),
    ]
    assert _classify_origin_type(stats) == OriginType.SCANNED_IMAGE


def test_classify_origin_type_mixed():
    stats = [
        PageStats(1, 400, 60, 0.2, True, 612, 792, 0, 0),
        PageStats(2, 5, 1, 0.9, False, 612, 792, 0, 0),
    ]
    assert _classify_origin_type(stats) == OriginType.MIXED


def test_classify_layout_single_column():
    stats = [
        PageStats(1, 300, 50, 0.1, True, 612, 792, 0, 2),
        PageStats(2, 350, 55, 0.05, True, 612, 792, 0, 1),
    ]
    assert _classify_layout_complexity(stats) == LayoutComplexity.SINGLE_COLUMN


def test_classify_layout_table_heavy():
    stats = [
        PageStats(1, 200, 30, 0.1, True, 612, 792, 2, 5),
        PageStats(2, 250, 40, 0.1, True, 612, 792, 2, 4),
    ]
    assert _classify_layout_complexity(stats) == LayoutComplexity.TABLE_HEAVY


def test_classify_domain_hint_financial():
    path = Path("data/annual_report_2023.pdf")
    text = "balance sheet revenue fiscal year income statement assets"
    assert _classify_domain_hint(path, text) == DomainHint.FINANCIAL


def test_classify_domain_hint_legal():
    path = Path("data/contract.pdf")
    text = "whereas the party hereby agrees pursuant to jurisdiction"
    assert _classify_domain_hint(path, text) == DomainHint.LEGAL


def test_classify_domain_hint_general():
    path = Path("data/notes.pdf")
    text = "hello world random content"
    assert _classify_domain_hint(path, text) == DomainHint.GENERAL


def test_estimate_extraction_cost():
    assert _estimate_extraction_cost(OriginType.SCANNED_IMAGE, LayoutComplexity.SINGLE_COLUMN) == EstimatedCost.NEEDS_VISION_MODEL
    assert _estimate_extraction_cost(OriginType.NATIVE_DIGITAL, LayoutComplexity.SINGLE_COLUMN) == EstimatedCost.FAST_TEXT_SUFFICIENT
    assert _estimate_extraction_cost(OriginType.NATIVE_DIGITAL, LayoutComplexity.TABLE_HEAVY) == EstimatedCost.NEEDS_LAYOUT_MODEL
    assert _estimate_extraction_cost(OriginType.MIXED, LayoutComplexity.SINGLE_COLUMN) == EstimatedCost.NEEDS_LAYOUT_MODEL


@pytest.mark.skipif(not Path("data").exists() or not list(Path("data").glob("*.pdf")), reason="No PDFs in data/")
def test_run_triage_on_real_pdf():
    pdfs = list(Path("data").glob("*.pdf"))
    if not pdfs:
        pytest.skip("No PDFs in data/")
    profile = run_triage(pdfs[0])
    assert profile.doc_id
    assert profile.origin_type in OriginType
    assert profile.layout_complexity in LayoutComplexity
    assert profile.domain_hint in DomainHint
    assert profile.estimated_extraction_cost in EstimatedCost
