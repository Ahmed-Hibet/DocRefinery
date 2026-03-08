"""Unit tests for Semantic Chunking Engine (Stage 3) and ChunkValidator."""

import pytest

from src.agents.chunker import ChunkingEngine, ChunkValidator, run_chunker
from src.models import (
    BoundingBox,
    ChunkType,
    ExtractedDocument,
    ExtractedFigure,
    ExtractedTable,
    LDU,
    TextBlock,
)


def _bbox(page: int = 1) -> BoundingBox:
    return BoundingBox(x0=0, top=0, x1=100, bottom=100, page=page)


@pytest.fixture
def sample_extracted_doc() -> ExtractedDocument:
    """Minimal ExtractedDocument with text blocks and one table."""
    return ExtractedDocument(
        doc_id="test_doc_1",
        pages=[1, 2],
        text_blocks=[
            TextBlock(text="1. Introduction\nThis report covers the annual results.", bbox=_bbox(1)),
            TextBlock(text="2. Financial Summary\nRevenue increased by 10%. See Table 1.", bbox=_bbox(1)),
        ],
        tables=[
            ExtractedTable(
                headers=["Year", "Revenue"],
                rows=[["2023", "100"], ["2024", "110"]],
                bbox=_bbox(2),
                caption="Table 1: Revenue",
            ),
        ],
        figures=[],
        reading_order=["0", "1"],
        raw_text="1. Introduction\nThis report covers the annual results.\n\n2. Financial Summary\nRevenue increased by 10%. See Table 1.",
    )


def test_chunker_emits_ldus(sample_extracted_doc: ExtractedDocument):
    ldus = run_chunker(sample_extracted_doc)
    assert len(ldus) >= 1
    for ldu in ldus:
        assert isinstance(ldu, LDU)
        assert ldu.content
        assert ldu.chunk_type in ChunkType
        assert ldu.content_hash
        assert ldu.chunk_id
        assert ldu.token_count >= 0
        assert len(ldu.page_refs) >= 1
        assert all(p >= 1 for p in ldu.page_refs)


def test_chunker_table_single_ldu(sample_extracted_doc: ExtractedDocument):
    """Rule 1: Table is one LDU (header + cells never split)."""
    ldus = run_chunker(sample_extracted_doc)
    table_ldus = [l for l in ldus if l.chunk_type == ChunkType.TABLE]
    assert len(table_ldus) == 1
    assert "Year" in table_ldus[0].content and "Revenue" in table_ldus[0].content
    assert "2023" in table_ldus[0].content


def test_chunker_figure_caption_in_metadata():
    """Rule 2: Figure caption stored as metadata of figure chunk."""
    doc = ExtractedDocument(
        doc_id="fig_doc",
        pages=[1],
        text_blocks=[],
        tables=[],
        figures=[
            ExtractedFigure(caption="Figure 1: Chart", bbox=_bbox(1)),
        ],
        raw_text="",
    )
    ldus = run_chunker(doc)
    fig_ldus = [l for l in ldus if l.chunk_type == ChunkType.FIGURE]
    assert len(fig_ldus) == 1
    assert "caption" in fig_ldus[0].metadata or "figure_caption" in fig_ldus[0].metadata or "Chart" in fig_ldus[0].content


def test_chunk_validator_passes_valid_ldus():
    validator = ChunkValidator()
    ldus = [
        LDU(
            content="Header | Col1\nrow1 | a",
            chunk_type=ChunkType.TABLE,
            page_refs=[1],
            content_hash="abc",
            chunk_id="t1",
        ),
    ]
    ok, errs = validator.validate(ldus)
    assert ok
    assert len(errs) == 0


def test_chunker_content_hash_stable():
    doc = ExtractedDocument(
        doc_id="hash_doc",
        pages=[1],
        text_blocks=[TextBlock(text="Same content", bbox=_bbox(1))],
        tables=[],
        figures=[],
        raw_text="Same content",
    )
    ldus1 = run_chunker(doc)
    ldus2 = run_chunker(doc)
    # Same content + page + bbox -> same content_hash for same logical chunk
    assert ldus1[0].content_hash == ldus2[0].content_hash


def test_chunker_raw_text_fallback():
    """When no text_blocks, raw_text is chunked into paragraphs."""
    doc = ExtractedDocument(
        doc_id="raw_only",
        pages=[1],
        text_blocks=[],
        tables=[],
        figures=[],
        raw_text="First paragraph.\n\nSecond paragraph with see Table 3 reference.",
    )
    ldus = run_chunker(doc)
    assert len(ldus) >= 1
    # Cross-ref may be in metadata (Rule 5)
    has_table_ref = any("cross_references" in l.metadata and "3" in str(l.metadata.get("cross_references", [])) for l in ldus)
    assert has_table_ref or any("Table" in l.content for l in ldus)
