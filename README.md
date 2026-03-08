# DocRefinery

Document Intelligence Refinery — a production-grade, multi-stage agentic pipeline that ingests heterogeneous documents and emits structured, queryable, spatially-indexed knowledge.

## Structure

- **src/** – Source code
  - **models/** – Pydantic schemas: `DocumentProfile`, `ExtractedDocument`, `LDU`, `PageIndex`, `ProvenanceChain`
  - **agents/** – Triage (`triage.py`), ExtractionRouter (`extractor.py`), ChunkingEngine (`chunker.py`), PageIndex builder (`indexer.py`), optional vector store (`vector_store.py`)
  - **strategies/** – Extraction strategies: `FastTextExtractor`, `LayoutExtractor`, `VisionExtractor`
  - **utils/** – PDF analysis (e.g. `pdf_analysis.py` for triage/confidence)
  - **config/** – Configuration
- **rubric/** – Extraction rules and thresholds (`extraction_rules.yaml`)
- **tests/** – Unit tests (Triage, extraction confidence)
- **data/** – Input documents (see [data/README.md](data/README.md))
  - **documents/** – PDFs by category: `annual_reports/`, `audits_financial/`, `technical/`, `economic_indices/`
- **.refinery/** – Runtime artifacts
  - **profiles/** – DocumentProfile JSON per document
  - **extraction_ledger.jsonl** – Extraction log (strategy, confidence, cost)
  - **pageindex/** – PageIndex trees (Stage 3+4)
  - **ldus/** – Cached LDUs for Query Agent (optional)
  - **facts.db** – SQLite fact table for structured_query (Stage 5)

## Setup

```bash
# From repo root
pip install -e .

# With uv (recommended):
uv sync
# Optional extras:
# uv sync --extra docling    # layout-aware extraction
# uv sync --extra chroma    # ChromaDB for vector search
```

Optional: `pip install -e ".[docling]"` for Docling, `pip install -e ".[chroma]"` for ChromaDB.

## Quick start: run against `data/`

From the repo root, run the full pipeline on all PDFs under `data/`, then see Query Agent results:

```bash
# Install (with uv)
uv sync

# Run pipeline on one PDF or entire data directory
uv run python main.py data/

# Or run the demo: pipeline + sample Query Agent question and audit
uv run python scripts/run_demo.py data/
```

To process a single PDF:

```bash
uv run python main.py "data/documents/audits_financial/Audit Report - 2023.pdf"
```

Results appear under **`.refinery/`**:
- **profiles/** – one JSON per document (triage result)
- **extraction_ledger.jsonl** – strategy and confidence per run
- **pageindex/** – one JSON per document (section tree)
- **ldus/** – cached chunks for the Query Agent
- **facts.db** – extracted facts for structured queries

Then in Python you can load the Query Agent and ask questions with provenance (see [Stage 5](#stage-5-query-agent-and-audit-mode) below).

## Usage

### Run pipeline on one document

```python
from pathlib import Path
from src.pipeline import run_refinery_on_document

profile_dict, strategy, confidence = run_refinery_on_document(Path("data/your_doc.pdf"))
# Profile → .refinery/profiles/{doc_id}.json
# Ledger → .refinery/extraction_ledger.jsonl
# LDUs + PageIndex → .refinery/pageindex/{doc_id}.json (Stage 3+4); optional ChromaDB ingestion
```

### Run on all PDFs in `data/` or `data/documents/`

```python
from pathlib import Path
from src.pipeline import run_refinery_on_directory

results = run_refinery_on_directory(Path("data"))
```

### Generate interim artifacts (12 sample profiles + ledger)

If you don't have the corpus yet, generate placeholder profiles and ledger entries for the four document classes:

```bash
python scripts/generate_interim_artifacts.py
```

### Stage 3 & 4: Chunking and PageIndex (after extraction)

```python
from src.agents.chunker import run_chunker
from src.agents.indexer import build_page_index, save_page_index, pageindex_query
from pathlib import Path

# After you have an ExtractedDocument (e.g. from ExtractionRouter):
ldus = run_chunker(extracted_doc)
page_index = build_page_index(profile.doc_id, ldus, total_pages=len(extracted_doc.pages))
save_page_index(page_index, Path(".refinery/pageindex"))

# Topic-based section lookup (before vector search)
top_sections = pageindex_query(page_index, "financial revenue", top_k=3)
```

### Stage 5: Query Agent and Audit Mode

After running the pipeline on a document, query it with full provenance and verify claims:

```python
from pathlib import Path
from src.agents.query_agent import load_refinery_context, RefineryQueryAgent

# Load context from .refinery (PageIndex, LDUs, fact table)
ctx = load_refinery_context("your_doc_id", document_name="Report 2024.pdf")
if ctx:
    agent = RefineryQueryAgent(ctx)
    # Every answer includes ProvenanceChain (document_name, page_number, bbox, content_hash)
    result = agent.ask("What was the revenue in Q3?")
    print(result.answer)
    for c in result.provenance.citations:
        print(f"  → {c.document_name} p.{c.page_number} {c.excerpt[:80]}...")

    # Audit Mode: verify a claim or flag as unverifiable
    audit = agent.audit_claim("The report states revenue was $4.2B in Q3.")
    print(audit.verified, audit.message)
    if audit.citation:
        print(f"  Source: p.{audit.citation.page_number} {audit.citation.excerpt[:100]}")
```

The agent uses three tools: **pageindex_navigate** (tree traversal), **semantic_search** (vector or keyword search over LDUs), and **structured_query** (SQL over the fact table). Facts are extracted automatically during pipeline run (Stage 3+4) into `.refinery/facts.db`.

### Triage only

```python
from pathlib import Path
from src.agents.triage import run_triage

profile = run_triage(Path("data/report.pdf"))
print(profile.origin_type, profile.layout_complexity, profile.estimated_extraction_cost)
```

## Configuration

Edit **rubric/extraction_rules.yaml** to change:

- Fast-text confidence thresholds and escalation
- Chunking rules (table/figure/list/section handling)
- Vision budget per document

## Interim submission checklist

- [x] Core models in `src/models/` (DocumentProfile, ExtractedDocument, LDU, PageIndex, ProvenanceChain)
- [x] Triage Agent: `src/agents/triage.py`
- [x] Strategies: `src/strategies/` (FastText, Layout, Vision) + `src/agents/extractor.py` (router with escalation)
- [x] `rubric/extraction_rules.yaml`
- [x] `.refinery/profiles/` (≥12 docs, ≥3 per class) — run pipeline or `scripts/generate_interim_artifacts.py`
- [x] `.refinery/extraction_ledger.jsonl`
- [x] Unit tests for Triage and confidence scoring
