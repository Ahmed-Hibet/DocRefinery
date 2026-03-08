"""
Run the full Refinery pipeline on data/ then run Query Agent (Stage 5) on the first processed doc.
Usage:
  uv run python scripts/run_demo.py [path]
  uv run python scripts/run_demo.py data/
  uv run python scripts/run_demo.py data/documents/audits_financial/Audit Report - 2023.pdf
"""

from pathlib import Path
import sys


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    data_path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data"
    if not data_path.exists():
        print(f"Path does not exist: {data_path}")
        print("Usage: uv run python scripts/run_demo.py [path-to-pdf-or-directory]")
        sys.exit(1)

    from src.pipeline import run_refinery_on_document, run_refinery_on_directory, REFINERY_DIR

    # --- Run pipeline ---
    if data_path.is_file():
        pdfs = [data_path]
        if data_path.suffix.lower() != ".pdf":
            print("Warning: expected a PDF. Proceeding anyway.")
    else:
        pdfs = sorted(data_path.glob("**/*.pdf"))

    if not pdfs:
        print(f"No PDFs found under {data_path}")
        sys.exit(1)

    print(f"Found {len(pdfs)} PDF(s). Running pipeline (Triage -> Extract -> Chunk -> PageIndex -> Facts)...\n")
    doc_ids_done = []
    for p in pdfs:
        try:
            profile_dict, strategy, confidence = run_refinery_on_document(p)
            doc_id = profile_dict["doc_id"]
            doc_ids_done.append((doc_id, profile_dict.get("metadata", {}).get("filename", p.name)))
            print(f"  OK  {p.name}")
            print(f"      doc_id: {doc_id}  strategy: {strategy}  confidence: {confidence:.2f}")
        except Exception as e:
            print(f"  FAIL {p.name}: {e}")
        print()

    if not doc_ids_done:
        print("No documents were processed successfully.")
        sys.exit(1)

    # --- Show artifacts ---
    print("Artifacts written under .refinery/")
    print(f"  profiles:    {list((REFINERY_DIR / 'profiles').glob('*.json'))[:5]}...")
    print(f"  pageindex:   {list((REFINERY_DIR / 'pageindex').glob('*.json'))[:5]}...")
    if (REFINERY_DIR / "ldus").exists():
        print(f"  ldus cache:  {list((REFINERY_DIR / 'ldus').glob('*.json'))[:5]}...")
    if (REFINERY_DIR / "facts.db").exists():
        print("  facts.db    (SQLite fact table)")
    print()

    # --- Query Agent on first doc ---
    doc_id, display_name = doc_ids_done[0]
    from src.agents.query_agent import load_refinery_context, RefineryQueryAgent

    ctx = load_refinery_context(doc_id, document_name=display_name or doc_id)
    if not ctx:
        print(f"Could not load context for {doc_id} (missing .refinery/pageindex/{doc_id}.json?).")
        print("Pipeline may have run but PageIndex not saved for this doc.")
        sys.exit(0)

    agent = RefineryQueryAgent(ctx)
    print(f"Query Agent loaded for: {ctx.document_name}\n")

    # Sample question
    question = "What is this document about? Summarize main topics or figures."
    print("--- Ask (with provenance) ---")
    print(f"Q: {question}\n")
    result = agent.ask(question)
    print(f"A: {result.answer[:500]}{'...' if len(result.answer) > 500 else ''}\n")
    print("Provenance (citations):")
    for i, c in enumerate(result.provenance.citations[:5], 1):
        print(f"  [{i}] {c.document_name}  p.{c.page_number}  {c.excerpt[:80]}...")
    if len(result.provenance.citations) > 5:
        print(f"  ... and {len(result.provenance.citations) - 5} more")
    print()

    # Audit a claim
    claim = "This document contains financial or audit information."
    print("--- Audit claim ---")
    print(f"Claim: \"{claim}\"\n")
    audit = agent.audit_claim(claim)
    print(f"Verified: {audit.verified}  Message: {audit.message}")
    if audit.citation:
        print(f"Source: p.{audit.citation.page_number}  {audit.citation.excerpt[:120]}...")
    print()
    print("Done. You can run more queries in Python:")
    print(f"  ctx = load_refinery_context('{doc_id}')")
    print("  agent = RefineryQueryAgent(ctx)")
    print("  agent.ask('your question')  # returns answer + provenance")
    print("  agent.audit_claim('claim to verify')  # returns verified + citation or unverifiable")


if __name__ == "__main__":
    main()
