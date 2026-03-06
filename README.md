# DocRefinery

Document Intelligence Refinery — a production-grade, multi-stage agentic pipeline that ingests heterogeneous documents and emits structured, queryable, spatially-indexed knowledge.

## Structure

- **src/** – Source code
  - **models/** – Pydantic schemas: `DocumentProfile`, `ExtractedDocument`, `LDU`, `PageIndex`, `ProvenanceChain`
  - **agents/** – Triage Agent (`triage.py`), ExtractionRouter (`extractor.py`)
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
  - **pageindex/** – PageIndex trees (Phase 3+)

## Setup

```bash
# From repo root
pip install -e .

# Optional: layout-aware extraction with Docling
pip install -e ".[docling]"
```

## Usage

### Run pipeline on one document

```python
from pathlib import Path
from src.pipeline import run_refinery_on_document

profile_dict, strategy, confidence = run_refinery_on_document(Path("data/your_doc.pdf"))
# Profile is saved to .refinery/profiles/{doc_id}.json
# Ledger entry appended to .refinery/extraction_ledger.jsonl
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
