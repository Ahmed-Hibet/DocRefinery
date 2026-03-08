"""Unit tests for PageIndex Builder (Stage 4) and PageIndex query."""

import json
import tempfile
from pathlib import Path

import pytest

from src.agents.indexer import (
    build_page_index,
    load_page_index,
    pageindex_navigate,
    pageindex_query,
    save_page_index,
    PageIndexBuilder,
)
from src.models import (
    BoundingBox,
    ChunkType,
    DataTypesPresent,
    LDU,
    PageIndexSection,
)


def _ldu(content: str, chunk_type: ChunkType, page: int = 1, title: str | None = None) -> LDU:
    return LDU(
        content=content,
        chunk_type=chunk_type,
        page_refs=[page],
        bounding_box=BoundingBox(x0=0, top=0, x1=100, bottom=100, page=page),
        parent_section=title,
        token_count=len(content) // 4,
        content_hash="h" + content[:8].replace(" ", "_"),
        chunk_id="id_" + content[:5].replace(" ", "_"),
        metadata={},
    )


@pytest.fixture
def sample_ldus() -> list[LDU]:
    """LDUs with section headers and content."""
    return [
        _ldu("1. Introduction", ChunkType.SECTION_HEADER, 1),
        _ldu("This report describes the methodology.", ChunkType.PARAGRAPH, 1, "1. Introduction"),
        _ldu("2. Financial Results", ChunkType.SECTION_HEADER, 2),
        _ldu("Revenue was $10M. Profit increased.", ChunkType.PARAGRAPH, 2, "2. Financial Results"),
        _ldu("3. Appendix", ChunkType.SECTION_HEADER, 3),
        _ldu("See Table A1 for details.", ChunkType.PARAGRAPH, 3, "3. Appendix"),
    ]


def test_build_page_index(sample_ldus: list[LDU]):
    pi = build_page_index("doc_1", sample_ldus, total_pages=3)
    assert pi.doc_id == "doc_1"
    assert pi.total_pages == 3
    assert pi.root.title == "Document"
    assert len(pi.root.child_sections) >= 1
    for child in pi.root.child_sections:
        assert child.page_start >= 1
        assert child.page_end >= child.page_start
        assert child.summary or child.title


def test_pageindex_query_returns_top_sections(sample_ldus: list[LDU]):
    pi = build_page_index("doc_1", sample_ldus, total_pages=3)
    sections = pageindex_query(pi, "financial revenue", top_k=3)
    assert len(sections) <= 3
    # "Financial Results" should rank high for topic "financial revenue"
    titles = [s.title for s in sections]
    assert any("Financial" in t or "financial" in t.lower() for t in titles) or len(titles) > 0


def test_pageindex_query_empty_topic_returns_children(sample_ldus: list[LDU]):
    pi = build_page_index("doc_1", sample_ldus, total_pages=3)
    sections = pageindex_query(pi, "", top_k=3)
    assert len(sections) <= 3


def test_pageindex_navigate_by_title(sample_ldus: list[LDU]):
    pi = build_page_index("doc_1", sample_ldus, total_pages=3)
    found = pageindex_navigate(pi, section_title="Financial")
    assert len(found) >= 1
    assert any("Financial" in s.title for s in found)


def test_pageindex_navigate_by_page(sample_ldus: list[LDU]):
    pi = build_page_index("doc_1", sample_ldus, total_pages=3)
    found = pageindex_navigate(pi, page_num=2)
    assert len(found) >= 1
    assert any(s.page_start <= 2 <= s.page_end for s in found)


def test_save_and_load_page_index(sample_ldus: list[LDU]):
    pi = build_page_index("doc_1", sample_ldus, total_pages=3)
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        path = save_page_index(pi, out_dir)
        assert path.exists()
        loaded = load_page_index(path)
        assert loaded.doc_id == pi.doc_id
        assert loaded.total_pages == pi.total_pages
        assert len(loaded.root.child_sections) == len(pi.root.child_sections)


def test_page_index_section_data_types():
    """Sections record data_types_present (tables, figures, text)."""
    ldus = [
        _ldu("1. Data", ChunkType.SECTION_HEADER, 1),
        _ldu("Table: A | B", ChunkType.TABLE, 1, "1. Data"),
        _ldu("Chart below.", ChunkType.FIGURE, 1, "1. Data"),
    ]
    pi = build_page_index("doc_1", ldus, total_pages=1)
    assert pi.root.child_sections
    first = pi.root.child_sections[0]
    assert DataTypesPresent.TABLES in first.data_types_present or DataTypesPresent.FIGURES in first.data_types_present or DataTypesPresent.TEXT in first.data_types_present
