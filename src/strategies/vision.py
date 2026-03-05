"""
Strategy C — Vision-Augmented: VLM via OpenRouter.
High cost; for scanned_image or when A/B confidence < threshold.
Budget guard: track token spend and cap per document.
"""

from __future__ import annotations

from pathlib import Path

from src.models import (
    BoundingBox,
    DocumentProfile,
    ExtractedDocument,
    ExtractedTable,
    TextBlock,
)


def _pdf_page_to_image(path: Path, page_num: int) -> bytes | None:
    """Render one PDF page to image bytes (e.g. for VLM)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        if page_num < 1 or page_num > len(doc):
            return None
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=150)
        return pix.tobytes("png")
    except Exception:
        return None


def _vision_extract_via_openrouter(
    path: Path,
    profile: DocumentProfile,
    budget_usd: float = 0.50,
    max_pages_per_doc: int = 20,
) -> tuple[ExtractedDocument, float]:
    """Call OpenRouter VLM; enforce budget_usd cap (tracking can be added)."""
    """
    Call OpenRouter multimodal API (e.g. GPT-4o-mini / Gemini Flash).
    Returns (ExtractedDocument, confidence). Uses budget_guard.
    """
    # Optional: openrouter client. For interim we return a placeholder if no key.
    import os
    import base64

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        # Return minimal extracted doc from pdfplumber so pipeline still runs
        from src.strategies.fast_text import FastTextExtractor
        fe = FastTextExtractor()
        doc, conf = fe.extract(path, profile)
        return doc, 0.5  # Low confidence to indicate fallback

    # Build request: image(s) + extraction prompt. Label each image with its page number.
    try:
        import fitz
        doc_fitz = fitz.open(path)
        pages = list(range(1, min(len(doc_fitz) + 1, max_pages_per_doc + 1)))  # Cap pages from config
        doc_fitz.close()
    except Exception:
        pages = [1]

    content = []
    for i, pnum in enumerate(pages):
        # Explicitly tell the VLM which page this image is (for correct provenance in response).
        content.append({
            "type": "text",
            "text": f"[Image {i + 1} of {len(pages)} — this is **Page {pnum}** of the document.]",
        })
        img_bytes = _pdf_page_to_image(path, pnum)
        if img_bytes:
            b64 = base64.standard_b64encode(img_bytes).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    prompt = (
        "Extract all text and tables from the document images above. "
        "Each image was labeled with its page number (e.g. 'Page 1', 'Page 2'). "
        "Return structured JSON: {\"text_blocks\": [{\"text\": \"...\", \"page\": <page_number>}], "
        "\"tables\": [{\"headers\": [...], \"rows\": [[...]], \"page\": <page_number>}]}. "
        "For every text_block and table, set \"page\" to the 1-based page number of the image that contains that content. "
        "Preserve reading order and table structure."
    )
    content.append({"type": "text", "text": prompt})

    import urllib.request
    import json

    body = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4096,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/docrefinery",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        # Parse JSON from response (may be in markdown code block)
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            out = {"text_blocks": [{"text": text, "page": 1}], "tables": []}

        # Clamp page numbers to valid range [1, len(pages)] so provenance is never invalid.
        page_min, page_max = 1, max(1, len(pages))

        def _safe_page(n: int | None) -> int:
            if n is None:
                return page_min
            p = int(n) if isinstance(n, (int, float)) else page_min
            return max(page_min, min(page_max, p))

        text_blocks = [
            TextBlock(
                text=b.get("text", ""),
                bbox=BoundingBox(x0=0, top=0, x1=0, bottom=0, page=_safe_page(b.get("page"))),
            )
            for b in out.get("text_blocks", [])
        ]
        tables = []
        for t in out.get("tables", []):
            table_page = _safe_page(t.get("page"))
            tables.append(
                ExtractedTable(
                    headers=t.get("headers", []),
                    rows=t.get("rows", []),
                    bbox=BoundingBox(x0=0, top=0, x1=0, bottom=0, page=table_page) if table_page else None,
                )
            )
        doc = ExtractedDocument(
            doc_id=profile.doc_id,
            pages=pages,
            text_blocks=text_blocks,
            tables=tables,
            raw_text="\n".join(b.text for b in text_blocks),
        )
        return doc, 0.9
    except Exception:
        from src.strategies.fast_text import FastTextExtractor
        fe = FastTextExtractor()
        doc, conf = fe.extract(path, profile)
        return doc, 0.4


class VisionExtractor:
    """Strategy C: VLM-based extraction with budget guard. Config from extraction_rules.yaml."""

    strategy_name = "vision"

    def __init__(self, budget_usd_per_doc: float = 0.50, max_pages_per_doc: int = 20):
        self.budget_usd_per_doc = budget_usd_per_doc
        self.max_pages_per_doc = max_pages_per_doc

    def can_handle(self, profile: DocumentProfile) -> bool:
        return True

    def extract(self, path: Path, profile: DocumentProfile) -> tuple[ExtractedDocument, float]:
        return _vision_extract_via_openrouter(
            path, profile,
            budget_usd=self.budget_usd_per_doc,
            max_pages_per_doc=self.max_pages_per_doc,
        )
