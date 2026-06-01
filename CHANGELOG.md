# Changelog

## 0.4.0 - 2026-06-01

### Added
- rebranded to **Paper Pilot**; package renamed to `paper-pilot`
- **6th academic source: DOAJ** (Directory of Open Access Journals)
- **`paper-pilot demo "<topic>"`** — zero-config one-command CLI that runs the full
  pipeline and opens an interactive citation graph in the browser (no MCP client needed)
- **`graph_topic` tool** and a `write_graph` flag on `research_topic` / `deep_read_topic`
  that render a self-contained interactive citation/relatedness graph (HTML)
- synthesis layer in deep-read reports: an at-a-glance **Comparison table**
  (debug relevance scores moved to the chunk-manifest JSON)
- committed real sample output under `examples/`
- `ruff` linting wired into CI; launch/distribution playbook under `docs/launch/`

### Fixed
- **HTTP/SSE transports** now work (`--transport streamable-http|sse` no longer crashes —
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
