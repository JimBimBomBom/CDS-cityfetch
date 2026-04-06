"""
Output generators for CDS-CityFetch.

Provides pluggable output format generators for city data:
- SQL: MySQL-compatible INSERT statements
- JSON: Structured JSON format
- CSV: Comma-separated values
"""

from .sql_generator import SQLGenerator
from .json_generator import JSONGenerator
from .csv_generator import CSVGenerator

__all__ = ["SQLGenerator", "JSONGenerator", "CSVGenerator"]
