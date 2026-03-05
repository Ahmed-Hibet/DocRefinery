"""
Refinery pipeline: run Triage -> save profile -> run Extraction -> log to ledger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agents.extractor import ExtractionRouter
from src.agents.triage import run_triage
from src.config import load_config


REFINERY_DIR = Path(".refinery")
PROFILES_DIR = REFINERY_DIR / "profiles"


def run_refinery_on_document(path: Path, config: dict[str, Any] | None = None) -> tuple[dict, str, float]:
    """
    Run full pipeline on one document: triage -> save profile -> extract -> ledger.
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
