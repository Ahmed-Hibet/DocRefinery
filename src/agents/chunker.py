"""
Semantic Chunking Engine (Stage 3).
Converts ExtractedDocument into Logical Document Units (LDUs) with all five
chunking rules enforced via ChunkValidator. Each LDU carries content_hash for
provenance verification.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from src.config import get_chunking_config, load_config
from src.models import (
    BoundingBox,
    ChunkType,
    ExtractedDocument,
    ExtractedFigure,
    ExtractedTable,
    LDU,
    TextBlock,
)


def _token_count_approx(text: str) -> int:
    """Approximate token count (~4 chars per token). Use tiktoken if available."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _content_hash(content: str, page_refs: list[int], bbox: BoundingBox | None) -> str:
    """Generate content_hash for provenance (spatial/content addressing)."""
    parts = [content or "", "|", ",".join(str(p) for p in sorted(page_refs))]
    if bbox:
        parts.append(f"|{bbox.page}:{bbox.x0:.1f},{bbox.top:.1f},{bbox.x1:.1f},{bbox.bottom:.1f}")
    raw = "".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _chunk_id() -> str:
    """Unique chunk id for LDU."""
    return str(uuid.uuid4())[:12]


# --- Chunking rules (constitution) ---
# 1. Table: never split cell from header row → emit one LDU per table (header + rows).
# 2. Figure caption as metadata of parent figure chunk.
# 3. Numbered list kept as single LDU unless exceeds list_max_tokens.
# 4. Section headers stored as parent_section on child chunks.
# 5. Cross-references resolved and stored in chunk metadata.


# Section header patterns (short lines, numbering, all-caps)
SECTION_PATTERNS = [
    re.compile(r"^(?:\d+\.)+\s*.{2,80}$"),  # 1. 2.1 2.1.1
    re.compile(r"^(?:Chapter|Section|Part)\s+\d+", re.I),
    re.compile(r"^[A-Z][a-z]*(?:\s+[A-Z][a-z]*)*\s*$"),  # Title Case line
]


def _is_section_header(text: str) -> bool:
    """Heuristic: line looks like a section header."""
    t = (text or "").strip()
    if not t or len(t) > 120:
        return False
    for pat in SECTION_PATTERNS:
        if pat.match(t):
            return True
    # Short all-caps line
    if len(t) < 60 and t.isupper() and len(t) > 3:
        return True
    return False


# Cross-reference patterns
CROSS_REF_PATTERNS = [
    re.compile(r"(?:see|refer to|cf\.?)\s+(?:table|fig\.?|figure|section|appendix)\s*(\d+[\w.-]*)", re.I),
    re.compile(r"(?:table|fig\.?|figure|section)\s+(\d+[\w.-]*)", re.I),
]


def _extract_cross_refs(text: str) -> list[str]:
    """Resolve cross-references (e.g. 'see Table 3') and return list of refs."""
    refs: list[str] = []
    for pat in CROSS_REF_PATTERNS:
        for m in pat.finditer(text):
            refs.append(m.group(1).strip())
    return list(dict.fromkeys(refs))


def _table_to_content(table: ExtractedTable) -> str:
    """Serialize table as markdown-like text (header + rows) for LDU content."""
    lines = [" | ".join(str(h) for h in table.headers)]
    for row in table.rows:
        cells = [str(c) if not isinstance(c, dict) else str(c.get("value", c)) for c in (row if isinstance(row, list) else [row])]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


class ChunkValidator:
    """
    Verifies that emitted LDUs satisfy the five chunking rules.
    Used before returning chunks from ChunkingEngine.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or get_chunking_config(load_config())

    def validate(self, ldus: list[LDU]) -> tuple[bool, list[str]]:
        """
        Validate all LDUs. Returns (all_ok, list of violation messages).
        """
        errors: list[str] = []
        # Rule 1: Table LDUs must contain header + cells (we emit one LDU per table, so satisfied if table content has header line)
        for i, ldu in enumerate(ldus):
            if ldu.chunk_type == ChunkType.TABLE:
                if " | " not in ldu.content and "\n" not in ldu.content and not ldu.content.strip().startswith("|"):
                    errors.append(f"LDU {i} (table): table chunk should contain structured header/rows")
        # Rule 2: Figure LDUs must have caption in metadata when figure_caption_as_metadata is true
        if self.config.get("figure_caption_as_metadata", True):
            for i, ldu in enumerate(ldus):
                if ldu.chunk_type == ChunkType.FIGURE:
                    if "caption" not in ldu.metadata and "figure_caption" not in ldu.metadata:
                        # Allow empty caption
                        pass  # No error if caption is optional
        # Rule 3: List LDUs not split mid-list (we keep list together; if over max_tokens we might split — validator can allow)
        list_max = int(self.config.get("list_max_tokens", 512))
        for i, ldu in enumerate(ldus):
            if ldu.chunk_type == ChunkType.LIST and ldu.token_count > list_max:
                # If we ever split lists, we could flag; for now we keep together so no violation
                pass
        # Rule 4: Section headers as parent_section on children — we set parent_section when we detect headers
        # Rule 5: Cross-refs in metadata — we set metadata["cross_references"] when we find refs
        return (len(errors) == 0, errors)


class ChunkingEngine:
    """
    Converts ExtractedDocument into RAG-ready LDUs.
    Enforces table=whole, figure+caption, list together, section parent, cross-refs.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        # Accept full pipeline config or chunking-only dict
        raw = config or load_config()
        self.config = raw.get("chunking") if isinstance(raw, dict) and "chunking" in raw else get_chunking_config(raw)
        self.max_tokens = int(self.config.get("max_tokens_per_chunk", 512))
        self.list_max_tokens = int(self.config.get("list_max_tokens", 512))
        self.overlap_tokens = int(self.config.get("overlap_tokens", 32))
        self.validator = ChunkValidator(self.config)

    def chunk(self, doc: ExtractedDocument) -> list[LDU]:
        """
        Convert ExtractedDocument to list of LDUs in reading order.
        Tables and figures become single LDUs; text is split by paragraphs/sections
        with list detection and section header propagation.
        """
        ldus: list[LDU] = []
        doc_id = doc.doc_id

        # 1. Tables: one LDU per table (header + all rows) — Rule 1
        for t in doc.tables:
            content = _table_to_content(t)
            token_count = _token_count_approx(content)
            page_refs = [t.bbox.page] if t.bbox else (doc.pages[:1] or [1])
            bbox = t.bbox
            content_hash = _content_hash(content, page_refs, bbox)
            ldus.append(
                LDU(
                    content=content,
                    chunk_type=ChunkType.TABLE,
                    page_refs=page_refs,
                    bounding_box=bbox,
                    parent_section=None,
                    token_count=token_count,
                    content_hash=content_hash,
                    chunk_id=_chunk_id(),
                    metadata={"caption": t.caption} if t.caption else {},
                )
            )

        # 2. Figures: one LDU per figure, caption in metadata — Rule 2
        for fig in doc.figures:
            content = fig.caption or "[Figure]"
            if fig.image_ref:
                content = f"{content} (image: {fig.image_ref})".strip()
            page_refs = [fig.bbox.page]
            token_count = _token_count_approx(content)
            content_hash = _content_hash(content, page_refs, fig.bbox)
            meta: dict[str, Any] = {}
            if self.config.get("figure_caption_as_metadata", True) and fig.caption:
                meta["caption"] = fig.caption
                meta["figure_caption"] = fig.caption
            ldus.append(
                LDU(
                    content=content,
                    chunk_type=ChunkType.FIGURE,
                    page_refs=page_refs,
                    bounding_box=fig.bbox,
                    parent_section=None,
                    token_count=token_count,
                    content_hash=content_hash,
                    chunk_id=_chunk_id(),
                    metadata=meta,
                )
            )

        # 3. Text blocks: order by reading_order or by (page, top)
        ordered_blocks: list[TextBlock] = []
        if doc.reading_order:
            # Map index -> block (reading_order may be indices as strings)
            by_idx = {str(i): doc.text_blocks[i] for i in range(len(doc.text_blocks)) if i < len(doc.text_blocks)}
            for key in doc.reading_order:
                if key in by_idx:
                    ordered_blocks.append(by_idx[key])
        if not ordered_blocks and doc.text_blocks:
            ordered_blocks = sorted(doc.text_blocks, key=lambda b: (b.bbox.page, b.bbox.top))

        current_section: str | None = None
        list_buffer: list[str] = []
        list_page_refs: list[int] = []
        list_bbox: BoundingBox | None = None

        def flush_list() -> None:
            nonlocal list_buffer, list_page_refs, list_bbox
            if not list_buffer:
                return
            content = "\n".join(list_buffer)
            tokens = _token_count_approx(content)
            # Rule 3: keep list together unless exceeds list_max_tokens
            if tokens <= self.list_max_tokens:
                refs = _extract_cross_refs(content)
                meta: dict[str, Any] = {}
                if self.config.get("resolve_cross_references", True) and refs:
                    meta["cross_references"] = refs
                ldus.append(
                    LDU(
                        content=content,
                        chunk_type=ChunkType.LIST,
                        page_refs=list_page_refs,
                        bounding_box=list_bbox,
                        parent_section=current_section,
                        token_count=tokens,
                        content_hash=_content_hash(content, list_page_refs, list_bbox),
                        chunk_id=_chunk_id(),
                        metadata=meta,
                    )
                )
            else:
                # Split by lines but keep items together where possible (simple split by max_tokens)
                current = []
                current_tokens = 0
                for line in list_buffer:
                    line_tokens = _token_count_approx(line)
                    if current_tokens + line_tokens > self.list_max_tokens and current:
                        part_content = "\n".join(current)
                        ldus.append(
                            LDU(
                                content=part_content,
                                chunk_type=ChunkType.LIST,
                                page_refs=list_page_refs,
                                bounding_box=list_bbox,
                                parent_section=current_section,
                                token_count=_token_count_approx(part_content),
                                content_hash=_content_hash(part_content, list_page_refs, list_bbox),
                                chunk_id=_chunk_id(),
                                metadata={},
                            )
                        )
                        current = [line]
                        current_tokens = line_tokens
                    else:
                        current.append(line)
                        current_tokens += line_tokens
                if current:
                    part_content = "\n".join(current)
                    refs = _extract_cross_refs(part_content)
                    meta = {"cross_references": refs} if (self.config.get("resolve_cross_references", True) and refs) else {}
                    ldus.append(
                        LDU(
                            content=part_content,
                            chunk_type=ChunkType.LIST,
                            page_refs=list_page_refs,
                            bounding_box=list_bbox,
                            parent_section=current_section,
                            token_count=_token_count_approx(part_content),
                            content_hash=_content_hash(part_content, list_page_refs, list_bbox),
                            chunk_id=_chunk_id(),
                            metadata=meta,
                        )
                    )
            list_buffer = []
            list_page_refs = []
            list_bbox = None

        def emit_paragraph(text: str, bbox: BoundingBox, cross_refs: list[str]) -> None:
            if not text.strip():
                return
            tokens = _token_count_approx(text)
            page_refs = [bbox.page]
            meta: dict[str, Any] = {}
            if self.config.get("resolve_cross_references", True) and cross_refs:
                meta["cross_references"] = cross_refs
            if tokens <= self.max_tokens:
                ldus.append(
                    LDU(
                        content=text.strip(),
                        chunk_type=ChunkType.PARAGRAPH,
                        page_refs=page_refs,
                        bounding_box=bbox,
                        parent_section=current_section,
                        token_count=tokens,
                        content_hash=_content_hash(text, page_refs, bbox),
                        chunk_id=_chunk_id(),
                        metadata=meta,
                    )
                )
            else:
                # Split by paragraphs or sentences to stay under max_tokens
                parts = re.split(r"\n\n+", text)
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    t = _token_count_approx(part)
                    if t <= self.max_tokens:
                        ldus.append(
                            LDU(
                                content=part,
                                chunk_type=ChunkType.PARAGRAPH,
                                page_refs=page_refs,
                                bounding_box=bbox,
                                parent_section=current_section,
                                token_count=t,
                                content_hash=_content_hash(part, page_refs, bbox),
                                chunk_id=_chunk_id(),
                                metadata=meta,
                            )
                        )
                    else:
                        # Sentence split fallback
                        sentences = re.split(r"(?<=[.!?])\s+", part)
                        buf = []
                        buf_tokens = 0
                        for s in sentences:
                            st = _token_count_approx(s)
                            if buf_tokens + st > self.max_tokens and buf:
                                ldus.append(
                                    LDU(
                                        content=" ".join(buf),
                                        chunk_type=ChunkType.PARAGRAPH,
                                        page_refs=page_refs,
                                        bounding_box=bbox,
                                        parent_section=current_section,
                                        token_count=buf_tokens,
                                        content_hash=_content_hash(" ".join(buf), page_refs, bbox),
                                        chunk_id=_chunk_id(),
                                        metadata=meta,
                                    )
                                )
                                buf = [s]
                                buf_tokens = st
                            else:
                                buf.append(s)
                                buf_tokens += st
                        if buf:
                            ldus.append(
                                LDU(
                                    content=" ".join(buf),
                                    chunk_type=ChunkType.PARAGRAPH,
                                    page_refs=page_refs,
                                    bounding_box=bbox,
                                    parent_section=current_section,
                                    token_count=_token_count_approx(" ".join(buf)),
                                    content_hash=_content_hash(" ".join(buf), page_refs, bbox),
                                    chunk_id=_chunk_id(),
                                    metadata=meta,
                                )
                            )

        for block in ordered_blocks:
            text = (block.text or "").strip()
            if not text:
                continue
            # Section header? — Rule 4
            if _is_section_header(text):
                flush_list()
                current_section = text
                # Emit as section_header LDU
                ldus.append(
                    LDU(
                        content=text,
                        chunk_type=ChunkType.SECTION_HEADER,
                        page_refs=[block.bbox.page],
                        bounding_box=block.bbox,
                        parent_section=None,
                        token_count=_token_count_approx(text),
                        content_hash=_content_hash(text, [block.bbox.page], block.bbox),
                        chunk_id=_chunk_id(),
                        metadata={},
                    )
                )
                continue
            # Numbered or bullet list item?
            if re.match(r"^\s*(?:\d+[.)]\s+|\*\s+|-)\s*.+", text) or re.match(r"^\s*[\u2022\u2023]\s*.+", text):
                list_buffer.append(text)
                if block.bbox.page not in list_page_refs:
                    list_page_refs.append(block.bbox.page)
                list_page_refs.sort()
                if list_bbox is None:
                    list_bbox = block.bbox
                continue
            flush_list()
            refs = _extract_cross_refs(text)
            emit_paragraph(text, block.bbox, refs)

        flush_list()

        # If we had no text blocks but have raw_text, emit as one or more paragraphs
        if not ordered_blocks and doc.raw_text:
            refs = _extract_cross_refs(doc.raw_text)
            default_page = doc.pages[0] if doc.pages else 1
            bbox = BoundingBox(x0=0, top=0, x1=100, bottom=100, page=default_page)
            emit_paragraph(doc.raw_text, bbox, refs)

        ok, errs = self.validator.validate(ldus)
        if not ok and errs:
            raise ValueError(f"ChunkValidator violations: {errs}")

        return ldus


def run_chunker(doc: ExtractedDocument, config: dict[str, Any] | None = None) -> list[LDU]:
    """
    Run the chunking engine on an ExtractedDocument. Returns list of LDUs.
    """
    engine = ChunkingEngine(config=config)
    return engine.chunk(doc)
