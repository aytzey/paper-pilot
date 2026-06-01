# Changelog

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
