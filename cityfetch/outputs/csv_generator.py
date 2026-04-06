"""
csv_generator.py
------------------
Generates CSV output for city data.

Creates a comma-separated values file with headers,
easy to import into Excel, databases, or data pipelines.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..wikidata_service import SparqlCityInfo

logger = logging.getLogger(__name__)


class CSVGenerator:
    """Generates CSV output for city data."""
    
    def __init__(self, delimiter: str = ","):
        self.delimiter = delimiter
    
    def _city_to_row(self, city: SparqlCityInfo) -> list[str]:
        """Convert a city object to a CSV row."""
        return [
            city.wikidata_id,
            city.city_name,
            city.language,
            str(city.latitude),
            str(city.longitude),
            city.country or "",
            city.country_code or "",
            city.admin_region or "",
            str(city.population) if city.population is not None else "",
        ]
    
    def generate(
        self,
        cities: list[SparqlCityInfo],
        output_path: Path,
        work_dir: Path,
    ) -> Path:
        """
        Generate CSV file from city data.
        
        Args:
            cities: List of city data objects
            output_path: Final output file path
            work_dir: Working directory for temporary files
            
        Returns:
            Path to the generated file
        """
        # Deduplicate by wikidata_id
        seen: set[str] = set()
        unique: list[SparqlCityInfo] = []
        for city in cities:
            if city.wikidata_id not in seen:
                seen.add(city.wikidata_id)
                unique.append(city)
        
        now_utc = datetime.now(timezone.utc)
        timestamp = now_utc.strftime("%Y%m%d_%H%M%S")
        temp_path = work_dir / f"cities_{timestamp}.csv"
        final_path = output_path
        
        logger.info(
            "Generating CSV: %d unique cities (from %d total).",
            len(unique), len(cities)
        )
        
        headers = [
            "city_id",
            "city_name",
            "language",
            "latitude",
            "longitude",
            "country",
            "country_code",
            "admin_region",
            "population",
        ]
        
        try:
            with open(temp_path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh, delimiter=self.delimiter)
                writer.writerow(headers)
                for city in unique:
                    writer.writerow(self._city_to_row(city))
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        
        # Atomic rename
        temp_path.replace(final_path)
        
        logger.info("CSV file written to: %s", final_path)
        return final_path
