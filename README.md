# halka-arz-advisor

**Phase 0: source discovery and validation.** This phase only checks whether the
official public data sources for Turkish IPOs (halka arz) are reachable and what
shape their responses have. It does **not** implement scoring, recommendations,
Telegram notifications, machine learning, a database, or a web UI.

## What's here

A small source-probing CLI that sends one polite HTTP GET to each of four official
sources, records a normalized result, and saves everything for inspection:

1. SPK IPO data — `https://spk.gov.tr/ihrac-verileri/ilk-halka-arz-verileri`
2. SPK IPO application list — `https://spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu`
3. SPK web service documentation — `https://ws.spk.gov.tr/help/index.html`
4. KAP disclosure search — `https://kap.org.tr/tr/bildirim-sorgu`

See [`docs/source-matrix.md`](docs/source-matrix.md) for what each source actually
returned on the last test run.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Run the probe

```bash
uv run python scripts/probe_sources.py
```

Useful flags: `--delay`, `--connect-timeout`, `--read-timeout`, `--max-retries`,
`--raw-dir`, `--report-dir`, `-v/--verbose`. Run with `-h` for details.

Output:

- One compact status line per source on the terminal.
- Complete raw responses under `data/raw/<source>/<timestamp>/` (`response.<ext>` + `meta.json`).
- A normalized JSON report under `data/probe-results/probe-report-<timestamp>.json`.
- A human-readable Markdown report under `data/probe-results/probe-report-<timestamp>.md`.

## Design constraints (Phase 0)

- `httpx` for HTTP, `BeautifulSoup` only when HTML parsing is needed.
- No Selenium/Playwright/Pyppeteer/browser automation.
- Descriptive `User-Agent`, configurable delay between requests, explicit connect/read
  timeouts, bounded exponential backoff retried only for 429/5xx and transient network
  errors — never for 4xx client errors.
- Errors are always recorded on the result (`ProbeResult.error`), never swallowed.
- No SPK API endpoints are guessed; only links/routes actually present in fetched pages
  are recorded.

## Tests

```bash
uv run pytest
```

Tests use static HTML fixtures (`tests/fixtures/`) and mocked HTTP responses
(`pytest-httpx`) — they do not depend on the live websites.
