"""
Strategy B — Layout-Aware: Docling or MinerU.
Medium cost; for multi_column, table_heavy, mixed.
Output normalized to ExtractedDocument (adapter if using DoclingDocument).
"""

from __future__ import annotations

from pathlib import Path

from src.models import (
    BoundingBox,
    DocumentProfile,
    ExtractedDocument,
    ExtractedFigure,
    ExtractedTable,
    TextBlock,
)

def _normalize_cell(c: object) -> str | int | float:
    """Coerce cell value for ExtractedTable.rows; None -> ''."""
    if c is None:
        return ""
    if isinstance(c, (str, int, float)):
        return c
    return str(c)


def _try_docling_extract(path: Path, profile: DocumentProfile) -> ExtractedDocument | None:
    """Use Docling if available; return None otherwise."""
    try:
        from docling.document_converter import DocumentConverter
        from docling.datamodel.base_models import InputFormat
    except ImportError:
        return None

    path = Path(path)
    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document

    text_blocks: list[TextBlock] = []
    tables: list[ExtractedTable] = []
    figures: list[ExtractedFigure] = []
    pages: list[int] = []
    raw_parts: list[str] = []

    # Adapt Docling document to our schema
    if hasattr(doc, "export_to_markdown"):
        raw_parts.append(doc.export_to_markdown())

    # Try to get tables and text with provenance
    if hasattr(doc, "tables"):
        for i, tbl in enumerate(doc.tables):
            try:
                if hasattr(tbl, "data") and tbl.data:
                    data = tbl.data
                    if isinstance(data, list) and data:
                        headers = [str(c) if c is not None else "" for c in data[0]]
                        rows = [[_normalize_cell(c) for c in row] for row in data[1:]] if len(data) > 1 else []
                        bbox = None
                        if hasattr(tbl, "prov") and tbl.prov:
                            prov = tbl.prov[0] if isinstance(tbl.prov, list) else tbl.prov
                            if hasattr(prov, "page_no") and hasattr(prov, "bbox"):
                                page_no = getattr(prov, "page_no", 1) or 1
                                b = getattr(prov, "bbox", None)
                                if b and len(b) >= 4:
                                    bbox = BoundingBox(x0=b[0], top=b[1], x1=b[2], bottom=b[3], page=page_no)
                        tables.append(ExtractedTable(headers=headers, rows=rows, bbox=bbox))
            except Exception:
                continue

    # Text blocks from document body
    if hasattr(doc, "text") and doc.text:
        raw_parts.append(doc.text)
    if hasattr(doc, "pages"):
        for p in doc.pages:
            pno = getattr(p, "page_no", len(pages) + 1) or (len(pages) + 1)
            if pno not in pages:
                pages.append(pno)
            if hasattr(p, "text") and p.text:
                raw_parts.append(p.text)

    if not pages:
        pages = list(range(1, 2))

    return ExtractedDocument(
        doc_id=profile.doc_id,
        pages=pages or [1],
        text_blocks=text_blocks,
        tables=tables,
        figures=figures,
        raw_text="\n\n".join(raw_parts) or "",
    )


def _fallback_pdfplumber_layout(path: Path, profile: DocumentProfile) -> ExtractedDocument:
    """Fallback: use pdfplumber with full table and block extraction."""
    import pdfplumber

    text_blocks: list[TextBlock] = []
    tables: list[ExtractedTable] = []
    raw_parts: list[str] = []
    pages: list[int] = []

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            pnum = i + 1
            pages.append(pnum)
            text = page.extract_text()
            if text:
                raw_parts.append(text)
            for tbl in page.find_tables() or []:
                data = tbl.extract()
                if data:
                    headers = [str(c) if c is not None else "" for c in data[0]]
                    rows = [[_normalize_cell(c) for c in row] for row in data[1:]]
                    bbox = tbl.bbox
                    b = BoundingBox(x0=float(bbox[0]), top=float(bbox[1]), x1=float(bbox[2]), bottom=float(bbox[3]), page=pnum) if bbox and len(bbox) >= 4 else None
                    tables.append(ExtractedTable(headers=headers, rows=rows, bbox=b))
            words = page.extract_words() or []
            if words:
                x0 = min(w["x0"] for w in words)
                top = min(w["top"] for w in words)
                x1 = max(w["x1"] for w in words)
                bottom = max(w["bottom"] for w in words)
                text_blocks.append(
                    TextBlock(
                        text=" ".join(w.get("text", "") for w in words),
                        bbox=BoundingBox(x0=x0, top=top, x1=x1, bottom=bottom, page=pnum),
                    )
                )

    return ExtractedDocument(
        doc_id=profile.doc_id,
        pages=pages,
        text_blocks=text_blocks,
        tables=tables,
        raw_text="\n\n".join(raw_parts),
    )


class LayoutExtractor:
    """Strategy B: Layout-aware extraction (Docling preferred, pdfplumber fallback)."""

    strategy_name = "layout"

    def can_handle(self, profile: DocumentProfile) -> bool:
        return True  # Router assigns when not fast_text and not vision-only

    def extract(self, path: Path, profile: DocumentProfile) -> tuple[ExtractedDocument, float]:
        path = Path(path)
        doc = _try_docling_extract(path, profile)
        if doc is None:
            doc = _fallback_pdfplumber_layout(path, profile)
        # Layout strategy typically has good confidence when used for appropriate docs
        confidence = 0.85
        return doc, confidence
