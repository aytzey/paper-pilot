"""Render an interactive citation / relatedness graph from search results.

Turns the bibliographic data Paper Pilot already collects (citation counts,
shared keywords, shared venues, similar-paper links) into a single
self-contained vis-network HTML page, the kind of artifact a researcher
screenshots and shares.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

from paper_pilot.config import Settings
from paper_pilot.models import PaperRecord, normalize_title, slugify, utc_timestamp

_PALETTE = {
    "≤2014": "#6b7280",
    "2015–2019": "#0ea5e9",
    "2020–2021": "#22c55e",
    "2022": "#eab308",
    "2023": "#f97316",
    "2024": "#ef4444",
    "2025+": "#a855f7",
    "n/a": "#475569",
}


def _year_bucket(year: int | None) -> str:
    if not year:
        return "n/a"
    if year <= 2014:
        return "≤2014"
    if year <= 2019:
        return "2015–2019"
    if year <= 2021:
        return "2020–2021"
    if year >= 2025:
        return "2025+"
    return str(year)


class GraphService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def graph_dir(self) -> Path:
        return self.settings.data_dir / "graphs"

    def build_graph(
        self,
        papers: list[PaperRecord],
        related: list[PaperRecord] | None = None,
    ) -> dict[str, Any]:
        related = related or []
        seen: dict[str, PaperRecord] = {}
        ordered: list[PaperRecord] = []
        for paper in [*papers, *related]:
            key = paper.dedupe_key()
            if key in seen:
                continue
            seen[key] = paper
            ordered.append(paper)

        primary_keys = {paper.dedupe_key() for paper in papers}
        nodes = []
        for paper in ordered:
            bucket = _year_bucket(paper.year)
            citations = paper.citation_count or 0
            nodes.append(
                {
                    "id": paper.dedupe_key(),
                    "label": self._wrap_label(paper.title),
                    "value": round(math.log1p(citations) * 8) + 6,
                    "group": bucket,
                    "color": _PALETTE.get(bucket, "#475569"),
                    "shape": "dot" if paper.dedupe_key() in primary_keys else "diamond",
                    "title": self._tooltip(paper),
                    "url": paper.url or (paper.pdf_url or ""),
                }
            )

        edges = self._build_edges(papers, related, seen)
        return {
            "nodes": nodes,
            "edges": edges,
            "legend": [{"label": label, "color": color} for label, color in _PALETTE.items()],
        }

    def _build_edges(
        self,
        papers: list[PaperRecord],
        related: list[PaperRecord],
        seen: dict[str, PaperRecord],
    ) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        edge_keys: set[tuple[str, str]] = set()

        def add(a: str, b: str, color: str, dashes: bool, title: str, value: int) -> None:
            if a == b:
                return
            pair = tuple(sorted((a, b)))
            if pair in edge_keys:
                return
            edge_keys.add(pair)
            edges.append({"from": a, "to": b, "color": {"color": color, "opacity": 0.5}, "dashes": dashes, "title": title, "value": value})

        items = list(seen.values())
        # Shared keywords (strongest signal) and shared venue.
        for i, left in enumerate(items):
            for right in items[i + 1 :]:
                shared = set(k.lower() for k in left.keywords) & set(k.lower() for k in right.keywords)
                if shared:
                    add(left.dedupe_key(), right.dedupe_key(), "#38bdf8", False, "shared: " + ", ".join(sorted(shared)[:4]), len(shared) + 1)
                elif left.venue and right.venue and normalize_title(left.venue) == normalize_title(right.venue):
                    add(left.dedupe_key(), right.dedupe_key(), "#94a3b8", True, f"venue: {left.venue}", 1)

        # Similar-paper links radiate from the top result.
        if papers:
            seed = papers[0].dedupe_key()
            for rel in related:
                add(seed, rel.dedupe_key(), "#f472b6", True, "similar work", 1)
        return edges

    def write_graph(
        self,
        topic: str,
        papers: list[PaperRecord],
        related: list[PaperRecord] | None = None,
    ) -> Path:
        graph = self.build_graph(papers, related)
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        destination = self.graph_dir / f"{slugify(topic)}-{utc_timestamp()}.html"
        destination.write_text(self.render_html(graph, topic), encoding="utf-8")
        return destination

    @staticmethod
    def _wrap_label(title: str, width: int = 24, max_lines: int = 3) -> str:
        words = (title or "Untitled").split()
        lines: list[str] = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > width and current:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        label = "\n".join(lines)
        if len(" ".join(words)) > len(label):
            label = label.rstrip() + "…"
        return label

    @staticmethod
    def _tooltip(paper: PaperRecord) -> str:
        authors = ", ".join(paper.authors[:4]) or "unknown"
        bits = [
            paper.title,
            f"{authors} · {paper.year or 'n/a'}",
        ]
        if paper.venue:
            bits.append(paper.venue)
        if paper.citation_count is not None:
            bits.append(f"{paper.citation_count} citations")
        bits.append(paper.source)
        return " · ".join(str(bit) for bit in bits)

    def render_html(self, graph: dict[str, Any], topic: str) -> str:
        payload = json.dumps(graph, ensure_ascii=False)
        safe_topic = html.escape(topic)
        legend_html = "".join(
            f'<span class="chip"><i style="background:{item["color"]}"></i>{html.escape(item["label"])}</span>'
            for item in graph["legend"]
        )
        return _HTML_TEMPLATE.replace("__TOPIC__", safe_topic).replace("__LEGEND__", legend_html).replace(
            "__PAYLOAD__", payload
        ).replace("__COUNT__", str(len(graph["nodes"])))


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Paper Pilot · citation map: __TOPIC__</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #0b1120; color: #e2e8f0; }
  header { padding: 18px 24px; border-bottom: 1px solid #1e293b;
           background: linear-gradient(90deg, #0b1120, #111c33); }
  header h1 { margin: 0; font-size: 18px; font-weight: 700; letter-spacing: .2px; }
  header h1 span { color: #38bdf8; }
  header p { margin: 6px 0 0; font-size: 13px; color: #94a3b8; }
  .legend { display: flex; flex-wrap: wrap; gap: 10px; padding: 12px 24px; font-size: 12px; color: #cbd5e1; }
  .chip { display: inline-flex; align-items: center; gap: 6px; }
  .chip i { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
  #graph { width: 100vw; height: calc(100vh - 132px); }
  footer { position: fixed; bottom: 8px; right: 14px; font-size: 11px; color: #475569; }
  footer a { color: #64748b; text-decoration: none; }
</style>
</head>
<body>
<header>
  <h1><span>Paper&nbsp;Pilot</span> · citation &amp; relatedness map</h1>
  <p>__TOPIC__: __COUNT__ papers · dot = top result, diamond = related · size ∝ citations · edges = shared keywords / venue / similarity</p>
</header>
<div class="legend">__LEGEND__</div>
<div id="graph"></div>
<footer><a href="https://github.com/aytzey/paper-pilot">github.com/aytzey/paper-pilot</a></footer>
<script>
  const data = __PAYLOAD__;
  const nodes = new vis.DataSet(data.nodes);
  const edges = new vis.DataSet(data.edges);
  const container = document.getElementById("graph");
  const network = new vis.Network(container, { nodes, edges }, {
    nodes: { font: { color: "#e2e8f0", size: 13, face: "ui-sans-serif" }, borderWidth: 0,
             scaling: { min: 8, max: 42 } },
    edges: { smooth: { type: "continuous" }, width: 1, selectionWidth: 2 },
    physics: { barnesHut: { gravitationalConstant: -9000, springLength: 150, springConstant: 0.03 },
               stabilization: { iterations: 220 } },
    interaction: { hover: true, tooltipDelay: 120, navigationButtons: false }
  });
  network.on("doubleClick", (params) => {
    if (params.nodes.length) {
      const node = nodes.get(params.nodes[0]);
      if (node && node.url) window.open(node.url, "_blank");
    }
  });
</script>
</body>
</html>
"""
