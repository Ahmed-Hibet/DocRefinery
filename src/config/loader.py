"""
Load extraction and chunking configuration from rubric/extraction_rules.yaml.
All thresholds and budgets are externalized so behavior can be tuned and
new domains onboarded without code changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("rubric/extraction_rules.yaml")


def load_config(path: Path | None = None) -> dict[str, Any]:
    """
    Load YAML config from path. Returns nested dict; missing file or key uses safe defaults.
    """
    path = path or DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        # Resolve relative to cwd (or project root if running from repo root)
        path = Path.cwd() / path

    if not path.exists():
        return _default_config()

    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return _default_config()

    if not isinstance(data, dict):
        return _default_config()

    # Merge with defaults so missing keys are filled
    defaults = _default_config()
    return _deep_merge(defaults, data)


def _default_config() -> dict[str, Any]:
    """Default values when YAML is missing or incomplete."""
    return {
        "extraction": {
            "fast_text": {
                "min_chars_per_page": 100,
                "max_image_area_ratio": 0.5,
                "min_confidence_to_accept": 0.6,
            },
            "escalation": {
                "confidence_threshold": 0.6,
            },
            "layout": {
                "prefer_for_layouts": ["multi_column", "table_heavy", "figure_heavy", "mixed"],
                "prefer_for_origin": ["mixed"],
            },
            "vision": {
                "required_for_origin": ["scanned_image"],
                "budget_usd_per_doc": 0.50,
                "max_pages_per_doc": 20,
            },
        },
        "chunking": {
            "table_keep_header_with_cells": True,
            "figure_caption_as_metadata": True,
            "list_max_tokens": 512,
            "list_keep_together": True,
            "section_header_as_parent_metadata": True,
            "resolve_cross_references": True,
            "max_tokens_per_chunk": 512,
            "overlap_tokens": 32,
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. Override wins for leaf values."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_extraction_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return extraction section; use loaded config or defaults."""
    if config is None:
        config = load_config()
    return config.get("extraction", _default_config()["extraction"])


def get_chunking_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return chunking section for ChunkingEngine (Phase 3)."""
    if config is None:
        config = load_config()
    return config.get("chunking", _default_config()["chunking"])
