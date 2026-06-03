"""Ingestion layer: canonical patent-number normalization."""

from .numbers import (
    CanonicalNumber,
    DocType,
    Office,
    normalize,
)

__all__ = ["CanonicalNumber", "DocType", "Office", "normalize"]
