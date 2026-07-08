# Optional integrations: Sci-Hub and LibGen

Paper Pilot's core pipeline uses only legal, open-access sources (Semantic Scholar, OpenAlex, arXiv, Crossref, Europe PMC, DOAJ, Unpaywall). Separately, it ships strictly optional integrations for Sci-Hub and Library Genesis (LibGen). These are **disabled by default** and nothing in the default configuration or the demo uses them.

> **Legal note:** Sci-Hub and LibGen distribute copyrighted material without publisher authorization, and accessing them may be unlawful in your jurisdiction or violate your institution's policies. These integrations exist for users who have determined they may lawfully use these sources. You are solely responsible for compliance with applicable laws, licenses, and institutional rules. The maintainers do not encourage or endorse copyright infringement.

## Enabling

Set the environment variable in your MCP client config or shell:

```bash
SCIHUB_ENABLED=true
```

While `SCIHUB_ENABLED` is `false` (the default), the related tools refuse to run and the pipeline never falls back to these sources.

## Tools

| Tool | What it does |
|---|---|
| `search_scihub` | Search Sci-Hub by DOI, title, or keyword |
| `download_scihub_paper` | Download a paper via Sci-Hub by DOI |
| `search_libgen` | Supplementary LibGen search |
| `inspect_libgen_item` | Resolve a LibGen mirror item and preview its PDF |

Once enabled, you can also pass `include_scihub=True` to `research_topic` / `deep_read_topic` to use Sci-Hub as a fallback when no open-access PDF is found.

## Related configuration

```bash
SCIHUB_ENABLED=false          # master switch, off by default
INSECURE_SHADOW_TLS=false     # opt in to skip TLS verification for Sci-Hub/LibGen mirrors
```

Implementation lives in `src/paper_pilot/services/scihub.py` and `src/paper_pilot/services/libgen.py`.
