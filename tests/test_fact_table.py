"""Unit tests for FactTable extractor (Stage 5)."""

import tempfile
from pathlib import Path

import pytest

from src.agents.fact_table import FactTableExtractor, run_fact_extraction, _extract_facts_from_text
from src.models import LDU, ChunkType, BoundingBox


def _ldu(content: str, page: int = 1) -> LDU:
    return LDU(
        content=content,
        chunk_type=ChunkType.PARAGRAPH,
        page_refs=[page],
        bounding_box=BoundingBox(x0=0, top=0, x1=100, bottom=100, page=page),
        content_hash="h" + content[:8].replace(" ", "_"),
        chunk_id="id_" + content[:5].replace(" ", "_"),
    )


def test_extract_facts_from_text():
    text = "Revenue was $4.2 billion. Fiscal year ended June 30, 2024."
    facts = _extract_facts_from_text(text)
    assert len(facts) >= 1
    keys = [f[0] for f in facts]
    # At least fiscal_period or revenue/amount pattern
    assert "fiscal_period" in keys or "revenue" in keys or "amount_currency" in keys


def test_fact_table_extractor_insert_and_query():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "facts.db"
        ext = FactTableExtractor(db_path=db)
        ldus = [
            _ldu("Total revenue: $10 million. Profit before tax: $2.5 million.", 1),
            _ldu("Fiscal year ended June 30, 2024. Assets: $100 million.", 2),
        ]
        n = ext.extract_from_ldus("doc1", ldus)
        assert n >= 1
        rows = ext.query("SELECT fact_key, fact_value, page_ref FROM facts WHERE doc_id = ?", ("doc1",))
        assert len(rows) >= 1
        row = rows[0]
        assert "fact_key" in row and "fact_value" in row


def test_fact_table_query_with_doc_id_filter():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "facts.db"
        ext = FactTableExtractor(db_path=db)
        ext.extract_from_ldus("doc_a", [_ldu("Revenue: $5B. Year 2023.", 1)])
        ext.extract_from_ldus("doc_b", [_ldu("Revenue: $6B.", 1)])
        rows = ext.query("SELECT doc_id, fact_key FROM facts", doc_id="doc_a")
        assert all(r["doc_id"] == "doc_a" for r in rows)


def test_run_fact_extraction():
    with tempfile.TemporaryDirectory() as tmp:
        n = run_fact_extraction("run_doc", [_ldu("Profit: $1.2 billion. Page 42.", 42)], db_path=Path(tmp) / "f.db")
        assert n >= 0
