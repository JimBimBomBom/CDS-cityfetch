"""
merge.py
--------
Client-side merge functionality for combining existing and new city data.

Implements upsert strategy: new data overwrites existing records on conflict.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Optional

from .wikidata_service import SparqlCityInfo

logger = logging.getLogger(__name__)


class DataMerger:
    """Merge existing data with newly fetched data using upsert strategy."""
    
    def __init__(self):
        self.existing_cities: dict[str, SparqlCityInfo] = {}
    
    def load_sql(self, sql_file: Path) -> list[SparqlCityInfo]:
        """
        Parse SQL file and extract city records.
        
        Handles INSERT statements with VALUES clauses.
        """
        cities = []
        content = sql_file.read_text(encoding="utf-8")
        
        # Find all VALUES rows in INSERT statements
        # Pattern: ('Q90', 'Paris', 48.85341000, 2.34880000, 'FR', ...),
        pattern = r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*([\d.-]+)\s*,\s*([\d.-]+)\s*,\s*(?:'([^']*)'|NULL)\s*,\s*(?:'([^']*)'|NULL)\s*,\s*(?:'([^']*)'|NULL)\s*,\s*(?:(\d+|NULL))\s*\)"
        
        for match in re.finditer(pattern, content):
            try:
                city = SparqlCityInfo(
                    wikidata_id=match.group(1),
                    city_name=match.group(2),
                    language="unknown",  # Will be set from new data
                    latitude=float(match.group(3)),
                    longitude=float(match.group(4)),
                    country_code=match.group(5) if match.group(5) else None,
                    country=match.group(6) if match.group(6) else None,
                    admin_region=match.group(7) if match.group(7) else None,
                    population=int(match.group(8)) if match.group(8) and match.group(8) != "NULL" else None,
                )
                cities.append(city)
            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse SQL row: {e}")
                continue
        
        logger.info(f"Loaded {len(cities)} cities from SQL file")
        return cities
    
    def load_json(self, json_file: Path) -> list[SparqlCityInfo]:
        """Parse JSON file and extract city records."""
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        cities = []
        for record in data.get("cities", []):
            try:
                city = SparqlCityInfo(
                    wikidata_id=record["city_id"],
                    city_name=record["city_name"],
                    language=record.get("language", "unknown"),
                    latitude=record["latitude"],
                    longitude=record["longitude"],
                    country=record.get("country"),
                    country_code=record.get("country_code"),
                    admin_region=record.get("admin_region"),
                    population=record.get("population"),
                )
                cities.append(city)
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse JSON record: {e}")
                continue
        
        logger.info(f"Loaded {len(cities)} cities from JSON file")
        return cities
    
    def load_csv(self, csv_file: Path) -> list[SparqlCityInfo]:
        """Parse CSV file and extract city records."""
        cities = []
        
        with open(csv_file, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    pop = row.get("population", "")
                    city = SparqlCityInfo(
                        wikidata_id=row["city_id"],
                        city_name=row["city_name"],
                        language=row.get("language", "unknown"),
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"]),
                        country=row.get("country") or None,
                        country_code=row.get("country_code") or None,
                        admin_region=row.get("admin_region") or None,
                        population=int(pop) if pop else None,
                    )
                    cities.append(city)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Failed to parse CSV row: {e}")
                    continue
        
        logger.info(f"Loaded {len(cities)} cities from CSV file")
        return cities
    
    def load_existing(self, file_path: Path) -> list[SparqlCityInfo]:
        """Load existing data from file (auto-detect format)."""
        file_path = Path(file_path)
        
        if not file_path.exists():
            logger.info(f"Existing file not found: {file_path}")
            return []
        
        suffix = file_path.suffix.lower()
        
        if suffix == ".sql":
            return self.load_sql(file_path)
        elif suffix == ".json":
            return self.load_json(file_path)
        elif suffix == ".csv":
            return self.load_csv(file_path)
        else:
            raise ValueError(f"Unsupported file format: {suffix}")
    
    def merge(
        self,
        existing: list[SparqlCityInfo],
        new_data: list[SparqlCityInfo],
        strategy: str = "upsert"
    ) -> list[SparqlCityInfo]:
        """
        Merge existing and new data.
        
        Args:
            existing: Cities from existing file
            new_data: Cities from fresh Wikidata fetch
            strategy: "upsert" (default) - new overwrites old on conflict
            
        Returns:
            Merged list of cities
        """
        # Build index of existing cities by ID
        existing_by_id = {city.wikidata_id: city for city in existing}
        
        # Track statistics
        added = 0
        updated = 0
        unchanged = 0
        
        # Process new data
        merged = []
        seen_ids = set()
        
        for city in new_data:
            if city.wikidata_id in seen_ids:
                continue  # Skip duplicates in new data
            seen_ids.add(city.wikidata_id)
            
            if city.wikidata_id in existing_by_id:
                if strategy == "upsert":
                    # New data wins
                    merged.append(city)
                    updated += 1
                else:
                    # Existing data preserved
                    merged.append(existing_by_id[city.wikidata_id])
                    unchanged += 1
            else:
                # New city
                merged.append(city)
                added += 1
        
        # Add existing cities not in new data (preserved)
        for city_id, city in existing_by_id.items():
            if city_id not in seen_ids:
                merged.append(city)
                unchanged += 1
        
        logger.info(
            f"Merge complete: {added} added, {updated} updated, "
            f"{unchanged} unchanged, {len(merged)} total"
        )
        
        return merged
