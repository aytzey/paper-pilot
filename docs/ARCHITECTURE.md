# Architecture

## Goal

`paper-pilot` exposes an MCP surface that lets an agent move from topic discovery to document inspection to Zotero persistence without leaving one server.

## Main Components

### `server.py`

Defines the MCP tools and orchestrates the research pipeline.

### `services/academic.py`

Handles discovery and enrichment across:

- Semantic Scholar
- OpenAlex
- arXiv
- Crossref
- Europe PMC
- DOAJ (Directory of Open Access Journals)
- Unpaywall (DOI-based OA enrichment)

### `services/open_access.py`

Resolves open-access PDF candidates, downloads files, and extracts PDF previews.

### `services/deep_read.py`

Performs:

- full-text extraction with PyMuPDF
- chunk generation for downstream retrieval
- page rendering to PNG for chart and figure review

### `services/zotero.py`

Supports two write paths:

1. Web API mode for remote libraries
2. Local mode for desktop Zotero

Local mode uses:

- local API reads through `pyzotero`
- connector-based item save requests
- a bridge plugin for collection membership, notes, and file imports

For sandboxed desktop installs such as Flatpak, attachment files can be staged under the Zotero home directory before import.

### `services/scihub.py`

Opt-in Sci-Hub integration (disabled by default, set `SCIHUB_ENABLED=true` to activate):

- resolves DOIs through configurable Sci-Hub mirrors
- uses CrossRef API for title-to-DOI and keyword lookups
- downloads PDFs with retry logic and mirror fallback
- integrates into the research pipeline as a fallback for non-OA papers

### `services/reporting.py`

Produces Markdown reports for both standard research and deep-read workflows, including an at-a-glance synthesis comparison table (raw relevance scores are kept in the chunk-manifest JSON, not the human-facing report).

### `services/graphing.py`

Builds a nodes/edges model from the search results (node size ∝ citation count, color by year; edges from shared keywords, shared venue, and similarity links) and renders a self-contained interactive vis-network HTML map.

### `services/net.py`

Shared network safety: an SSRF guard (`is_public_http_url`) that blocks loopback/private/link-local/cloud-metadata targets and non-HTTP schemes, plus size-capped streaming download helpers used by every PDF fetch path.

### `cli.py` / `demo.py`

`cli.py` is the entry point: it runs the MCP server (stdio/streamable-http/sse) or, via the `demo` subcommand, runs `demo.py` — a zero-config one-shot pipeline that writes a report + citation graph and opens the graph in a browser.

## Data Flow

1. `search_literature` gathers candidates from multiple scholarly APIs
2. records are normalized into project models
3. OA enrichment resolves better download targets
4. PDFs are downloaded locally
5. deep-read mode extracts full text and chunk evidence
6. the report is written to `data/reports`
7. Zotero sync optionally persists the collection, items, attachment, and note

## Output Artifacts

Generated files are written under `data/`:

- `downloads/` for PDFs
- `reports/` for Markdown reports
- `deep_reads/` for extracted text and chunk manifests
- `renders/` for PNG page renders
- `cache/` for HTTP response cache data

## Network and Reliability Features

- proxy support via `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY`
- custom CA support via `SSL_CERT_FILE`
- cache fallback for temporary upstream failures
- best-effort handling for unstable LibGen mirrors
- Sci-Hub mirror rotation with retry and rate-limit awareness (opt-in)
