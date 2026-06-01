"""Zero-config one-command demo.

``paper-pilot demo "<topic>"`` runs the full research + deep-read pipeline
against live open-access APIs, writes a Markdown report and an interactive
citation-graph HTML, prints the paths, and opens the graph in a browser. It
needs no MCP client and no configuration. A polite placeholder email is used
for OA resolution if you have not set your own.
"""

from __future__ import annotations

import asyncio
import os
import sys
import webbrowser
from pathlib import Path

_PLACEHOLDER_EMAIL = "paper-pilot-demo@example.com"


def _ensure_polite_email() -> None:
    for var in ("OPENALEX_EMAIL", "UNPAYWALL_EMAIL"):
        if not os.getenv(var):
            os.environ[var] = _PLACEHOLDER_EMAIL
            print(
                f"[paper-pilot] {var} not set; using a placeholder for the demo. "
                "Set your own email (free, no signup) for polite, reliable API access.",
                file=sys.stderr,
            )


def run_demo(
    topic: str,
    *,
    limit_per_source: int = 4,
    download_top_n: int = 2,
    open_browser: bool = True,
) -> dict:
    _ensure_polite_email()
    # Imported lazily so the placeholder env is in place before settings load.
    from paper_pilot.server import deep_read_topic

    print(f'[paper-pilot] Researching "{topic}" across 6 academic databases…', file=sys.stderr)
    result = asyncio.run(
        deep_read_topic(
            topic=topic,
            limit_per_source=limit_per_source,
            download_top_n=download_top_n,
            write_graph=True,
        )
    )

    papers = result.get("top_papers", [])
    deep_reads = result.get("deep_reads", [])
    print(f"\n  Found {len(papers)} papers · deep-read {len(deep_reads)} full-text PDFs\n", file=sys.stderr)
    for paper in papers[:8]:
        authors = ", ".join(paper.get("authors", [])[:2]) or "unknown"
        print(f"   • {paper.get('title')} ({paper.get('year') or 'n/a'}) · {authors}", file=sys.stderr)

    report_path = result.get("report_path")
    graph_path = result.get("graph_path")
    print("\n  Report:", report_path, file=sys.stderr)
    print("  Citation graph:", graph_path, file=sys.stderr)
    for warning in result.get("warnings", [])[:5]:
        print("  ! ", warning, file=sys.stderr)

    if open_browser and graph_path:
        try:
            # Path.as_uri() emits a valid file URI on every OS (file:///C:/... on Windows,
            # file:///home/... on POSIX); f"file://{path}" is malformed for Windows paths.
            webbrowser.open(Path(graph_path).as_uri())
        except Exception:  # pragma: no cover - headless environments
            pass
    return result
