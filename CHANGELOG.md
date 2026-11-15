# Changelog

## 0.8.0 - 2026-11-15

The goal of this release: an agent that asks for a paper gets the whole paper, not its abstract.

### Fixed
- **New installs crashed on start.** `uvx --from git+…` resolved `mcp` 2.x, which removed
  `mcp.server.fastmcp`. The dependency is pinned to `mcp>=1.12.4,<2` again.
- **Responses were cut by the client.** A default `deep_read_topic` call returned about 289,000
  characters (roughly 72,000 tokens), far above the tool-output limit of Codex (about 10,000 tokens)
  and Claude Code (25,000). The same first pages were sent up to four times (`full_text`, two
  `extracted_preview` copies, `report_markdown`). It now returns about 29,000 characters. Default
  `search_literature` went from about 68,000 to 26,000 characters.
- **Server instructions were truncated.** Claude Code keeps about 2,000 characters; the old
  instructions were three times that and lost their last steps. They are now 1,667 characters with
  the reading protocol first. The full workflow is available as the `research_decision` prompt.
- `paper-pilot demo` crashed with `'list' object has no attribute 'get'`.
- Two downloads started in the same second with the same title overwrote each other. Files are now
  named by content hash, which also stops repeated downloads from piling up copies.

### Changed
- **`inspect_open_access_pdf` is now `download_paper`** and takes `doi`, `pdf_url`, `arxiv_id`,
  `pmcid` and `title`. A `doi.org` link, an arXiv abstract page or a PMC article page also works as
  `pdf_url`. It returns the first full-text batch with a cursor instead of a five-page preview.
- **More routes to the full text**, tried in this order: arXiv by ID, the PubMed Central open-data
  store, every PDF link the search sources reported, Unpaywall, then PubMed Central looked up by DOI.
  PMC links on `pmc.ncbi.nlm.nih.gov` and `europepmc.org` answer scripts with a bot challenge and were
  the largest source of failed downloads. Paywalled papers with an author manuscript in PMC can now
  be read. On the first 12 results of five topic searches (60 papers), full-text access went from
  32 to 40; the rest are paywalled or dead links.
- Permanent failures (403, 404, an HTML page, a corrupt file) are no longer retried. A failed
  download returns in about a second instead of 7 to 37.
- Merged search records keep every PDF link as a download candidate. Semantic Scholar's arXiv and
  PMC ids and all OpenAlex locations are kept too.
- `read_pdf_text` returns up to 20,000 characters by default (was 12,000), so a typical paper takes
  two to four calls.
- Tool payloads carry compact paper records: no `raw` provider blobs, authors capped at five,
  abstracts sharing one budget. `research_topic` and `deep_read_topic` no longer inline
  `report_markdown` or `extracted_preview`; the report stays at `report_path`.
- `download_paper` warns when a file is short or has no text layer, and lists supplement PDFs that
  PubMed Central holds for the article.

## 0.7.0 - 2026-06-01

### Changed
- **Better research quality.** Results are re-ranked by topic relevance first (query-term coverage
  in the title and abstract, plus an exact-phrase bonus), then by citations, recency, and open-access.
  Precise on-topic papers now outrank merely famous-but-tangential ones, instead of citation count
  dominating. New `PaperRecord.relevance_score()` / `quality_score()`.

### Added
- `ZOTERO_DATA_DIR` env var: point Paper Pilot at a relocated or sandboxed (e.g. Flatpak) Zotero
  data directory on any OS. Local-Zotero PDF staging targets it (default `~/Zotero`, resolved
  cross-platform via pathlib), and `healthcheck` reports it.

## 0.6.3 - 2026-06-01

### Fixed
- `paper-pilot demo` now opens the citation graph via `Path.as_uri()`, so it works on Windows
  (the old `file://` string interpolation produced a malformed URI for Windows paths).

### Docs
- Cross-platform, cross-client setup. `docs/CLIENTS.md` rewritten with per-OS config-file locations
  (Claude Desktop, Cursor, Claude Code, Codex), the Windows `spawn uv ENOENT` fix, prerequisites
  (uv install, PyMuPDF wheels), and a per-client PDF-capability matrix. Added `examples/cursor.mcp.json`
  and a Cursor + Windows section to the README.

## 0.6.2 - 2026-06-01

### Added
- New **`get_pdf_page_text(pdf_path, page_numbers)`** tool: returns the exact extracted text of
  specific pages as plain JSON, so clients without filesystem/shell access (e.g. Claude API
  integrations) can pull a single fine-grained detail (a reference entry, a table) over the wire.
  `deep_read_topic` agent_notes now point to it for known-page lookups.

### Fixed
- The chunk manifest (`{stem}.chunks.json`) now stores full, untruncated chunk text. It is a
  sidecar file, not an MCP payload, so the 1200-char preview cap no longer applies there.

## 0.6.1 - 2026-06-01

### Changed
- PDF content now defaults to **file paths, not base64**. `deep_read_topic` returns every
  downloaded PDF's local path in `pdf_paths` (no inlined images or PDF by default); set
  `render_top_pages=True` / `attach_top_pdf=True` to opt into base64 for clients that read it.
- `read_pdf_document` defaults to returning the local path plus a `paperpilot://pdf/{doc_id}`
  resource link; pass `embed_base64=True` to inline the PDF. Keeps payloads small for clients
  (e.g. Codex) that open files from disk or fetch the resource on demand.

## 0.6.0 - 2026-06-01

### Added
- **Native PDF usage for PDF-capable models.** `render_pdf_pages` now returns the pages as
  real images (the model sees figures, tables, and layout), not just PNG paths.
- New **`read_pdf_document`** tool returns a downloaded PDF as an embedded `application/pdf`
  resource (or a resource link), bounded by `PDF_EMBED_MAX_MB` / `PDF_EMBED_MAX_PAGES`.
- `deep_read_topic` now, by default, renders the top paper's most relevant pages as images and
  embeds that PDF (controllable via `render_top_pages` / `attach_top_pdf`; degrades to warnings).
- Downloaded PDFs are exposed as MCP resources (`paperpilot://pdf/{doc_id}`); download tools
  return a `doc_id`.
- New `services/content.py` (page images, embedded PDF, resource links, `safe_pdf_path`).

### Config
- new: `PAPER_PILOT_ALLOW_EXTERNAL_PDF` (default true), `PDF_EMBED_MAX_MB`, `PDF_EMBED_MAX_PAGES`.

### Fixed
- `ReportService.write_report` now creates the reports directory if missing.

## 0.5.0 - 2026-06-01

### Added
- **6th academic source: DOAJ** (Directory of Open Access Journals)
- **`paper-pilot demo "<topic>"`**: zero-config one-command CLI that runs the full
  pipeline and opens an interactive citation graph in the browser (no MCP client needed)
- **`graph_topic` tool** and a `write_graph` flag on `research_topic` / `deep_read_topic`
  that render a self-contained interactive citation/relatedness graph (HTML)
- synthesis layer in deep-read reports: an at-a-glance **Comparison table**
  (debug relevance scores moved to the chunk-manifest JSON)
- committed real sample output under `examples/`
- `ruff` linting wired into CI; launch/distribution playbook under `docs/launch/`

### Fixed
- **HTTP/SSE transports** now work (`--transport streamable-http|sse` no longer crashes;
  network options are set on `mcp.settings` instead of passed to `mcp.run()`)
- Semantic Scholar single-sided year filters are now open-ended (no more silent 10-year window)
- arXiv records dedupe across `abs`/`pdf`/versioned URLs
- chunk relevance scoring is now Unicode-aware (non-ASCII query terms are honored)

### Security
- TLS verification is **on by default** for Sci-Hub and LibGen (opt out with `INSECURE_SHADOW_TLS=true`)
- `search_scihub` / `download_scihub_paper` now require `SCIHUB_ENABLED=true`
- SSRF guard + size-capped streaming downloads across all PDF fetch paths
- Zotero sync no longer aborts on a single item failure (reports `failed_items`)

### Config
- new: `MAX_DOWNLOAD_MB`, `INSECURE_SHADOW_TLS`; malformed numeric env vars now fall back to defaults

## 0.4.0 - 2026-04-10

- rebranded to **Paper Pilot**; package renamed to `paper-pilot`
- viral-ready README rewrite and English-only codebase
- actionable remediation hints in `healthcheck`
- hardened DOI open-access enrichment fallback

## 0.3.0 - 2026-04-10

- added **Sci-Hub integration** as opt-in paper download source (disabled by default)
- new tools: `search_scihub` (DOI/title/keyword search) and `download_scihub_paper` (PDF download by DOI)
- `research_topic` and `deep_read_topic` now accept `include_scihub=True` for automatic fallback
- new config: `SCIHUB_ENABLED`, `SCIHUB_MIRRORS`, `SCIHUB_TIMEOUT_SEC`
- added copyright disclaimer and responsible use notice
- no new dependencies: uses existing `httpx` and `beautifulsoup4`

## 0.2.0 - 2026-04-09

- rebranded to **Deep Research MCP**
- renamed Python package from `zotero-researcher-mcp` to `deep-research-mcp`
- rewrote README for clarity and discoverability
- updated all documentation, configs, and agent guides
- bumped version across the board

## 0.1.2 - 2026-04-09

- added demo GIF and publishing guide
- added GitHub release workflow with built artifacts
- added manual PyPI publish workflow for trusted publishing
- extended CI to validate built distributions
- improved README discoverability with release badge and demo section

## 0.1.1 - 2026-04-09

- added portfolio-grade README badges and positioning
- added architecture and community documentation
- added citation, contributing, security, and conduct files
- added GitHub issue and pull request templates
- added Dependabot and CI workflow
- published repository metadata, topics, and release assets

## 0.1.0 - 2026-04-09

- initial release
- multi-source academic search across Semantic Scholar, OpenAlex, arXiv, Crossref, and Europe PMC
- OA PDF resolution and inspection
- deep-read workflow with text extraction, chunking, and page rendering
- local and web Zotero integration
