"""
One-time script to move PDFs from data/data/ into data/documents/{category}/.
Run from repo root: python scripts/structure_data_dir.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "data" / "data"
DOCUMENTS_DIR = REPO_ROOT / "data" / "documents"

# Category subdirs and filename patterns (lowercase match)
CATEGORIES = {
    "annual_reports": [
        "cbe annual",
        "annual_report_june",
        "ethswitch",
        "ets",
        "annual-report",
        "company_profile",
        "ethio_re_at_a_glance",
    ],
    "audits_financial": [
        "audited_financial",
        "audit report",
        "audit-finding",
        "procurement",
        "assigned-regular-budget",
        "2013-e.c-",
    ],
    "technical": [
        "pharmaceutical",
        "fta_performance",
        "security_vulnerability",
    ],
    "economic_indices": [
        "consumer price index",
        "tax_expenditure",
    ],
}


def _category_for(name: str) -> str:
    lower = name.lower()
    for category, patterns in CATEGORIES.items():
        if any(p in lower for p in patterns):
            return category
    return "annual_reports"  # default for uncategorized


def main() -> None:
    if not SOURCE_DIR.exists():
        print(f"Source {SOURCE_DIR} not found; nothing to do.")
        return

    for category in CATEGORIES:
        (DOCUMENTS_DIR / category).mkdir(parents=True, exist_ok=True)

    moved = 0
    for path in sorted(SOURCE_DIR.iterdir()):
        if path.suffix.lower() != ".pdf" or path.name.startswith("."):
            continue
        category = _category_for(path.stem)
        dest_dir = DOCUMENTS_DIR / category
        dest = dest_dir / path.name
        if dest.exists() and dest.resolve() == path.resolve():
            continue
        shutil.move(str(path), str(dest))
        print(f"  {path.name} -> documents/{category}/")
        moved += 1

    print(f"Moved {moved} PDF(s) to data/documents/.")
    # Remove __MACOSX and empty data/data if desired (optional)
    macosx = REPO_ROOT / "data" / "__MACOSX"
    if macosx.exists():
        shutil.rmtree(macosx, ignore_errors=True)
        print("Removed data/__MACOSX/.")
    if SOURCE_DIR.exists():
        remaining = list(SOURCE_DIR.iterdir())
        for f in remaining:
            if f.name.startswith(".") or f.suffix.lower() != ".pdf":
                try:
                    f.unlink() if f.is_file() else shutil.rmtree(f, ignore_errors=True)
                except OSError:
                    pass
        if not any(SOURCE_DIR.iterdir()):
            SOURCE_DIR.rmdir()
            print("Removed empty data/data/.")


if __name__ == "__main__":
    main()
