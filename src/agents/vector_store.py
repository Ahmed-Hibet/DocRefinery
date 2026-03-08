"""
Vector store ingestion for LDUs (ChromaDB).
Enables semantic search over chunks; used with PageIndex for section-aware retrieval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.models import LDU


def get_chromadb():
    """Import ChromaDB; raise if not installed."""
    try:
        import chromadb
        from chromadb.config import Settings
        return chromadb, Settings
    except ImportError as e:
        raise ImportError(
            "ChromaDB is required for vector store ingestion. "
            "Install with: pip install chromadb"
        ) from e


def ingest_ldus(
    ldus: list[LDU],
    doc_id: str,
    collection_name: str = "docrefinery_ldus",
    persist_directory: Path | str | None = None,
) -> Any:
    """
    Ingest LDUs into a ChromaDB collection. Each chunk is stored with content as document
    and metadata (doc_id, chunk_id, page_refs, content_hash, chunk_type, parent_section).
    Returns the collection for optional semantic_search use.
    """
    chromadb, Settings = get_chromadb()
    persist_path = str(Path(persist_directory or ".refinery/chromadb").resolve())
    client = chromadb.PersistentClient(path=persist_path)
    coll = client.get_or_create_collection(
        name=collection_name,
        metadata={"description": "DocRefinery LDUs"},
    )

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for ldu in ldus:
        chunk_id = ldu.chunk_id or f"{doc_id}_{len(ids)}"
        ids.append(chunk_id)
        documents.append(ldu.content)
        meta: dict[str, Any] = {
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "content_hash": ldu.content_hash,
            "chunk_type": ldu.chunk_type.value if hasattr(ldu.chunk_type, "value") else str(ldu.chunk_type),
        }
        if ldu.page_refs:
            meta["page_refs"] = ",".join(str(p) for p in ldu.page_refs)
            meta["page_start"] = min(ldu.page_refs)
        if ldu.parent_section:
            meta["parent_section"] = ldu.parent_section[:200]
        metadatas.append(meta)

    if ids:
        coll.add(ids=ids, documents=documents, metadatas=metadatas)

    return coll


def semantic_search(
    collection: Any,
    query: str,
    top_k: int = 5,
    doc_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Run semantic search over the LDU collection. Optionally filter by doc_id.
    Returns list of dicts with id, document, metadata, distance.
    """
    kwargs: dict[str, Any] = {"query_texts": [query], "n_results": top_k}
    if doc_id:
        kwargs["where"] = {"doc_id": doc_id}
    results = collection.query(**kwargs)
    out = []
    if results and results["ids"] and results["ids"][0]:
        for i, id_ in enumerate(results["ids"][0]):
            out.append({
                "id": id_,
                "document": results["documents"][0][i] if results.get("documents") else "",
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                "distance": results["distances"][0][i] if results.get("distances") else None,
            })
    return out
