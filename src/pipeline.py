"""
Refinery pipeline: run Triage -> save profile -> run Extraction -> log to ledger
-> Chunking (Stage 3) -> PageIndex (Stage 4) -> optional vector store ingestion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agents.chunker import ChunkingEngine
from src.agents.extractor import ExtractionRouter
from src.agents.indexer import build_page_index, save_page_index
from src.agents.triage import run_triage
from src.config import load_config


REFINERY_DIR = Path(".refinery")
PROFILES_DIR = REFINERY_DIR / "profiles"
PAGEINDEX_DIR = REFINERY_DIR / "pageindex"


def run_chunk_and_index(
    doc_id: str,
    extracted_doc: Any,
    config: dict[str, Any] | None = None,
    *,
    save_index: bool = True,
    ingest_vector_store: bool = True,
) -> tuple[list[Any], Any]:
    """
    Stage 3 + 4: Chunk ExtractedDocument to LDUs, build PageIndex, optionally ingest to ChromaDB.
    Returns (list of LDUs, PageIndex). Writes PageIndex to .refinery/pageindex/{doc_id}.json when save_index=True.
    """
    from src.models import ExtractedDocument

    cfg = config or load_config()
    engine = ChunkingEngine(config=cfg)
    ldus = engine.chunk(extracted_doc)
    total_pages = len(extracted_doc.pages) if extracted_doc.pages else 1
    page_index = build_page_index(doc_id, ldus, total_pages)

    if save_index:
        PAGEINDEX_DIR.mkdir(parents=True, exist_ok=True)
        save_page_index(page_index, PAGEINDEX_DIR)

    if ingest_vector_store and ldus:
        try:
            from src.agents.vector_store import ingest_ldus
            ingest_ldus(ldus, doc_id, persist_directory=REFINERY_DIR / "chromadb")
        except ImportError:
            pass  # ChromaDB optional

    return ldus, page_index


def run_refinery_on_document(
    path: Path,
    config: dict[str, Any] | None = None,
    *,
    run_stages_3_4: bool = True,
) -> tuple[dict, str, float]:
    """
    Run full pipeline on one document: triage -> save profile -> extract -> ledger
    -> (optional) chunk -> PageIndex -> vector store.
    Returns (profile_dict, strategy_used, confidence).
    Uses rubric/extraction_rules.yaml when config is not provided.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    cfg = config if config is not None else load_config()
    profile = run_triage(path, config=cfg)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = PROFILES_DIR / f"{profile.doc_id}.json"
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(profile.model_dump_json(indent=2))

    router = ExtractionRouter(config=cfg, ledger_path=REFINERY_DIR / "extraction_ledger.jsonl")
    doc, strategy, confidence, cost = router.extract(path, profile)

    if run_stages_3_4:
        run_chunk_and_index(profile.doc_id, doc, config=cfg, save_index=True, ingest_vector_store=True)

    return profile.model_dump(), strategy, confidence


def run_refinery_on_directory(data_dir: Path) -> list[tuple[str, str, float]]:
    """Run pipeline on all PDFs in directory. Returns list of (doc_id, strategy, confidence)."""
    data_dir = Path(data_dir)
    results = []
    for p in sorted(data_dir.glob("**/*.pdf")):
        try:
            profile_dict, strategy, confidence = run_refinery_on_document(p)
            results.append((profile_dict["doc_id"], strategy, confidence))
        except Exception as e:
            results.append((p.name, "error", 0.0))
    return results
