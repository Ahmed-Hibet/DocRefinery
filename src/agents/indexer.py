"""
PageIndex Builder (Stage 4).
Builds a hierarchical navigation tree over the document with LLM-generated
section summaries. Supports topic-based traversal to return top-k relevant sections
before vector search.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.models import (
    ChunkType,
    DataTypesPresent,
    LDU,
    PageIndex,
    PageIndexSection,
)


def _section_level(title: str) -> tuple[int, str]:
    """
    Infer heading level from numbering (1. -> 0, 1.1 -> 1, 2.1.3 -> 2).
    Returns (level, normalized_title).
    """
    t = (title or "").strip()
    m = re.match(r"^(\d+(?:\.\d+)*)[.)]\s*(.*)$", t)
    if m:
        num_part = m.group(1)
        rest = m.group(2).strip()
        level = num_part.count(".")  # 1 -> 0, 1.1 -> 1, 1.1.1 -> 2
        return (level, f"{num_part} {rest}" if rest else num_part)
    return (0, t)


def _data_types_from_chunk_type(chunk_type: ChunkType) -> DataTypesPresent | None:
    if chunk_type == ChunkType.TABLE:
        return DataTypesPresent.TABLES
    if chunk_type == ChunkType.FIGURE:
        return DataTypesPresent.FIGURES
    if chunk_type in (ChunkType.PARAGRAPH, ChunkType.SECTION_HEADER, ChunkType.LIST):
        return DataTypesPresent.TEXT
    return None


def _summarize_fallback(content: str, max_sentences: int = 3, max_chars: int = 400) -> str:
    """Fallback when no LLM: first N sentences or chars."""
    if not content or not content.strip():
        return ""
    # First few sentences
    sentences = re.split(r"(?<=[.!?])\s+", content.strip())
    out = " ".join(sentences[:max_sentences]).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 3].rsplit(" ", 1)[0] + "..."
    return out or content[:max_chars]


def _default_summary_fn(section_content: str, section_title: str) -> str:
    """
    Default section summary: use fallback (first 2-3 sentences).
    Replace with LLM call when a fast model is configured.
    """
    return _summarize_fallback(section_content, max_sentences=3, max_chars=350)


def _build_section_tree(
    section_list: list[tuple[str, list[LDU], set[DataTypesPresent]]],
    summary_fn: Callable[[str, str], str],
) -> PageIndexSection:
    """
    Build tree from flat list of (title, ldus, data_types).
    Nest by heading level: 0 = root child, 1 = child of last level-0, etc.
    """
    if not section_list:
        return PageIndexSection(
            title="Document",
            page_start=1,
            page_end=1,
            child_sections=[],
            key_entities=[],
            summary="",
            data_types_present=[],
        )

    root = PageIndexSection(
        title="Document",
        page_start=1,
        page_end=1,
        child_sections=[],
        key_entities=[],
        summary="",
        data_types_present=[],
    )
    # stack[i] = last section at level i (stack[0] = root)
    stack: list[PageIndexSection] = [root]

    for title, ldus, data_types in section_list:
        if not ldus:
            continue
        page_start = min(p for ldu in ldus for p in ldu.page_refs) if ldus else 1
        page_end = max(p for ldu in ldus for p in ldu.page_refs) if ldus else 1
        level, _ = _section_level(title)
        content = " ".join(ldu.content for ldu in ldus if ldu.content)[:2000]
        summary = summary_fn(content, title)
        data_list = list(data_types) if data_types else [DataTypesPresent.TEXT]

        node = PageIndexSection(
            title=title,
            page_start=page_start,
            page_end=page_end,
            child_sections=[],
            key_entities=[],
            summary=summary,
            data_types_present=data_list,
        )

        # Pop stack so stack[level] is the parent we want
        while len(stack) > level + 1:
            stack.pop()
        parent = stack[level]
        parent.child_sections.append(node)
        # New node becomes last at level (level+1) for potential children
        if level + 1 == len(stack):
            stack.append(node)
        else:
            stack[level + 1] = node

        root.page_start = min(root.page_start, page_start) if root.page_start else page_start
        root.page_end = max(root.page_end, page_end)

    if root.child_sections:
        root.page_start = min(s.page_start for s in root.child_sections)
        root.page_end = max(s.page_end for s in root.child_sections)
        root.summary = summary_fn(
            " ".join(s.summary for s in root.child_sections)[:500],
            "Document",
        )

    return root


class PageIndexBuilder:
    """
    Builds PageIndex from LDUs: infers section hierarchy, assigns page ranges,
    and generates section summaries (LLM or fallback).
    """

    def __init__(
        self,
        summary_fn: Callable[[str, str], str] | None = None,
    ):
        self.summary_fn = summary_fn or _default_summary_fn

    def build(self, doc_id: str, ldus: list[LDU], total_pages: int) -> PageIndex:
        """
        Build hierarchical PageIndex from LDUs.
        Sections are inferred from SECTION_HEADER chunks and their following content.
        """
        if total_pages < 1:
            total_pages = 1

        # Group LDUs by section (section_header + following content until next section)
        section_list: list[tuple[str, list[LDU], set[DataTypesPresent]]] = []
        current_title = "Document"
        current_ldus: list[LDU] = []
        current_types: set[DataTypesPresent] = set()

        for ldu in ldus:
            if ldu.chunk_type == ChunkType.SECTION_HEADER:
                if current_ldus:
                    section_list.append((current_title, current_ldus, current_types))
                current_title = ldu.content or "Untitled"
                current_ldus = [ldu]
                dt = _data_types_from_chunk_type(ldu.chunk_type)
                current_types = {dt} if dt else set()
            else:
                current_ldus.append(ldu)
                dt = _data_types_from_chunk_type(ldu.chunk_type)
                if dt:
                    current_types.add(dt)

        if current_ldus:
            section_list.append((current_title, current_ldus, current_types))

        # If no section headers, treat all as one section
        if not section_list and ldus:
            types = set()
            for ldu in ldus:
                dt = _data_types_from_chunk_type(ldu.chunk_type)
                if dt:
                    types.add(dt)
            section_list.append(("Document", ldus, types or {DataTypesPresent.TEXT}))

        if not section_list:
            root = PageIndexSection(
                title="Document",
                page_start=1,
                page_end=total_pages,
                child_sections=[],
                key_entities=[],
                summary="",
                data_types_present=[DataTypesPresent.TEXT],
            )
            return PageIndex(doc_id=doc_id, root=root, total_pages=total_pages)

        root = _build_section_tree(section_list, self.summary_fn)
        return PageIndex(doc_id=doc_id, root=root, total_pages=total_pages)


def build_page_index(
    doc_id: str,
    ldus: list[LDU],
    total_pages: int,
    summary_fn: Callable[[str, str], str] | None = None,
) -> PageIndex:
    """Convenience: build PageIndex from LDUs."""
    builder = PageIndexBuilder(summary_fn=summary_fn)
    return builder.build(doc_id, ldus, total_pages)


def save_page_index(page_index: PageIndex, out_dir: Path) -> Path:
    """Write PageIndex to .refinery/pageindex/{doc_id}.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{page_index.doc_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        f.write(page_index.model_dump_json(indent=2))
    return path


def load_page_index(path: Path) -> PageIndex:
    """Load PageIndex from JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return PageIndex.model_validate(data)


# --- PageIndex query: topic -> top-k sections ---


def _score_section_for_topic(section: PageIndexSection, topic: str) -> float:
    """Score section relevance to topic (keyword overlap on title + summary)."""
    topic_lower = topic.lower().strip()
    if not topic_lower:
        return 0.0
    words = set(re.findall(r"\w+", topic_lower))
    if not words:
        return 0.0
    text = f"{section.title} {section.summary}".lower()
    text_words = set(re.findall(r"\w+", text))
    overlap = len(words & text_words) / max(1, len(words))
    # Bonus for title match
    if any(w in section.title.lower() for w in words):
        overlap += 0.3
    return min(1.0, overlap)


def pageindex_query(
    page_index: PageIndex,
    topic: str,
    top_k: int = 3,
) -> list[PageIndexSection]:
    """
    Traverse PageIndex tree and return top-k sections most relevant to the topic.
    Used before vector search to narrow the retrieval space (section-specific queries).
    """
    if not topic or not topic.strip():
        # Return root's immediate children as default
        return page_index.root.child_sections[:top_k]

    def collect_sections(node: PageIndexSection) -> list[PageIndexSection]:
        out = [node]
        for c in node.child_sections:
            out.extend(collect_sections(c))
        return out

    all_sections = collect_sections(page_index.root)
    # Score and sort
    scored = [(s, _score_section_for_topic(s, topic)) for s in all_sections if s.title != "Document"]
    scored.sort(key=lambda x: -x[1])
    return [s for s, _ in scored[:top_k]]


def pageindex_navigate(
    page_index: PageIndex,
    section_title: str | None = None,
    page_num: int | None = None,
) -> list[PageIndexSection]:
    """
    Navigate the tree: by section title (fuzzy match) or by page number.
    Returns matching section(s) for traversal.
    """
    def collect(node: PageIndexSection) -> list[PageIndexSection]:
        out = []
        if section_title and section_title.lower() in node.title.lower():
            out.append(node)
        if page_num is not None and node.page_start <= page_num <= node.page_end:
            out.append(node)
        for c in node.child_sections:
            out.extend(collect(c))
        return out

    return collect(page_index.root)
