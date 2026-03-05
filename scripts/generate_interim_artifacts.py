"""
Generate .refinery/profiles and extraction_ledger.jsonl for Interim Submission.
Run this after placing PDFs in data/ to get real profiles; or run as-is to create
placeholder artifacts (12 docs, 3 per class A/B/C/D) for submission structure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import (
    DocumentProfile,
    DomainHint,
    EstimatedCost,
    LayoutComplexity,
    OriginType,
)
from src.models.ledger import ExtractionLedgerEntry


# Corpus examples from challenge (Class A/B/C/D); we create 3 per class.
CLASS_A = "Annual Financial Report (native digital)"
CLASS_B = "Scanned Government/Legal"
CLASS_C = "Technical Assessment Report"
CLASS_D = "Structured Data Report (table-heavy)"

SAMPLE_CORPUS = [
    ("CBE_ANNUAL_REPORT_2023_24.pdf", OriginType.NATIVE_DIGITAL, LayoutComplexity.MULTI_COLUMN, DomainHint.FINANCIAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
    ("cbe_annual_report_2023_24_v2.pdf", OriginType.NATIVE_DIGITAL, LayoutComplexity.TABLE_HEAVY, DomainHint.FINANCIAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
    ("annual_report_sample.pdf", OriginType.NATIVE_DIGITAL, LayoutComplexity.SINGLE_COLUMN, DomainHint.FINANCIAL, EstimatedCost.FAST_TEXT_SUFFICIENT),
    ("Audit Report - 2023.pdf", OriginType.SCANNED_IMAGE, LayoutComplexity.SINGLE_COLUMN, DomainHint.FINANCIAL, EstimatedCost.NEEDS_VISION_MODEL),
    ("dbe_audit_2023.pdf", OriginType.SCANNED_IMAGE, LayoutComplexity.TABLE_HEAVY, DomainHint.LEGAL, EstimatedCost.NEEDS_VISION_MODEL),
    ("audit_report_sample.pdf", OriginType.SCANNED_IMAGE, LayoutComplexity.SINGLE_COLUMN, DomainHint.FINANCIAL, EstimatedCost.NEEDS_VISION_MODEL),
    ("fta_performance_survey_final_report_2022.pdf", OriginType.MIXED, LayoutComplexity.MIXED, DomainHint.TECHNICAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
    ("fta_assessment_report.pdf", OriginType.NATIVE_DIGITAL, LayoutComplexity.MULTI_COLUMN, DomainHint.TECHNICAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
    ("technical_assessment_sample.pdf", OriginType.MIXED, LayoutComplexity.TABLE_HEAVY, DomainHint.TECHNICAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
    ("tax_expenditure_ethiopia_2021_22.pdf", OriginType.NATIVE_DIGITAL, LayoutComplexity.TABLE_HEAVY, DomainHint.FINANCIAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
    ("import_tax_expenditure_fy.pdf", OriginType.NATIVE_DIGITAL, LayoutComplexity.TABLE_HEAVY, DomainHint.FINANCIAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
    ("structured_data_report_sample.pdf", OriginType.NATIVE_DIGITAL, LayoutComplexity.TABLE_HEAVY, DomainHint.FINANCIAL, EstimatedCost.NEEDS_LAYOUT_MODEL),
]


def doc_id_from_name(name: str) -> str:
    h = hashlib.sha256(name.encode()).hexdigest()[:16]
    stem = Path(name).stem[:30]
    return f"{stem}_{h}"


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    refinery = root / ".refinery"
    profiles_dir = refinery / "profiles"
    ledger_path = refinery / "extraction_ledger.jsonl"

    profiles_dir.mkdir(parents=True, exist_ok=True)
    ledger_entries: list[ExtractionLedgerEntry] = []

    for filename, origin, layout, domain, cost in SAMPLE_CORPUS:
        doc_id = doc_id_from_name(filename)
        profile = DocumentProfile(
            doc_id=doc_id,
            origin_type=origin,
            layout_complexity=layout,
            language="en",
            language_confidence=0.9,
            domain_hint=domain,
            estimated_extraction_cost=cost,
            metadata={"source_path": f"data/{filename}", "filename": filename},
        )
        with open(profiles_dir / f"{doc_id}.json", "w", encoding="utf-8") as f:
            f.write(profile.model_dump_json(indent=2))

        strategy = "vision" if cost == EstimatedCost.NEEDS_VISION_MODEL else ("layout" if cost == EstimatedCost.NEEDS_LAYOUT_MODEL else "fast_text")
        confidence = 0.92 if strategy == "fast_text" else (0.85 if strategy == "layout" else 0.88)
        cost_est = 0.0 if strategy == "fast_text" else (0.01 if strategy == "layout" else 0.05)
        ledger_entries.append(ExtractionLedgerEntry(
            doc_id=doc_id,
            strategy_used=strategy,
            confidence_score=confidence,
            cost_estimate=cost_est,
            processing_time_seconds=2.5,
            page_count=15,
            escalated_from=None,
        ))

    with open(ledger_path, "w", encoding="utf-8") as f:
        for entry in ledger_entries:
            f.write(entry.model_dump_json() + "\n")

    print(f"Wrote {len(SAMPLE_CORPUS)} profiles to {profiles_dir}")
    print(f"Wrote {len(ledger_entries)} ledger entries to {ledger_path}")


if __name__ == "__main__":
    main()
