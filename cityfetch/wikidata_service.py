"""
wikidata_service.py
-------------------
Fetches city data from the Wikidata SPARQL endpoint for a given language.

Optimized for performance and reliability:
  - Paginated requests (PAGE_SIZE rows per request, up to MAX_PAGES pages)
  - Deduplication guard (OFFSET-boundary shifts on live data can overlap)
  - Polite delays between pages to respect Wikidata's service
  - Automatic retries on transient failures (5xx, 429)
  - Strips control characters that JSON parsers reject
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
PAGE_SIZE = 500
MAX_PAGES = 40
PAGE_DELAY_SECONDS = 10
HTTP_TIMEOUT_SECONDS = 180  # 3 minutes

# Retry configuration for transient failures
MAX_PAGE_RETRIES = max(0, int(os.environ.get("MAX_PAGE_RETRIES", "3")))
RETRY_BASE_DELAY_SECONDS = max(1, int(os.environ.get("RETRY_BASE_DELAY_SECONDS", "30")))

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_HEADERS = {
    "User-Agent": "CDS-CityFetch/1.0 (github.com/filip CDS-CityFetch; filip.dvorak13@gmail.com)",
    "Accept": "application/sparql-results+json",
}


@dataclass
class SparqlCityInfo:
    """Represents a city record from Wikidata."""
    wikidata_id: str
    city_name: str
    language: str
    latitude: float
    longitude: float
    country: Optional[str] = None
    country_code: Optional[str] = None
    admin_region: Optional[str] = None
    population: Optional[int] = None


def _build_query(language: str, limit: int, offset: int) -> str:
    """
    Build the SPARQL query for a given language, page size and offset.
    
    Optimized for Wikidata performance:
    - Uses direct instance of Q515 (city) with coordinates
    - Direct label lookups instead of expensive SERVICE block
    """
    return f"""
SELECT ?city ?label ?lat ?lon ?countryLabel ?iso2 ?adminLabel ?pop WHERE {{
  ?city wdt:P31 wd:Q515 .
  ?city wdt:P625 ?coord .
  ?city rdfs:label ?label .
  FILTER(LANG(?label) = "{language}" || LANG(?label) = "en")
  
  BIND(geof:latitude(?coord) AS ?lat)
  BIND(geof:longitude(?coord) AS ?lon)
  
  OPTIONAL {{ ?city wdt:P1082 ?pop. }}
  OPTIONAL {{ ?city wdt:P17 ?country. }}
  OPTIONAL {{ ?city wdt:P131 ?admin. }}
  OPTIONAL {{ ?country wdt:P297 ?iso2. }}
  OPTIONAL {{ ?country rdfs:label ?countryLabel. FILTER(LANG(?countryLabel) = "{language}" || LANG(?countryLabel) = "en") }}
  OPTIONAL {{ ?admin rdfs:label ?adminLabel. FILTER(LANG(?adminLabel) = "{language}" || LANG(?adminLabel) = "en") }}
}}
LIMIT {limit}
OFFSET {offset}"""


def _sanitise_raw(raw: str) -> str:
    """
    Strip control characters that JSON parsers reject.
    Keeps \t (0x09) and \n (0x0A); normalises CR/CRLF.
    """
    raw = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", " ", raw)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    return raw


def _parse_response(raw: str, language: str) -> list[SparqlCityInfo]:
    """Parse the raw SPARQL JSON response into a list of SparqlCityInfo objects."""
    data = json.loads(_sanitise_raw(raw))
    bindings = data["results"]["bindings"]
    cities: list[SparqlCityInfo] = []

    for row in bindings:
        try:
            if not all(k in row for k in ("city", "label", "lat", "lon")):
                continue

            wikidata_id = row["city"]["value"].rsplit("/", 1)[-1]
            city_name = row["label"]["value"]

            try:
                lat = float(row["lat"]["value"])
                lon = float(row["lon"]["value"])
            except (ValueError, KeyError):
                continue

            country = row.get("countryLabel", {}).get("value")
            country_code = row.get("iso2", {}).get("value")
            admin_region = row.get("adminLabel", {}).get("value")

            population: Optional[int] = None
            pop_raw = row.get("pop", {}).get("value")
            if pop_raw:
                try:
                    population = int(pop_raw)
                except ValueError:
                    pass

            cities.append(
                SparqlCityInfo(
                    wikidata_id=wikidata_id,
                    city_name=city_name,
                    language=language,
                    latitude=lat,
                    longitude=lon,
                    country=country,
                    country_code=country_code,
                    admin_region=admin_region,
                    population=population,
                )
            )
        except Exception as exc:
            logger.warning("[%s] Skipping row due to parse error: %s", language, exc)

    return cities


def _fetch_page_with_retry(
    client: httpx.Client,
    language: str,
    page_number: int,
    offset: int,
) -> list[SparqlCityInfo] | None:
    """
    Fetch a single SPARQL page with exponential backoff retry logic.
    
    Returns the parsed list of cities on success, or None if all retries fail.
    """
    query = _build_query(language, PAGE_SIZE, offset)
    delay = RETRY_BASE_DELAY_SECONDS

    for attempt in range(1, MAX_PAGE_RETRIES + 2):
        try:
            response = client.post(SPARQL_ENDPOINT, data={"query": query})
            raw = response.text

            logger.info(
                "[%s] Page %d – HTTP %d %s (attempt %d)",
                language, page_number, response.status_code, response.reason_phrase, attempt,
            )

            if response.status_code == 429:
                retry_after = _parse_retry_after(response, fallback=delay)
                logger.warning(
                    "[%s] Page %d – rate limited (429). Waiting %ds before retry...",
                    language, page_number, retry_after,
                )
                time.sleep(retry_after)
                delay = min(delay * 2, 300)
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES:
                snippet = raw[: min(200, len(raw))]
                raise RuntimeError(f"HTTP {response.status_code}: {snippet}")

            if not response.is_success:
                snippet = raw[: min(500, len(raw))]
                logger.error(
                    "[%s] Page %d – non-retryable error HTTP %d. Body: %s",
                    language, page_number, response.status_code, snippet,
                )
                return None

            return _parse_response(raw, language)

        except Exception as exc:
            is_last_attempt = attempt == MAX_PAGE_RETRIES + 1
            if is_last_attempt:
                logger.error(
                    "[%s] Page %d – attempt %d/%d failed: %s. No more retries.",
                    language, page_number, attempt, MAX_PAGE_RETRIES + 1, exc,
                )
                return None

            logger.warning(
                "[%s] Page %d – attempt %d/%d failed: %s. Retrying in %ds...",
                language, page_number, attempt, MAX_PAGE_RETRIES + 1, exc, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 300)

    return None


def _parse_retry_after(response: httpx.Response, fallback: int) -> int:
    """Parse the Retry-After response header."""
    header = response.headers.get("retry-after", "").strip()
    if not header:
        return fallback
    try:
        return max(1, int(header))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        retry_at = parsedate_to_datetime(header)
        wait = int((retry_at - retry_at.utcnow()).total_seconds())
        return max(1, wait)
    except Exception:
        return fallback


def fetch_cities(language: str) -> list[SparqlCityInfo]:
    """
    Fetch all cities for a language from Wikidata SPARQL endpoint.
    
    Returns a deduplicated list of SparqlCityInfo objects.
    Uses pagination with automatic retries and polite delays.
    """
    all_cities: list[SparqlCityInfo] = []
    seen_ids: set[str] = set()
    offset = 0

    logger.info("[%s] Starting paginated fetch (page size: %d)", language, PAGE_SIZE)

    with httpx.Client(headers=_HEADERS, timeout=HTTP_TIMEOUT_SECONDS) as client:
        for page_number in range(1, MAX_PAGES + 1):
            logger.info("[%s] Fetching page %d (offset %d)...", language, page_number, offset)

            page = _fetch_page_with_retry(client, language, page_number, offset)
            if page is None:
                logger.error(
                    "[%s] Page %d failed after all retries. Stopping with %d cities collected.",
                    language, page_number, len(all_cities),
                )
                break

            # Deduplicate
            new_count = 0
            for city in page:
                if city.wikidata_id not in seen_ids:
                    seen_ids.add(city.wikidata_id)
                    all_cities.append(city)
                    new_count += 1

            duplicates = len(page) - new_count
            logger.info(
                "[%s] Page %d: %d rows, %d new, %d duplicates. Total: %d",
                language, page_number, len(page), new_count, duplicates, len(all_cities),
            )

            if len(page) < PAGE_SIZE:
                logger.info("[%s] Last page reached. Done.", language)
                break

            offset += PAGE_SIZE

            if page_number < MAX_PAGES:
                logger.info("[%s] Waiting %ds before next page...", language, PAGE_DELAY_SECONDS)
                time.sleep(PAGE_DELAY_SECONDS)
        else:
            logger.warning("[%s] Hit MAX_PAGES (%d) cap. More cities may exist.", language, MAX_PAGES)

    logger.info("[%s] Fetch complete. Total unique cities: %d", language, len(all_cities))
    return all_cities
