"""
Core Pydantic schemas for the Document Intelligence Refinery.
All stages consume and produce these typed structures.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --- Enums for classification dimensions ---


class OriginType(str, Enum):
    """How the document was produced; affects extraction strategy."""

    NATIVE_DIGITAL = "native_digital"
    SCANNED_IMAGE = "scanned_image"
    MIXED = "mixed"
    FORM_FILLABLE = "form_fillable"


class LayoutComplexity(str, Enum):
    """Layout structure; influences strategy and chunking."""

    SINGLE_COLUMN = "single_column"
    MULTI_COLUMN = "multi_column"
    TABLE_HEAVY = "table_heavy"
    FIGURE_HEAVY = "figure_heavy"
    MIXED = "mixed"


class DomainHint(str, Enum):
    """Domain for extraction prompt strategy selection."""

    FINANCIAL = "financial"
    LEGAL = "legal"
    TECHNICAL = "technical"
    MEDICAL = "medical"
    GENERAL = "general"


class EstimatedCost(str, Enum):
    """Estimated extraction cost tier from DocumentProfile."""

    FAST_TEXT_SUFFICIENT = "fast_text_sufficient"
    NEEDS_LAYOUT_MODEL = "needs_layout_model"
    NEEDS_VISION_MODEL = "needs_vision_model"


# --- DocumentProfile (Stage 1 output) ---


class DocumentProfile(BaseModel):
    """Classification result from the Triage Agent. Governs downstream extraction."""

    doc_id: str = Field(..., description="Unique document identifier (e.g. filename hash)")
    origin_type: OriginType
    layout_complexity: LayoutComplexity
    language: str = Field(default="en", description="Detected language code")
    language_confidence: float = Field(default=1.0, ge=0, le=1)
    domain_hint: DomainHint = DomainHint.GENERAL
    estimated_extraction_cost: EstimatedCost
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Structure extraction (normalized representation) ---


class BoundingBox(BaseModel):
    """Page-relative bounding box (e.g. pdfplumber-style: x0, top, x1, bottom in points)."""

    x0: float
    top: float
    x1: float
    bottom: float
    page: int = Field(..., ge=1)

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.top, self.x1, self.bottom)


class TextBlock(BaseModel):
    """A contiguous text region with spatial provenance."""

    text: str
    bbox: BoundingBox
    font_name: str | None = None
    font_size: float | None = None


class TableCell(BaseModel):
    """Single table cell."""

    value: str | int | float
    row_span: int = 1
    col_span: int = 1


class ExtractedTable(BaseModel):
    """Structured table: headers + rows with optional bbox."""

    headers: list[str]
    rows: list[list[TableCell | str | int | float]]
    bbox: BoundingBox | None = None
    caption: str | None = None


class ExtractedFigure(BaseModel):
    """Figure with optional caption and bbox."""

    caption: str | None = None
    bbox: BoundingBox
    image_ref: str | None = None  # path or blob id if image extracted


class ExtractedDocument(BaseModel):
    """
    Normalized representation of document content.
    All extraction strategies (Fast Text, Layout, Vision) must output this schema.
    """

    doc_id: str
    pages: list[int] = Field(default_factory=list)
    text_blocks: list[TextBlock] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    figures: list[ExtractedFigure] = Field(default_factory=list)
    reading_order: list[str] = Field(
        default_factory=list,
        description="Ordered IDs or indices for reading order of blocks",
    )
    raw_text: str = ""  # fallback full text when structure is minimal


# --- Semantic Chunking (LDUs) ---


class ChunkType(str, Enum):
    """Type of logical document unit."""

    PARAGRAPH = "paragraph"
    TABLE = "table"
    FIGURE = "figure"
    LIST = "list"
    SECTION_HEADER = "section_header"
    FOOTNOTE = "footnote"
    OTHER = "other"


class LDU(BaseModel):
    """Logical Document Unit — RAG-ready, semantically coherent chunk."""

    content: str
    chunk_type: ChunkType
    page_refs: list[int] = Field(default_factory=list, description="Page numbers (1-based); each >= 1")
    bounding_box: BoundingBox | None = None
    parent_section: str | None = None
    token_count: int = Field(default=0, ge=0)
    content_hash: str = Field(default="")
    chunk_id: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- PageIndex (hierarchical navigation) ---


class DataTypesPresent(str, Enum):
    TABLES = "tables"
    FIGURES = "figures"
    EQUATIONS = "equations"
    TEXT = "text"


class PageIndexSection(BaseModel):
    """One node in the PageIndex tree."""

    title: str
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    child_sections: list["PageIndexSection"] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    summary: str = Field(default="")
    data_types_present: list[DataTypesPresent] = Field(default_factory=list)


# Allow self-reference for tree
PageIndexSection.model_rebuild()


class PageIndex(BaseModel):
    """Hierarchical navigation over a document (smart table of contents)."""

    doc_id: str
    root: PageIndexSection
    total_pages: int = Field(..., ge=1)


# --- Provenance (audit trail) ---


class ProvenanceCitation(BaseModel):
    """Single source citation for a claim."""

    document_name: str
    page_number: int = Field(..., ge=1)
    bbox: BoundingBox | None = None
    content_hash: str = ""
    excerpt: str = Field(default="", description="Snippet of source content")


class ProvenanceChain(BaseModel):
    """List of source citations attached to every answer."""

    citations: list[ProvenanceCitation] = Field(default_factory=list)

    def add(self, document_name: str, page_number: int, bbox: BoundingBox | None = None, content_hash: str = "", excerpt: str = "") -> None:
        self.citations.append(
            ProvenanceCitation(
                document_name=document_name,
                page_number=page_number,
                bbox=bbox,
                content_hash=content_hash,
                excerpt=excerpt,
            )
        )


# --- Query Agent (Stage 5) ---


class QueryAnswer(BaseModel):
    """Answer from the Query Interface Agent; every answer carries full provenance."""

    answer: str = Field(..., description="Natural language or structured answer")
    provenance: ProvenanceChain = Field(default_factory=ProvenanceChain)


class AuditResult(BaseModel):
    """Result of Audit Mode: claim verification with source or unverifiable."""

    verified: bool = False
    citation: ProvenanceCitation | None = None
    message: str = Field(default="", description="e.g. 'Verified' or 'Not found / unverifiable'")
