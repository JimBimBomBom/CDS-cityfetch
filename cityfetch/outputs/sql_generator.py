"""
sql_generator.py
----------------
Generates MySQL-compatible SQL output for city data.

Creates INSERT ... ON DUPLICATE KEY UPDATE statements in batches
for efficient import into MySQL databases.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..wikidata_service import SparqlCityInfo

logger = logging.getLogger(__name__)


class SQLGenerator:
    """Generates MySQL SQL output for city data."""
    
    def __init__(self, batch_size: int = 1000):
        self.batch_size = batch_size
    
    def _escape_sql(self, value: str) -> str:
        """Escape single quotes and backslashes for MySQL."""
        return value.replace("\\", "\\\\").replace("'", "''")
    
    def _sql_str(self, value: Optional[str]) -> str:
        """Return a quoted SQL string or NULL."""
        if value is None:
            return "NULL"
        return f"'{self._escape_sql(value)}'"
    
    def _sql_int(self, value: Optional[int]) -> str:
        """Return an integer literal or NULL."""
        return "NULL" if value is None else str(value)
    
    def _write_batch(self, lines: list[str], batch: list[SparqlCityInfo]) -> None:
        """Append an INSERT ... ON DUPLICATE KEY UPDATE block."""
        lines.append(
            "INSERT INTO cities "
            "(CityId, CityName, Latitude, Longitude, CountryCode, Country, AdminRegion, Population) "
            "VALUES"
        )
        
        for i, city in enumerate(batch):
            comma = "" if i == len(batch) - 1 else ","
            row = (
                f"    ({self._sql_str(city.wikidata_id)}, {self._sql_str(city.city_name)}, "
                f"{city.latitude:.8f}, {city.longitude:.8f}, "
                f"{self._sql_str(city.country_code)}, {self._sql_str(city.country)}, "
                f"{self._sql_str(city.admin_region)}, {self._sql_int(city.population)}){comma}"
            )
            lines.append(row)
        
        lines.append("ON DUPLICATE KEY UPDATE")
        lines.append("    CityName = VALUES(CityName),")
        lines.append("    Latitude = VALUES(Latitude),")
        lines.append("    Longitude = VALUES(Longitude),")
        lines.append("    CountryCode = VALUES(CountryCode),")
        lines.append("    Country = VALUES(Country),")
        lines.append("    AdminRegion = VALUES(AdminRegion),")
        lines.append("    Population = VALUES(Population);")
        lines.append("")
    
    def generate(
        self,
        cities: list[SparqlCityInfo],
        output_path: Path,
        work_dir: Path,
    ) -> Path:
        """
        Generate SQL file from city data.
        
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
        temp_path = work_dir / f"cities_{timestamp}.sql"
        final_path = output_path
        
        logger.info(
            "Generating SQL: %d unique cities (from %d total).",
            len(unique), len(cities)
        )
        
        lines: list[str] = [
            "-- Auto-generated SQL file from Wikidata via CDS-CityFetch",
            f"-- Generated at: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"-- Total records: {len(unique)}",
            "-- Format: INSERT ... ON DUPLICATE KEY UPDATE",
            "-- Tool: https://github.com/filip/cds-cityfetch",
            "",
            "USE CityDistanceService;",
            "",
        ]
        
        for batch_start in range(0, len(unique), self.batch_size):
            batch = unique[batch_start:batch_start + self.batch_size]
            self._write_batch(lines, batch)
        
        sql_content = "\n".join(lines)
        
        try:
            with open(temp_path, "w", encoding="utf-8") as fh:
                fh.write(sql_content)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        
        # Atomic rename
        temp_path.replace(final_path)
        
        logger.info("SQL file written to: %s", final_path)
        return final_path
