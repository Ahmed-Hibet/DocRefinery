"""
FactTable extractor (Stage 5).
Extracts key-value facts from financial/numerical documents into a SQLite table
for precise structured_query (e.g. revenue: $4.2B, date: Q3 2024).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from src.models import LDU


# Patterns for key-value fact extraction (financial/numerical)
FACT_PATTERNS = [
    (r"\b(?:revenue|total revenue|net revenue)\s*[:\s]*([\$€£]?\s*[\d,]+(?:\.\d+)?\s*(?:million|billion|B|M|bn|mn)?)", "revenue"),
    (r"\b(?:profit|net profit|profit before tax)\s*[:\s]*([\$€£]?\s*[\d,]+(?:\.\d+)?\s*(?:million|billion|B|M)?)", "profit"),
    (r"\b(?:fiscal year|FY|year ended|period ended)\s*[:\s]*([A-Za-z]+\s+\d{1,2},?\s*\d{4}|\d{4}[-/]\d{2}|\d{4})", "fiscal_period"),
    (r"\b(?:Q[1-4]|quarter)\s*(\d{4})\b", "quarter"),
    (r"\b(?:assets|total assets)\s*[:\s]*([\$€£]?\s*[\d,]+(?:\.\d+)?\s*(?:million|billion|B|M)?)", "assets"),
    (r"\b(?:equity|shareholders?\s*equity)\s*[:\s]*([\$€£]?\s*[\d,]+(?:\.\d+)?\s*(?:million|billion|B|M)?)", "equity"),
    (r"\b(?:expenditure|tax expenditure)\s*[:\s]*([\$€£]?\s*[\d,]+(?:\.\d+)?\s*(?:million|billion|B|M)?)", "expenditure"),
    (r"\b(?:Birr|ETB|USD)\s*([\d,]+(?:\.\d+)?)\s*(?:million|billion)?", "amount_currency"),
    (r"\b(?:page|p\.)\s*(\d+)\b", "page_ref"),
]


def _extract_facts_from_text(text: str) -> list[tuple[str, str, str]]:
    """Return list of (fact_key, fact_value, matched_phrase)."""
    facts: list[tuple[str, str, str]] = []
    for pattern, key in FACT_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            value = m.group(1).strip()
            phrase = m.group(0).strip()[:200]
            if value and len(value) < 100:
                facts.append((key, value, phrase))
    return facts


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            value_type TEXT DEFAULT 'text',
            page_ref INTEGER,
            content_hash TEXT,
            excerpt TEXT,
            UNIQUE(doc_id, fact_key, fact_value, page_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_facts_doc_key ON facts(doc_id, fact_key);
    """)


class FactTableExtractor:
    """
    Extracts key-value facts from LDUs and stores them in SQLite.
    Used by the Query Agent's structured_query tool for precise numerical queries.
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or ".refinery/facts.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        _init_schema(conn)
        conn.close()

    def extract_from_ldus(self, doc_id: str, ldus: list[LDU]) -> int:
        """
        Extract facts from LDUs and insert into the fact table. Returns count inserted.
        """
        conn = sqlite3.connect(str(self.db_path))
        _init_schema(conn)
        count = 0
        for ldu in ldus:
            facts = _extract_facts_from_text(ldu.content)
            page_ref = ldu.page_refs[0] if ldu.page_refs else None
            for fact_key, fact_value, phrase in facts:
                try:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO facts (doc_id, fact_key, fact_value, page_ref, content_hash, excerpt)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (doc_id, fact_key, fact_value, page_ref, ldu.content_hash or "", phrase[:500]),
                    )
                    count += cur.rowcount
                except sqlite3.IntegrityError:
                    pass
        conn.commit()
        conn.close()
        return count

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def query(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
        doc_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Run a SELECT query against the fact table. If doc_id is set, restrict to that document.
        Returns list of dicts; each row can include page_ref and content_hash for provenance.
        """
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            if doc_id and "WHERE" not in sql.upper():
                sql = sql.rstrip(";") + " WHERE doc_id = ?"
                parameters = (*parameters, doc_id)
            elif doc_id and "WHERE" in sql.upper():
                sql = sql.rstrip(";") + " AND doc_id = ?"
                parameters = (*parameters, doc_id)
            cur = conn.execute(sql, parameters)
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()


def run_fact_extraction(doc_id: str, ldus: list[LDU], db_path: Path | str | None = None) -> int:
    """Convenience: extract facts from LDUs into default fact table. Returns count inserted."""
    extractor = FactTableExtractor(db_path=db_path)
    return extractor.extract_from_ldus(doc_id, ldus)
