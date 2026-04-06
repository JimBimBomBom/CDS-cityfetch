"""
json_generator.py
-----------------
Generates JSON output for city data.

Creates a structured JSON file with all city information,
easy to import into databases or use in applications.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..wikidata_service import SparqlCityInfo

logger = logging.getLogger(__name__)


class JSONGenerator:
    """Generates JSON output for city data."""
    
    def __init__(self, indent: int = 2):
        self.indent = indent
    
    def _city_to_dict(self, city: SparqlCityInfo) -> dict[str, Any]:
        """Convert a city object to a dictionary."""
        return {
            "city_id": city.wikidata_id,
            "city_name": city.city_name,
            "language": city.language,
            "latitude": city.latitude,
            "longitude": city.longitude,
            "country": city.country,
            "country_code": city.country_code,
            "admin_region": city.admin_region,
            "population": city.population,
        }
    
    def generate(
        self,
        cities: list[SparqlCityInfo],
        output_path: Path,
        work_dir: Path,
    ) -> Path:
        """
        Generate JSON file from city data.
        
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
        temp_path = work_dir / f"cities_{timestamp}.json"
        final_path = output_path
        
        logger.info(
            "Generating JSON: %d unique cities (from %d total).",
            len(unique), len(cities)
        )
        
        # Build the document structure
        document = {
            "metadata": {
                "generated_at": now_utc.strftime('%Y-%m-%d %H:%M:%S UTC'),
                "tool": "CDS-CityFetch",
                "tool_version": "1.0.0",
                "source": "Wikidata",
                "total_records": len(unique),
            },
            "cities": [self._city_to_dict(city) for city in unique],
        }
        
        try:
            with open(temp_path, "w", encoding="utf-8") as fh:
                json.dump(document, fh, indent=self.indent, ensure_ascii=False)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        
        # Atomic rename
        temp_path.replace(final_path)
        
        logger.info("JSON file written to: %s", final_path)
        return final_path
