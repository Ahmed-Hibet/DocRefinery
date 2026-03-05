# Config — load extraction_rules.yaml and expose extraction/chunking sections

from src.config.loader import (
    get_chunking_config,
    get_extraction_config,
    load_config,
)

__all__ = ["load_config", "get_extraction_config", "get_chunking_config"]
