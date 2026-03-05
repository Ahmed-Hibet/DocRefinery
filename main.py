"""
DocRefinery CLI — run triage and extraction on documents.
"""

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py <path-to-pdf-or-directory>")
        print("  Run Refinery pipeline (triage + extraction) on a PDF or all PDFs in a directory.")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Error: path does not exist: {path}")
        sys.exit(1)

    from src.pipeline import run_refinery_on_document, run_refinery_on_directory

    if path.is_file():
        if path.suffix.lower() != ".pdf":
            print("Warning: expected a PDF file. Proceeding anyway.")
        profile_dict, strategy, confidence = run_refinery_on_document(path)
        print(f"Doc ID: {profile_dict['doc_id']}")
        print(f"Strategy: {strategy} | Confidence: {confidence:.2f}")
        print(f"Profile saved to .refinery/profiles/{profile_dict['doc_id']}.json")
    else:
        results = run_refinery_on_directory(path)
        for doc_id, strategy, confidence in results:
            print(f"{doc_id} | {strategy} | {confidence:.2f}")
        print(f"Processed {len(results)} document(s).")


if __name__ == "__main__":
    main()
