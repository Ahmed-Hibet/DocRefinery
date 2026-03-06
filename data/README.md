# Data Directory

Input documents for the DocRefinery pipeline. Place PDFs here (or in subfolders) for triage and extraction.

## Structure

```
data/
├── README.md           # This file
└── documents/          # Source PDFs (discovered recursively by the pipeline)
    ├── annual_reports/ # Bank & institutional annual reports
    ├── audits_financial/   # Audits, financial statements, budget/expense
    ├── technical/     # Technical assessments, surveys, procedures
    └── economic_indices/   # CPI, tax expenditure, economic summaries
```

The pipeline uses `**/*.pdf` under the path you pass (e.g. `data/` or `data/documents/`), so you can add more subfolders or keep PDFs in `documents/` if you prefer a flat layout.

## Usage

- **Single file:** `python main.py data/documents/annual_reports/some_report.pdf`
- **All documents:** `python main.py data/` or `python main.py data/documents/`

Profiles and ledger entries are written to `.refinery/` as usual.

## Adding documents

1. Add PDFs under `documents/` (optionally in the category subfolders above).
2. Re-run the pipeline on the file or directory; new profiles and ledger entries will be created.
