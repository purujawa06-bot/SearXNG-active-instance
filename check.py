#!/usr/bin/env python3
"""SearXNG active instance checker (instances.json edition).

Downloads https://searx.space/data/instances.json, collects EVERY URL
listed in it (each instance key plus every entry of its
``alternativeUrls`` map), probes each candidate one by one with a real
JSON search query, and keeps only instances that return HTTP 200 with
a valid SearXNG JSON payload (no 403/429, no errors of any kind).

The output file (active.json) is fully regenerated on every run.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import time
import urllib.error
import urllib.request

# Registry holding every known instance URL (main keys + alternativeUrls).
SOURCE_URL = "https://searx.space/data/instances.json"
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


def build_search_url(base_url: str) -> str:
    """Build the JSON search probe URL for a given instance base URL."""
    return f"{base_url.rstrip('/')}/search?q={TEST_QUERY}&format=json"


def fetch_all_urls() -> list[str]:
    """Download instances.json and return every listed URL, deduped.

    Both the top-level instance keys and every ``alternativeUrls``
    entry are collected so that all available URLs are probed.
    """
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REGISTRY_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    instances = payload.get("instances", {})
    candidates: list[str] = []
    seen: set[str] = set()
    for base_url, meta in instances.items():
        for raw_url in [base_url, *alternative_urls(meta)]:
            normalized = normalize_url(raw_url)
            if normalized is not None and normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
    return candidates


def alternative_urls(meta: object) -> list[str]:
    """Return the alternativeUrls keys of one registry entry, if any."""
    if isinstance(meta, dict):
        alternatives = meta.get("alternativeUrls")
        if isinstance(alternatives, dict):
            return [url for url in alternatives if isinstance(url, str)]
    return []


def normalize_url(raw_url: object) -> str | None:
    """Normalize a registry URL, returning None for unusable values."""
    if not isinstance(raw_url, str):
        return None
    candidate = raw_url.strip()
    if not candidate.startswith(("http://", "https://")):
        return None
    return candidate.rstrip("/") + "/"


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
    """Fetch all URLs, probe them one by one, and overwrite active.json."""
    print(f"Fetching instance list from {SOURCE_URL}")
    try:
        candidates = fetch_all_urls()
    except Exception as exc:
        raise SystemExit(f"Failed to fetch instance list: {exc}") from exc
    print(f"Probing {len(candidates)} URLs with ?q={TEST_QUERY}&format=json")

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
        "source": SOURCE_URL,
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
