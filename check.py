#!/usr/bin/env python3
"""SearXNG active instance checker.

Fetches the public instance list from searx.space, probes each candidate
with a real JSON search query, and keeps only instances that return
HTTP 200 with a valid SearXNG JSON payload (no 403/429, no errors).
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import time
import urllib.error
import urllib.request

# Public registry of SearXNG instances (base domain source).
SOURCE_URL = "https://searx.space/data/instances.json"
# Reference query shape: {base}/search?q=example&format=json
TEST_QUERY = "example"
REQUEST_TIMEOUT = 15
MAX_WORKERS = 20
USER_AGENT = (
    "Mozilla/5.0 (compatible; SearXNG-active-instance-checker/1.0; "
    "+https://github.com/purujawa06-bot/SearXNG-active-instance)"
)
OUTPUT_FILE = "active.json"


def build_search_url(base_url: str) -> str:
    """Build the JSON search probe URL for a given instance base URL."""
    return f"{base_url.rstrip('/')}/search?q={TEST_QUERY}&format=json"


def fetch_candidates() -> list[str]:
    """Download the instance registry and return HTTPS candidate base URLs."""
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    instances = payload.get("instances", {})
    candidates: list[str] = []
    for base_url, meta in instances.items():
        if not isinstance(base_url, str) or not base_url.startswith("https://"):
            continue
        if ".onion" in base_url:
            continue
        # Skip registry entries already known to be offline/broken.
        if isinstance(meta, dict) and meta.get("error"):
            http_info = meta.get("http") or {}
            if http_info.get("status_code") not in (200, None):
                pass  # Still re-probed live below; registry status is only a hint.
        candidates.append(base_url.rstrip("/") + "/")
    return sorted(set(candidates))


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
        # Explicitly reject blocks/rate-limits (403/429) and any other HTTP error.
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

    # A healthy SearXNG JSON response is a dict containing a "results" list.
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
    """Run the full check and write active.json."""
    print(f"Fetching candidates from {SOURCE_URL}")
    try:
        candidates = fetch_candidates()
    except Exception as exc:
        raise SystemExit(f"Failed to fetch instance list: {exc}") from exc
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
        "source": SOURCE_URL,
        "check_endpoint": f"/search?q={TEST_QUERY}&format=json",
        "criteria": "HTTP 200, valid SearXNG JSON with results key, no 403/429 or other errors",
        "total": len(active),
        "instances": active,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Done: {len(active)}/{len(candidates)} active -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
