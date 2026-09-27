#!/usr/bin/env python3
"""SearXNG active instance checker (HTML scraping edition).

Scrapes the public instance tables on https://searx.space/ for every
listed base URL, probes each candidate with a real JSON search query,
and keeps only instances that return HTTP 200 with a valid SearXNG
JSON payload (no 403/429, no errors of any kind).

The output file (active.json) is fully regenerated on every run.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import re
import time
import urllib.error
import urllib.request

# Page whose instance tables are scraped for base URLs.
SOURCE_PAGE = "https://searx.space/"
# Reference probe shape: {base}/search?q=example&format=json
TEST_QUERY = "example"
REGISTRY_TIMEOUT = 30
REQUEST_TIMEOUT = 15
MAX_WORKERS = 25
USER_AGENT = (
    "Mozilla/5.0 (compatible; SearXNG-active-instance-checker/1.0; "
    "+https://github.com/purujawa06-bot/SearXNG-active-instance)"
)
OUTPUT_FILE = "active.json"

# Cells holding the primary instance URL and any alternative mirrors.
COLUMN_URL_PATTERN = re.compile(r'<td class="column-url".*?</td>', re.DOTALL)
ALT_URL_PATTERN = re.compile(r'<td class="column-alternativeurls.*?</td>', re.DOTALL)
HREF_PATTERN = re.compile(r'href="(https://[^"]+)"')

# Hosts that may appear in the scraped cells but are never instances.
BLOCKED_HOSTS = ("github.com", "searx.space")


def fetch_registry_page() -> str:
    """Download the searx.space HTML page used as the scraping source."""
    request = urllib.request.Request(SOURCE_PAGE, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REGISTRY_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_base_url(raw_url: str) -> str | None:
    """Normalize a scraped URL, returning None for non-instance links."""
    candidate = raw_url.strip()
    if ".onion" in candidate:
        return None
    host = candidate.removeprefix("https://").split("/", 1)[0].lower()
    if host in BLOCKED_HOSTS:
        return None
    return candidate.rstrip("/") + "/"


def scrape_candidates(html: str) -> list[str]:
    """Extract every instance base URL from the scraped tables.

    The first link of each ``column-url`` cell is the primary instance
    URL; ``column-alternativeurls`` cells may hold extra clearnet
    mirrors. Order is preserved and duplicates are removed.
    """
    raw_urls: list[str] = []
    for cell in COLUMN_URL_PATTERN.findall(html):
        hrefs = HREF_PATTERN.findall(cell)
        if hrefs:
            raw_urls.append(hrefs[0])
    for cell in ALT_URL_PATTERN.findall(html):
        raw_urls.extend(HREF_PATTERN.findall(cell))

    candidates: list[str] = []
    seen: set[str] = set()
    for raw_url in raw_urls:
        normalized = normalize_base_url(raw_url)
        if normalized is not None and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    return candidates


def build_search_url(base_url: str) -> str:
    """Build the JSON search probe URL for a given instance base URL."""
    return f"{base_url.rstrip('/')}/search?q={TEST_QUERY}&format=json"


def check_instance(base_url: str) -> dict | None:
    """Probe one instance; return its record on success, else None."""
    search_url = build_search_url(base_url)
    started = time.monotonic()
    try:
        request = urllib.request.Request(
            search_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.getcode()
            body = response.read()
    except urllib.error.HTTPError as exc:
        # Explicitly reject blocks/rate-limits (403/429) and any HTTP error.
        print(f"SKIP {base_url} -> HTTP {exc.code}")
        return None
    except Exception as exc:  # Timeout, DNS, TLS, connection reset, ...
        print(f"SKIP {base_url} -> {type(exc).__name__}: {exc}")
        return None

    elapsed_ms = int((time.monotonic() - started) * 1000)

    if status != 200:
        print(f"SKIP {base_url} -> HTTP {status}")
        return None

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeError):
        print(f"SKIP {base_url} -> invalid JSON")
        return None

    # A healthy SearXNG JSON response is a dict containing a "results" list,
    # which proves the instance supports ?format=json.
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        print(f"SKIP {base_url} -> 200 but unexpected JSON shape")
        return None

    print(f"OK   {base_url} -> 200 ({elapsed_ms}ms, {len(data['results'])} results)")
    return {
        "url": base_url,
        "search_url": search_url,
        "status": 200,
        "response_time_ms": elapsed_ms,
        "results_count": len(data["results"]),
    }


def main() -> None:
    """Scrape candidates, probe them all, and overwrite active.json."""
    print(f"Scraping instance URLs from {SOURCE_PAGE}")
    try:
        candidates = scrape_candidates(fetch_registry_page())
    except Exception as exc:
        raise SystemExit(f"Failed to scrape instance list: {exc}") from exc
    print(f"Probing {len(candidates)} candidates with ?q={TEST_QUERY}&format=json")

    active: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_instance, url): url for url in candidates}
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            if record is not None:
                active.append(record)

    # Fastest instances first for convenient consumption.
    active.sort(key=lambda item: item["response_time_ms"])
    updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    output = {
        "updated_at": updated_at,
        "source": SOURCE_PAGE,
        "method": "scraping",
        "check_endpoint": f"/search?q={TEST_QUERY}&format=json",
        "criteria": "HTTP 200, valid SearXNG JSON with results key, no 403/429 or other errors",
        "total_checked": len(candidates),
        "total": len(active),
        "instances": active,
    }
    # Always overwrite: the file is fully regenerated on every run.
    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Done: {len(active)}/{len(candidates)} active -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
