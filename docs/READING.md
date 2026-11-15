# Full-paper research guide

## Read the whole paper in Codex or Claude

Ask your agent:

> Find papers on <topic>. Use deep_read_topic, read each deep_reads[*].full_text batch,
> then call read_pdf_text with its pdf_path and next_cursor until null. Read every page,
> including references and appendices. Render figures and tables. Report unreadable pages
> and cite PDF page numbers; do not substitute abstracts or top_chunks for a full read.

`download_paper`, `deep_read_topic` and `extract_local_pdf_text` deliver the first sequential text batch
automatically. For one specific paper, call `download_paper` with its DOI, arXiv ID, PubMed Central ID
or PDF link, then `read_pdf_text` on the returned `pdf_path`.
Continue with the same path and the returned `start_page` / `start_char`. Each response carries at most
20,000 text characters; even an oversized page continues at the exact character.

Responses are sized on purpose. Codex truncates tool output at about 10,000 tokens and Claude Code at
25,000, and text cut by the client never reaches the model. Search results therefore carry shortened
abstracts and no provider blobs, `deep_read_topic` returns only the opening of each paper, and reports
are written to `report_path` instead of being inlined. Ask for more with the cursors, not with larger limits.

The agent can request the **original PDF whenever the task needs it**, without first finishing text
reading: call `read_pdf_document`, then open the returned local file or fetch its MCP resource link
to receive the PDF bytes. Set `embed_base64=true` when the client supports embedded PDFs (within the
tool's size/page limits). Choose text, the PDF, or page images based on the task; receiving the file
alone does not establish that it was read.

| Reading option | What reaches the model | Use |
|---|---|---|
| Abstract / `top_chunks` | Summary or selected, shortened excerpts | Discovery and navigation |
| `read_pdf_text` | Consecutive page text with a continuation cursor | Full-text reading in any MCP client |
| `render_pdf_pages` | Page images | Figures, tables, formulas, extraction checks |
| Local PDF / embedded PDF | File path or optional PDF bytes | Clients that support direct PDF inspection |

`extraction_status` and `pages_without_text` expose extraction gaps. PDFs need an extractable text layer;
pages without one are reported as unreadable. Text extraction can miss layout or symbols:
inspect relevant page images and disclose gaps.
PDFs are stored locally; returned text/images are sent to the configured AI client.

`end_of_document` means the cursor reached the end. It does **not** certify that the agent read earlier
batches. Reading packs and reports contain excerpts, and must not be presented as completed syntheses.

## Research a practical decision

For example: “I want to build a cheap circuit to measure plant-soil pH. Research the relevant
soil science, electrode materials and electronics, starting with recent work, and recommend
an approach I can build and validate.”

The MCP initialization instructions guide the agent to clarify the decision, split it into
discipline-specific questions, search recent work and foundational references, fully read decisive
papers, seek contradictory evidence, and compare alternatives under comparable conditions.
The final recommendation should include sources/pages, rejected alternatives, unresolved questions
and a first validation experiment. The agent performs the reasoning; the server supplies access
and reading tools. Instruction delivery alone does not guarantee the agent followed every step.

Start each discovery query with:

```python
search_literature(
    topic="soil pH reference electrode stability",
    sort_by="newest",
    open_access_only=False,
    limit_per_source=5,
)
```

Inspect `source_status`. To go deeper in one database, pass its `next_request` arguments back
to `search_literature` unchanged. A failed source has a `retry_request`; failure is not exhaustion.
Download the selected paper with `download_paper(doi=..., pdf_url=...)`, then read its
text/PDF. This preserves the selection instead of rerunning a broad download pipeline.

`newest` requests native date ordering: publication dates where available, first submission for
arXiv, and year precision for DOAJ. Each record includes its date precision and source; partial
dates are not padded with invented days. Crossref date paging stops at 10,000 records; narrow
the query/year range to continue. Results cover returned provider pages, not the entire literature
in global date order. Metadata may be cached for `CACHE_TTL_SEC`; access and coverage gaps stay visible.
The existing default remains relevance ranking and `open_access_only=True`.

The detailed product scope and pH case are in [Research decisions](RESEARCH_DECISIONS.md).

## Configuration

**`download_paper` tries arXiv, the PubMed Central open-data store, known PDF links, Unpaywall, and
PubMed Central by DOI, in that order.** Details are in [AGENTS.md](../AGENTS.md#source-policy).
Unpaywall uses `UNPAYWALL_EMAIL`, then `OPENALEX_EMAIL`, then `nomail@mail.com`
if both are missing or blank. A PDF URL is tried before making an extra lookup.
The integration follows the DOI/location model in the [roadoi guide](https://cran.r-project.org/web/packages/roadoi/vignettes/intro.html)
using the existing Python Unpaywall v2 client; no R installation is needed.

When Unpaywall is needed, downloads try `best_oa_location` and then other `oa_locations` PDF URLs.
Landing pages and embargoed future locations are not treated as downloadable PDFs.
`paper.downloaded_from` identifies the copy actually downloaded, with the licence and version
Unpaywall reports for it. Email configuration is optional.
API failures are reported even when OpenAlex supplies a fallback. Valid cached Unpaywall responses can be reused.
An openable PDF might still be a cover or abstract: `warnings` flags short files, and the agent must
inspect the content. In that case, call `download_paper(doi="...")` without a URL to get another copy.
A 403, a 404 or an HTML page is not retried; only timeouts, 429 and 5xx responses are.

```bash
OPENALEX_EMAIL=you@example.com        # Optional contact email for API access
UNPAYWALL_EMAIL=you@example.com       # Optional; OPENALEX_EMAIL or nomail@mail.com is used otherwise
SEMANTIC_SCHOLAR_API_KEY=             # Optional, higher rate limits

# Local Zotero
ZOTERO_LOCAL=true
ZOTERO_LIBRARY_TYPE=user
ZOTERO_DATA_DIR=                       # optional: relocated/sandboxed Zotero data dir (default ~/Zotero)

# Web Zotero API (alternative)
ZOTERO_LIBRARY_ID=
ZOTERO_API_KEY=

# Storage
PAPER_PILOT_DATA_DIR=./data
MAX_DOWNLOAD_MB=75                     # per-PDF download size cap
PAPER_PILOT_ALLOW_EXTERNAL_PDF=true   # read PDFs outside the data dir (set false on networked transports)
PDF_EMBED_MAX_MB=5                     # size cap for an embedded PDF resource
PDF_EMBED_MAX_PAGES=60                 # page cap for an embedded PDF resource

# Institutional networks
HTTP_PROXY=
HTTPS_PROXY=
SSL_CERT_FILE=
```
