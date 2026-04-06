"""
fetcher.py
----------
High-level interface for fetching city data from Wikidata.

Wraps the low-level wikidata_service with progress tracking
and a cleaner API for the CLI.
"""

from __future__ import annotations

import logging
from typing import Callable

from tqdm import tqdm

from .wikidata_service import fetch_cities as _fetch_cities_raw, SparqlCityInfo

logger = logging.getLogger(__name__)


class CityFetcher:
    """
    High-level fetcher with progress tracking and configuration.
    """
    
    def __init__(
        self,
        max_pages: int = 40,
        page_size: int = 500,
        verbose: bool = False,
    ):
        self.max_pages = max_pages
        self.page_size = page_size
        self.verbose = verbose
    
    def fetch_cities_for_language(self, language: str) -> list[SparqlCityInfo]:
        """
        Fetch all cities for a given language with optional progress tracking.
        
        Args:
            language: Language code (e.g., 'en', 'de')
            
        Returns:
            List of SparqlCityInfo objects
        """
        if self.verbose:
            logger.info(f"Starting fetch for language: {language}")
        
        # Fetch cities using the existing service
        cities = _fetch_cities_raw(language)
        
        if self.verbose:
            logger.info(f"Completed fetch for {language}: {len(cities)} cities")
        
        return cities
