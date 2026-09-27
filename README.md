# SearXNG Active Instance

Daily list of active [SearXNG](https://github.com/searxng/searxng) instances, refreshed by GitHub Actions every day at **00:00 UTC**.

- Source of candidates: **scraping** the instance tables on [searx.space](https://searx.space/) — every listed base URL (primary + alternative mirrors) is collected and probed one by one (concurrently)
- Probe: `{base}/search?q=example&format=json`
- Kept only if the probe returns **HTTP 200** with a valid SearXNG JSON payload containing a `results` list — this proves the instance supports `?format=json`; 403 / 429 / timeouts / any error means skipped

## Output

`active.json` is **fully overwritten on every run** (never merged or appended):

```json
{
  "updated_at": "2026-09-27T00:00:00Z",
  "source": "https://searx.space/",
  "method": "scraping",
  "check_endpoint": "/search?q=example&format=json",
  "total_checked": 70,
  "total": 12,
  "instances": [
    {
      "url": "https://example.org/",
      "search_url": "https://example.org/search?q=example&format=json",
      "status": 200,
      "response_time_ms": 342,
      "results_count": 10
    }
  ]
}
```

Instances are sorted fastest-first by `response_time_ms`.

## Manual run

Actions tab → **Check active SearXNG instances** → **Run workflow**,
or locally with Python 3:

```sh
python check.py
```
