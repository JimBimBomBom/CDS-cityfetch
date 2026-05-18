"""
wikidata_service.py
-------------------
Fetches maximum city data from Wikidata SPARQL endpoint.

Uses a two-pass approach for reliability:
  - Pass 1: Core data per settlement type (city + strict villages)
  - Pass 2: Combined enrichment (country, population, admin region in one query)

All cities are kept - no filtering. Missing data is stored as null.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
HTTP_TIMEOUT_SECONDS = 60

# Rate limiting - keeps us within Wikidata's limits
BATCH_SIZE = 200
DELAY_BETWEEN_BATCHES = 1.0
MAX_RETRIES = 3
RETRY_DELAY = 2.0

_HEADERS = {
    "User-Agent": "CDS-CityFetch/2.1 (github.com/filip CDS-CityFetch; filip.dvorak13@gmail.com)",
    "Accept": "text/csv; charset=utf-8",
}


@dataclass
class CityData:
    """Represents a city record."""
    wikidata_id: str
    city_name: str
    language: str
    latitude: float
    longitude: float
    country: Optional[str] = None
    country_code: Optional[str] = None
    admin_region: Optional[str] = None
    population: Optional[int] = None


def _execute_query(query: str, language: str, batch_name: str) -> list[dict]:
    """Execute SPARQL query with retry logic."""
    delay = RETRY_DELAY
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(headers=_HEADERS, timeout=HTTP_TIMEOUT_SECONDS) as client:
                response = client.post(SPARQL_ENDPOINT, data={"query": query})
                
                if response.status_code == 429:
                    retry_after = int(response.headers.get("retry-after", delay))
                    logger.warning(f"[{language}] {batch_name} - Rate limited, waiting {retry_after}s")
                    time.sleep(retry_after)
                    delay = min(delay * 2, 30)
                    continue
                
                if response.status_code >= 500:
                    logger.warning(f"[{language}] {batch_name} - Server error {response.status_code}, retrying...")
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                
                if not response.is_success:
                    logger.error(f"[{language}] {batch_name} - Failed: HTTP {response.status_code}")
                    return []
                
                csv_text = response.text.replace('\r\n', '\n').replace('\r', '\n')
                return list(csv.DictReader(io.StringIO(csv_text)))
                
        except Exception as exc:
            logger.warning(f"[{language}] {batch_name} - Error: {exc}, retrying...")
            time.sleep(delay)
            delay = min(delay * 2, 30)
    
    logger.error(f"[{language}] {batch_name} - All retries exhausted")
    return []


def _chunk(items: list, size: int) -> list[list]:
    """Split list into chunks."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _extract_qid(uri: str) -> str:
    """Extract QID from a Wikidata URI."""
    return uri.rsplit("/", 1)[-1] if uri else ""


# Settlement types we query individually to keep each query lightweight.
# The value is a tuple of (Wikidata QID, optional extra SPARQL constraints).
# Q515 "city" is the core type and covers towns and capital cities automatically.
# Q532 "village" is extremely broad, so we restrict to substantial villages only:
#   - at least 20 Wikipedia sitelinks (well-known internationally)
#   - population > 1,000 (genuine settlement, not a hamlet)
_SETTLEMENT_TYPES: dict[str, tuple[str, str]] = {
    "city": ("wd:Q515", ""),
    "village": (
        "wd:Q532",
        """          ?city wikibase:sitelinks ?sitelinks .
          FILTER(?sitelinks >= 20)
          ?city wdt:P1082 ?pop .
          FILTER(?pop > 1000)""",
    ),
}


def fetch_cities(language: str) -> list[CityData]:
    """
    Fetch all city data for a language using lightweight per-type queries.
    
    Pass 1 fetches core data per settlement type (city + strict villages).
    Pass 2 runs a single combined enrichment query for country, population,
    and admin region per batch.
    """
    logger.info(f"[{language}] Starting lightweight fetch...")
    
    # ====================================================================
    # PASS 1: Core Data per settlement type (lightweight, no ORDER BY)
    # ====================================================================
    cities: dict[str, CityData] = {}
    
    for type_name, (type_qid, extra_filter) in _SETTLEMENT_TYPES.items():
        logger.info(f"[{language}] Pass 1: Fetching {type_name}...")

        query = f"""
        SELECT DISTINCT ?city ?cityLabel ?lat ?lon WHERE {{
          ?city wdt:P31/wdt:P279* {type_qid} .
          ?city wdt:P625 ?coord .{extra_filter}
          BIND(geof:latitude(?coord) AS ?lat)
          BIND(geof:longitude(?coord) AS ?lon)
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},en" }}
        }}
        LIMIT 100000"""
        
        rows = _execute_query(query, language, f"base-{type_name}")
        new_count = 0
        for row in rows:
            try:
                qid = _extract_qid(row.get("city", ""))
                if not qid or qid in cities:
                    continue
                
                label = row.get("cityLabel", "").strip()
                # wikibase:label falls back to the QID string when no label exists;
                # treat that as missing so we don't store "Q12345" as a city name
                if label.startswith("Q") and label[1:].isdigit():
                    label = ""
                
                cities[qid] = CityData(
                    wikidata_id=qid,
                    city_name=label,
                    language=language,
                    latitude=float(row.get("lat", 0)),
                    longitude=float(row.get("lon", 0)),
                )
                new_count += 1
            except Exception:
                continue
        
        logger.info(
            f"[{language}] {type_name}: {len(rows)} rows, "
            f"{new_count} new cities, total: {len(cities)}"
        )
        time.sleep(DELAY_BETWEEN_BATCHES)
    
    if not cities:
        logger.error(f"[{language}] Failed to fetch any core data")
        return []
    
    city_ids = list(cities.keys())
    total = len(cities)
    logger.info(f"[{language}] Pass 1 complete: {total} unique cities")
    
    # ====================================================================
    # PASS 2: Enrichment (country + population + admin region in one shot)
    # ====================================================================
    logger.info(f"[{language}] Pass 2/2: Enriching country, population & admin region...")
    
    batches = _chunk(city_ids, BATCH_SIZE)
    failed = 0
    
    for i, batch in enumerate(batches, 1):
        values = " ".join(f"wd:{qid}" for qid in batch)
        query = f"""
        SELECT ?city ?countryLabel ?pop ?adminLabel WHERE {{
          VALUES ?city {{ {values} }}
          OPTIONAL {{
            ?city wdt:P17 ?country .
            ?country rdfs:label ?countryLabel .
            FILTER(LANG(?countryLabel) = "{language}")
          }}
          OPTIONAL {{ ?city wdt:P1082 ?pop }}
          OPTIONAL {{
            ?city wdt:P131 ?admin .
            ?admin rdfs:label ?adminLabel .
            FILTER(LANG(?adminLabel) = "{language}")
          }}
        }}"""
        
        rows = _execute_query(query, language, f"enrich-{i}/{len(batches)}")
        if rows:
            for row in rows:
                try:
                    qid = _extract_qid(row.get("city", ""))
                    if qid not in cities:
                        continue
                    
                    if val := row.get("countryLabel", "").strip():
                        cities[qid].country = val
                    if val := row.get("pop", "").strip():
                        cities[qid].population = int(float(val))
                    if val := row.get("adminLabel", "").strip():
                        cities[qid].admin_region = val
                except (ValueError, TypeError):
                    continue
        else:
            failed += 1
        
        if i % 10 == 0 or i == len(batches):
            with_country = sum(1 for c in cities.values() if c.country)
            with_pop = sum(1 for c in cities.values() if c.population)
            with_admin = sum(1 for c in cities.values() if c.admin_region)
            logger.info(
                f"[{language}] Enrich: {i}/{len(batches)} batches, "
                f"C={with_country} P={with_pop} A={with_admin}/{total}"
            )
        
        if i < len(batches):
            time.sleep(DELAY_BETWEEN_BATCHES)
    
    with_country = sum(1 for c in cities.values() if c.country)
    with_pop = sum(1 for c in cities.values() if c.population)
    with_admin = sum(1 for c in cities.values() if c.admin_region)
    logger.info(
        f"[{language}] Pass 2 complete: "
        f"country={with_country}, pop={with_pop}, admin={with_admin} "
        f"({failed} failed batches)"
    )
    
    # ====================================================================
    # SUMMARY
    # ====================================================================
    result = list(cities.values())
    
    logger.info(f"[{language}] === FETCH COMPLETE ===")
    logger.info(f"[{language}] Total cities: {len(result)}")
    if result:
        logger.info(f"[{language}] With country: {with_country} ({100*with_country//len(result)}%)")
        logger.info(f"[{language}] With population: {with_pop} ({100*with_pop//len(result)}%)")
        logger.info(f"[{language}] With admin region: {with_admin} ({100*with_admin//len(result)}%)")
    
    return result
