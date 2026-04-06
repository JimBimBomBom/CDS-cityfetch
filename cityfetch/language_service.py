"""
language_service.py
-------------------
Handles language code parsing and validation.

Provides utilities for normalizing and validating language codes
for use with Wikidata SPARQL queries.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback language list used when no override is provided
_FALLBACK_LANGUAGES = [
    "en", "cs", "sk", "de", "fr", "es", "it", "pt",
    "pl", "nl", "ru", "ja", "zh", "ar", "ko", "sv",
    "tr", "fi", "hu", "no",
]


def _normalise_code(code: str) -> str:
    """
    Strip region subtag: 'en-US' → 'en', 'zh-Hant' → 'zh'.
    Wikidata uses the base language tag.
    """
    return code.split("-")[0].lower()


def fetch_language_codes(override: Optional[str] = None) -> list[str]:
    """
    Return a deduplicated, ordered list of Wikidata-compatible language codes.
    
    If override is provided (comma-separated like "en,de,fr"), 
    it is parsed and returned immediately.
    
    Otherwise, falls back to a built-in list of common languages.
    
    Args:
        override: Optional comma-separated language codes
        
    Returns:
        List of normalized language codes
    """
    if override is not None:
        codes = [_normalise_code(c.strip()) for c in override.split(",") if c.strip()]
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique = [c for c in codes if not (c in seen or seen.add(c))]
        logger.info("Using language override list (%d): %s", len(unique), unique)
        return unique

    logger.info("Using fallback language list: %s", _FALLBACK_LANGUAGES)
    return list(_FALLBACK_LANGUAGES)
