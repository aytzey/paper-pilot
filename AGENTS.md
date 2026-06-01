# AGENTS.md

This file is the shared entry point for any coding or research agent working with `paper-pilot`.

## What This Repository Does

This project exposes an MCP server for:

- academic search across 6 databases (Semantic Scholar, OpenAlex, arXiv, Crossref, Europe PMC, DOAJ)
- open-access PDF resolution and inspection
- Sci-Hub paper resolution and download (opt-in, disabled by default)
- deep reading with full-text extraction and chunking
- PDF page rendering for charts, tables, and figures
- interactive citation/relatedness graph export (HTML)
- Zotero sync in both local and web modes

There is also a zero-config CLI: `paper-pilot demo "<topic>"` runs the whole pipeline and opens a citation graph without any MCP client.

## Read Order

If you need context fast, read files in this order:

1. [README.md](README.md)
2. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
3. [src/paper_pilot/server.py](src/paper_pilot/server.py)
4. [src/paper_pilot/services/zotero.py](src/paper_pilot/services/zotero.py)
5. [CLAUDE.md](CLAUDE.md) or [CODEX.md](CODEX.md), depending on the client

## Recommended Tool Order

For most research tasks, use tools in this order:

1. `healthcheck`
2. `research_topic` for broad discovery and report generation
3. `deep_read_topic` when you need evidence chunks and local PDF access
4. `render_pdf_pages` when visual inspection matters (returns the pages as images you can see)
5. `read_pdf_document` to get a downloaded PDF's local path + resource link (pass embed_base64=true only if your client reads inlined PDFs)
6. `graph_topic` (or `write_graph=True`) when a citation/relatedness map helps
7. `list_zotero_collections` before writing into an existing collection

Use `search_literature` and `find_similar_papers` when you want fine-grained control instead of the bundled pipeline.

## Important Output Locations

The server writes artifacts under `data/`:

- `data/downloads/`: downloaded PDFs
- `data/reports/`: Markdown reports
- `data/deep_reads/`: extracted text and chunk manifests
- `data/renders/`: rendered PNG pages
- `data/cache/`: cached API responses

If a tool returns an absolute `pdf_path`, prefer using that file directly instead of guessing the location.

## Zotero Rules

Always run `healthcheck` before relying on Zotero writes.

Local Zotero mode is considered healthy only when:

- `zotero.mode` is `local`
- `local_api_reachable` is `true`
- `bridge_reachable` is `true` for full writes

If the bridge is unavailable, metadata-only flows may still work, but collection membership, note creation, and file attachment imports are limited.

## Source Policy

The project is OA-first by design.

Search runs across 6 databases: Semantic Scholar, OpenAlex, arXiv, Crossref, Europe PMC, and DOAJ.

Preferred sources for the full text itself:

1. Semantic Scholar / OpenAlex / DOAJ open PDFs
2. arXiv and Europe PMC OA locations
3. Unpaywall resolution
4. publisher open links
5. Sci-Hub (opt-in, disabled by default)

`Sci-Hub` is available as an opt-in fallback when `SCIHUB_ENABLED=true`. If used, its provenance should remain explicit.

`LibGen` exists as a best-effort supplemental layer. If used, its provenance should remain explicit and secondary in summaries.

## When You Need Code Context

The implementation is organized around service modules:

- `services/academic.py`: discovery and enrichment
- `services/open_access.py`: OA downloads and PDF previews
- `services/deep_read.py`: text extraction and page rendering
- `services/zotero.py`: local and web Zotero integration
- `services/scihub.py`: Sci-Hub paper resolution and download (opt-in)
- `services/reporting.py`: report generation and synthesis comparison tables
- `services/graphing.py`: interactive citation-graph HTML export
- `services/content.py`: PDF/image MCP content blocks (page images, embedded PDF, resource links)
- `services/net.py`: SSRF guard and size-capped downloads

Tool entry points are defined in [src/paper_pilot/server.py](src/paper_pilot/server.py).

## Good Default Prompt Shapes

- `Research the topic <topic>, deep-read the strongest papers, and give me the evidence-backed summary.`
- `Find the best OA papers for <topic>, render the pages with the key figures, and prepare a Zotero collection.`
- `Check local Zotero health, then sync the report into a new collection named <name>.`
