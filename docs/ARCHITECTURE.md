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
- Unpaywall (DOI resolution for missing/failed PDFs or when requested by the agent; status and provenance retained)

`search_literature` supports relevance (existing default) and native newest-first queries.
`SearchPage` retains each provider's continuation and coverage; `SearchBundle.source_status`
supplies complete next/retry requests for the existing search tool. Semantic Scholar bulk responses
are exposed in smaller groups through the existing API cache without dropping their remainder.
Crossref newest-first queries use bounded offset paging because its cursor rejects publication-date
sorting. Provider limits and date meanings remain visible. The model owns cross-call deduplication.
`PaperRecord` preserves date precision/provenance and conflicting dates from merged records.

The server initialization instructions carry the reading protocol and a condensed research-to-decision
workflow to clients outside this checkout; the full eight-step workflow is the `research_decision` MCP prompt. The client model plans queries, evaluates evidence and makes recommendations.

### `services/open_access.py`

Gets the full text of one paper. `_acquire` walks the routes in order of reliability and stops at the
first readable PDF: arXiv by ID, the PubMed Central open-data store by PMC ID, every PDF link the
search sources reported, Unpaywall's best and alternate locations, then PubMed Central looked up by DOI.
PubMed Central records that hold text only are laid out as a PDF so the page-based reading tools work
on them unchanged. Permanent failures (403, 404, an HTML page, a corrupt file) move on to the next
route at once; only timeouts, 429 and 5xx responses are retried. The downloaded copy's location and
licence are recorded, and files are named by content hash so parallel downloads cannot collide.

### Response budgets

Clients cut what does not fit: Codex truncates tool output at about 10,000 tokens, Claude Code at
25,000, and Claude Code keeps about 2,000 characters of server instructions. `server.py` therefore
sends compact paper records (`PaperRecord.brief`), shares one abstract budget across a result list,
gives `deep_read_topic` one text budget across its papers, and keeps reports on disk. Tests in
`tests/test_server_pdf.py` hold the default responses and the instructions under those limits.

### `services/deep_read.py`

Performs:

- full-text extraction with PyMuPDF
- chunk generation for downstream retrieval
- page rendering to PNG for chart and figure review
- bounded sequential reading over MCP (`read_pdf_text`) with exact page/character continuations
- explicit text-extraction gaps for PDFs without a readable text layer

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

### `services/content.py`

Builds MCP content blocks from local files so a PDF-capable model receives the real artifact: page renders as `ImageContent`, the PDF itself as an embedded `application/pdf` resource (bounded by size and page count), and resource links. Includes `safe_pdf_path` for path-traversal protection.

### `services/net.py`

Shared network safety: an SSRF guard (`is_public_http_url`) that blocks loopback/private/link-local/cloud-metadata targets and non-HTTP schemes, plus size-capped streaming download helpers used by every PDF fetch path.

### `cli.py` / `demo.py`

`cli.py` is the entry point: it runs the MCP server (stdio/streamable-http/sse) or, via the `demo` subcommand, runs `demo.py`, a zero-config one-shot pipeline that writes a report and citation graph and opens the graph in a browser.

## Data Flow

1. `search_literature` gathers candidates from multiple scholarly APIs
2. records are normalized into project models
3. OA enrichment resolves better download targets
4. PDFs are downloaded locally
5. deep-read mode extracts full text, labels extraction gaps, and delivers the first sequential text batch
6. the agent follows `read_pdf_text` continuation cursors until the end and inspects figures as images;
   reports and ranked chunks alone are not a full-paper read
7. the report is written to `data/reports`
8. Zotero sync optionally persists the collection, items, attachment, and note

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
- valid response caches; expired search/Unpaywall responses do not hide upstream failures
- best-effort handling for unstable LibGen mirrors
- Sci-Hub mirror rotation with retry and rate-limit awareness (opt-in)
