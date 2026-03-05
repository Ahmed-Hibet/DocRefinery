"""Base interface for extraction strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.models import DocumentProfile, ExtractedDocument


class BaseExtractor(ABC):
    """Shared interface for Fast Text, Layout, and Vision extractors."""

    @property
    def strategy_name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def extract(self, path: Path, profile: DocumentProfile) -> tuple[ExtractedDocument, float]:
        """
        Extract document content. Returns (ExtractedDocument, confidence_score in [0, 1]).
        """
        ...

    def can_handle(self, profile: DocumentProfile) -> bool:
        """Whether this strategy is applicable for the given profile. Override if needed."""
        return True
