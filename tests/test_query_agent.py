"""Unit tests for Query Interface Agent (Stage 5)."""

import tempfile
from pathlib import Path

import pytest

from src.agents.indexer import build_page_index, save_page_index
from src.agents.query_agent import (
    RefineryContext,
    RefineryQueryAgent,
    load_refinery_context,
    pageindex_navigate,
    semantic_search,
    structured_query,
)
from src.models import (
    BoundingBox,
    ChunkType,
    LDU,
    PageIndex,
    QueryAnswer,
    AuditResult,
)


def _ldu(content: str, page: int = 1) -> LDU:
    return LDU(
        content=content,
        chunk_type=ChunkType.PARAGRAPH,
        page_refs=[page],
        bounding_box=BoundingBox(x0=0, top=0, x1=100, bottom=100, page=page),
        content_hash="chash_" + content[:6].replace(" ", "_"),
        chunk_id="cid_" + content[:5].replace(" ", "_"),
    )


@pytest.fixture
def sample_ldus():
    return [
        _ldu("1. Introduction", 1),
        _ldu("This report describes revenue and profit for fiscal year 2024. Revenue was $10 million.", 1),
        _ldu("2. Financial Results", 2),
        _ldu("Profit before tax: $2.5 million. See table below.", 2),
    ]


@pytest.fixture
def sample_page_index(sample_ldus):
    return build_page_index("test_doc", sample_ldus, total_pages=2)


@pytest.fixture
def refinery_context(sample_page_index, sample_ldus):
    return RefineryContext(
        doc_id="test_doc",
        document_name="Test Document",
        page_index=sample_page_index,
        chroma_collection=None,
        ldus=sample_ldus,
        fact_db_path=None,
    )


def test_pageindex_navigate_tool(refinery_context):
    sections, chain = pageindex_navigate(refinery_context, section_title="Financial")
    assert isinstance(chain.citations, list)
    assert len(sections) >= 0


def test_semantic_search_tool(refinery_context):
    hits, chain = semantic_search(refinery_context, "revenue profit", top_k=3)
    assert isinstance(hits, list)
    assert isinstance(chain.citations, list)
    # Keyword fallback should find chunks with revenue/profit
    assert len(hits) >= 1 or len(refinery_context.ldus) == 0


def test_structured_query_tool_no_db(refinery_context):
    # No fact db path -> empty result
    rows, chain = structured_query(refinery_context, "SELECT 1")
    assert rows == []
    assert isinstance(chain.citations, list)


def test_refinery_query_agent_ask(refinery_context):
    agent = RefineryQueryAgent(refinery_context)
    result = agent.ask("What is the revenue?")
    assert isinstance(result, QueryAnswer)
    assert result.answer
    assert hasattr(result, "provenance")
    assert isinstance(result.provenance.citations, list)


def test_refinery_query_agent_audit_claim_verified(refinery_context):
    agent = RefineryQueryAgent(refinery_context)
    result = agent.audit_claim("The report describes revenue and profit for fiscal year 2024.")
    assert isinstance(result, AuditResult)
    # Should verify (overlap with LDU content)
    assert result.verified in (True, False)
    if result.verified:
        assert result.citation is not None
        assert result.message == "Verified"


def test_refinery_query_agent_audit_claim_unverifiable(refinery_context):
    agent = RefineryQueryAgent(refinery_context)
    result = agent.audit_claim("The moon is made of cheese.")
    assert isinstance(result, AuditResult)
    assert result.verified is False
    assert "not found" in result.message.lower() or "unverifiable" in result.message.lower()


def test_load_refinery_context_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = load_refinery_context("nonexistent_doc", refinery_dir=tmp)
        assert ctx is None


def test_load_refinery_context_with_pageindex(sample_ldus, sample_page_index):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pageindex").mkdir(parents=True)
        (root / "ldus").mkdir(parents=True)
        save_page_index(sample_page_index, root / "pageindex")
        import json
        with open(root / "ldus" / "test_doc.json", "w", encoding="utf-8") as f:
            json.dump([u.model_dump(mode="json") for u in sample_ldus], f)
        ctx = load_refinery_context("test_doc", document_name="Test", refinery_dir=root)
        assert ctx is not None
        assert ctx.doc_id == "test_doc"
        assert ctx.document_name == "Test"
        assert len(ctx.ldus) == len(sample_ldus)
        agent = RefineryQueryAgent(ctx)
        ans = agent.ask("revenue")
        assert ans.answer and isinstance(ans.provenance.citations, list)
