"""
Query Interface Agent (Stage 5).
LangGraph-style agent with three tools: pageindex_navigate, semantic_search, structured_query.
Every answer includes a full ProvenanceChain (document_name, page_number, bbox, content_hash).
Audit Mode verifies claims with source citation or flags as unverifiable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.models import (
    BoundingBox,
    LDU,
    PageIndex,
    PageIndexSection,
    ProvenanceChain,
    ProvenanceCitation,
    QueryAnswer,
    AuditResult,
)


# --- Refinery context: what the agent can use ---


class RefineryContext:
    """
    Holds document-specific resources for the Query Agent:
    PageIndex, vector store (or LDU list fallback), fact table.
    """

    def __init__(
        self,
        doc_id: str,
        document_name: str,
        page_index: PageIndex,
        *,
        chroma_collection: Any = None,
        ldus: list[LDU] | None = None,
        fact_db_path: Path | str | None = None,
    ):
        self.doc_id = doc_id
        self.document_name = document_name
        self.page_index = page_index
        self.chroma_collection = chroma_collection
        self.ldus = ldus or []
        self.fact_db_path = Path(fact_db_path) if fact_db_path else Path(".refinery/facts.db")


# --- Three tools (return results + provenance info) ---


def pageindex_navigate(
    ctx: RefineryContext,
    section_title: str | None = None,
    page_num: int | None = None,
) -> tuple[list[PageIndexSection], ProvenanceChain]:
    """
    Tool: Navigate PageIndex by section title or page number.
    Returns (matching sections, provenance chain with page refs).
    """
    from src.agents.indexer import pageindex_navigate as _navigate

    sections = _navigate(ctx.page_index, section_title=section_title, page_num=page_num)
    chain = ProvenanceChain()
    for s in sections:
        chain.add(
            document_name=ctx.document_name,
            page_number=s.page_start,
            bbox=None,
            content_hash="",
            excerpt=f"Section: {s.title} (pp. {s.page_start}-{s.page_end})",
        )
    return sections, chain


def semantic_search(
    ctx: RefineryContext,
    query: str,
    top_k: int = 5,
) -> tuple[list[dict[str, Any]], ProvenanceChain]:
    """
    Tool: Vector or keyword search over LDUs. Returns (hits with content/metadata, ProvenanceChain).
    """
    chain = ProvenanceChain()
    if ctx.chroma_collection:
        try:
            from src.agents.vector_store import semantic_search as _chroma_search
            hits = _chroma_search(ctx.chroma_collection, query, top_k=top_k, doc_id=ctx.doc_id)
        except Exception:
            hits = _keyword_search_ldus(ctx.ldus, query, top_k)
    else:
        hits = _keyword_search_ldus(ctx.ldus, query, top_k)

    for h in hits:
        meta = h.get("metadata") or {}
        doc_name = ctx.document_name
        page = meta.get("page_start") or (meta.get("page_refs", "1").split(",")[0] if meta.get("page_refs") else 1)
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        excerpt = (h.get("document") or h.get("content") or "")[:300]
        bbox = h.get("bbox")  # Optional BoundingBox when from LDU
        chain.add(
            document_name=doc_name,
            page_number=page,
            bbox=bbox,
            content_hash=meta.get("content_hash", ""),
            excerpt=excerpt,
        )
    return hits, chain


def _keyword_search_ldus(ldus: list[LDU], query: str, top_k: int) -> list[dict[str, Any]]:
    """Fallback when ChromaDB not available: score by keyword overlap."""
    if not ldus:
        return []
    qwords = set(re.findall(r"\w+", query.lower()))
    scored = []
    for ldu in ldus:
        text = (ldu.content or "").lower()
        twords = set(re.findall(r"\w+", text))
        overlap = len(qwords & twords) / max(1, len(qwords))
        scored.append((overlap, ldu))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, ldu in scored[:top_k]:
        page = ldu.page_refs[0] if ldu.page_refs else 1
        out.append({
            "document": ldu.content,
            "metadata": {
                "doc_id": "",
                "content_hash": ldu.content_hash,
                "page_start": page,
                "page_refs": ",".join(str(p) for p in ldu.page_refs),
            },
            "bbox": ldu.bounding_box,
        })
    return out


def structured_query(
    ctx: RefineryContext,
    sql: str,
) -> tuple[list[dict[str, Any]], ProvenanceChain]:
    """
    Tool: Run SQL over the fact table. Returns (rows, ProvenanceChain from page_ref/content_hash in rows).
    """
    chain = ProvenanceChain()
    db_path = ctx.fact_db_path
    if not db_path.exists():
        return [], chain

    try:
        from src.agents.fact_table import FactTableExtractor
        ext = FactTableExtractor(db_path=db_path)
        rows = ext.query(sql, doc_id=ctx.doc_id)
    except Exception:
        return [], chain

    for row in rows:
        page = row.get("page_ref") or 1
        chain.add(
            document_name=ctx.document_name,
            page_number=int(page) if page else 1,
            bbox=None,
            content_hash=row.get("content_hash") or "",
            excerpt=(row.get("excerpt") or str(row))[:200],
        )
    return rows, chain


# --- Query Agent: orchestrates tools and builds answer with provenance ---


class RefineryQueryAgent:
    """
    Query Interface Agent. Uses pageindex_navigate, semantic_search, structured_query
    to answer questions; every answer includes a full ProvenanceChain.
    """

    def __init__(self, context: RefineryContext):
        self.ctx = context

    def ask(self, question: str) -> QueryAnswer:
        """
        Answer a natural language question using the three tools.
        Returns QueryAnswer with answer text and full ProvenanceChain.
        """
        provenance = ProvenanceChain()

        # 1) Use PageIndex to find relevant sections (topic -> sections)
        from src.agents.indexer import pageindex_query
        topic_sections = pageindex_query(self.ctx.page_index, question, top_k=3)
        section_refs = []
        for s in topic_sections:
            provenance.add(
                document_name=self.ctx.document_name,
                page_number=s.page_start,
                content_hash="",
                excerpt=f"Section: {s.title} (pp. {s.page_start}-{s.page_end}). {s.summary[:150]}",
            )
            section_refs.append(f"{s.title} (pp. {s.page_start}-{s.page_end})")

        # 2) Semantic search for relevant chunks
        hits, search_chain = semantic_search(self.ctx, question, top_k=5)
        for c in search_chain.citations:
            provenance.citations.append(c)

        # 3) If question looks like a fact/number query, try structured_query
        if _looks_like_fact_query(question):
            try:
                sql = _natural_to_sql(question)
                if sql:
                    rows, struct_chain = structured_query(self.ctx, sql)
                    for c in struct_chain.citations:
                        provenance.citations.append(c)
                    if rows:
                        answer_parts = [f"From the fact table: {len(rows)} result(s)."]
                        for r in rows[:5]:
                            answer_parts.append(f"  - {r.get('fact_key', '')}: {r.get('fact_value', '')} (p. {r.get('page_ref', '?')})")
                        answer = "\n".join(answer_parts)
                    else:
                        answer = _synthesize_from_hits(hits, question, section_refs)
                else:
                    answer = _synthesize_from_hits(hits, question, section_refs)
            except Exception:
                answer = _synthesize_from_hits(hits, question, section_refs)
        else:
            answer = _synthesize_from_hits(hits, question, section_refs)

        # Deduplicate citations by (doc, page, content_hash)
        seen = set()
        unique = []
        for c in provenance.citations:
            key = (c.document_name, c.page_number, c.content_hash or c.excerpt[:50])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        provenance.citations = unique

        return QueryAnswer(answer=answer, provenance=ProvenanceChain(citations=unique))

    def audit_claim(self, claim: str) -> AuditResult:
        """
        Audit Mode: verify the claim against the document. Returns AuditResult with
        verified=True and citation if found, else verified=False and message 'not found / unverifiable'.
        """
        # Search for supporting content
        hits, chain = semantic_search(self.ctx, claim, top_k=5)
        # Simple verification: check if any hit has high lexical overlap with claim
        claim_words = set(re.findall(r"\w+", claim.lower()))
        for h in hits:
            content = (h.get("document") or "").lower()
            content_words = set(re.findall(r"\w+", content))
            overlap = len(claim_words & content_words) / max(1, len(claim_words))
            if overlap >= 0.4:  # threshold for "supported"
                meta = h.get("metadata") or {}
                page = meta.get("page_start") or 1
                try:
                    page = int(page)
                except (TypeError, ValueError):
                    page = 1
                citation = ProvenanceCitation(
                    document_name=self.ctx.document_name,
                    page_number=page,
                    bbox=None,
                    content_hash=meta.get("content_hash", ""),
                    excerpt=(h.get("document") or "")[:300],
                )
                return AuditResult(verified=True, citation=citation, message="Verified")
        # Try fact table for numerical claims
        if _looks_like_fact_query(claim):
            try:
                sql = _natural_to_sql(claim)
                if sql:
                    rows, _ = structured_query(self.ctx, sql)
                    if rows:
                        c = ProvenanceCitation(
                            document_name=self.ctx.document_name,
                            page_number=rows[0].get("page_ref") or 1,
                            content_hash=rows[0].get("content_hash", ""),
                            excerpt=str(rows[0].get("excerpt", ""))[:200],
                        )
                        return AuditResult(verified=True, citation=c, message="Verified (fact table)")
            except Exception:
                pass
        return AuditResult(verified=False, citation=None, message="Not found / unverifiable")


def _looks_like_fact_query(text: str) -> bool:
    """Heuristic: question asks for numbers, revenue, date, etc."""
    lower = text.lower()
    return any(
        w in lower for w in ("revenue", "profit", "amount", "figure", "number", "total", "fiscal", "year", "quarter", "percent", "%")
    )


def _natural_to_sql(question: str) -> str | None:
    """Simple mapping from natural language to fact table SELECT."""
    q = question.lower()
    if "revenue" in q:
        return "SELECT fact_key, fact_value, page_ref, content_hash, excerpt FROM facts WHERE fact_key IN ('revenue', 'amount_currency') LIMIT 10"
    if "profit" in q:
        return "SELECT fact_key, fact_value, page_ref, content_hash, excerpt FROM facts WHERE fact_key = 'profit' LIMIT 10"
    if "fiscal" in q or "year" in q:
        return "SELECT fact_key, fact_value, page_ref, content_hash, excerpt FROM facts WHERE fact_key IN ('fiscal_period', 'quarter') LIMIT 10"
    return "SELECT fact_key, fact_value, page_ref, content_hash, excerpt FROM facts LIMIT 10"


def _synthesize_from_hits(
    hits: list[dict],
    question: str,
    section_refs: list[str],
) -> str:
    """Build a short answer from search hits and section refs."""
    parts = []
    if section_refs:
        parts.append(f"Relevant sections: {', '.join(section_refs[:3])}.")
    if hits:
        for i, h in enumerate(hits[:3], 1):
            content = (h.get("document") or "").strip()
            if content:
                parts.append(f"[{i}] {content[:250]}{'...' if len(content) > 250 else ''}")
    if not parts:
        return "No relevant content found in the document for this question."
    return "\n\n".join(parts)


# --- Load context from .refinery (for Query Agent after pipeline run) ---


def load_refinery_context(
    doc_id: str,
    document_name: str | None = None,
    refinery_dir: Path | str = ".refinery",
) -> RefineryContext | None:
    """
    Load RefineryContext for a document from .refinery (PageIndex, optional ChromaDB, LDU cache, fact table).
    Returns None if PageIndex file is missing. document_name defaults to doc_id.
    """
    root = Path(refinery_dir)
    pageindex_path = root / "pageindex" / f"{doc_id}.json"
    if not pageindex_path.exists():
        return None
    from src.agents.indexer import load_page_index
    pi = load_page_index(pageindex_path)
    chroma_coll = None
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(root / "chromadb"))
        chroma_coll = client.get_or_create_collection("docrefinery_ldus")
    except Exception:
        pass
    ldus: list[LDU] = []
    cache_path = root / "ldus" / f"{doc_id}.json"
    if cache_path.exists():
        try:
            import json
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            ldus = [LDU.model_validate(d) for d in data]
        except Exception:
            pass
    return RefineryContext(
        doc_id=doc_id,
        document_name=document_name or doc_id,
        page_index=pi,
        chroma_collection=chroma_coll,
        ldus=ldus,
        fact_db_path=root / "facts.db",
    )
