# Sample output: `deep_read_topic("retrieval augmented generation")`

> This is a **real** report produced by Paper Pilot's `deep_read_topic` tool,
> curated for the README: local file paths shortened, one run's transient
> rate-limit warnings removed, and the most representative evidence pages
> surfaced. Every paper, author, citation count, and quoted excerpt below is
> verbatim from the live pipeline run. Reproduce it yourself with:
>
> ```bash
> paper-pilot demo "retrieval augmented generation"
> ```
>
> The interactive citation graph from the same run is in
> [`sample-citation-graph.html`](sample-citation-graph.html).

---

# Deep Read Report: retrieval augmented generation

- **Research question:** retrieval augmented generation
- **Deep-read papers:** 2
- **Total candidate papers:** 12 (merged & deduplicated across 6 databases)

## Comparison

| # | Paper | Year | Venue | Citations | Pages | Top evidence |
|---|-------|------|-------|-----------|-------|--------------|
| 1 | Retrieval-Augmented Generation for Large Language Models: A Survey | 2023 | arXiv (Cornell University) | 643 | 21 | p.1, p.2, p.6 |
| 2 | Benchmarking Large Language Models in Retrieval-Augmented Generation | 2024 | Proceedings of the AAAI Conference | 311 | 9 | p.1, p.2, p.3 |

### Synthesis (filled in by the agent from the evidence below)

| Paper | Method | Key finding | Limitation |
|-------|--------|-------------|------------|
| **RAG Survey** (Gao et al., 2023) | A taxonomy review organizing RAG systems into three paradigms (**Naive RAG**, **Advanced RAG**, **Modular RAG**) and dissecting the tripartite foundation of retrieval, generation, and augmentation. | RAG grounds LLMs in external knowledge to reduce hallucination; the field is moving from naive retrieve-then-read pipelines toward modular, iterative architectures. | A survey, not an empirical study; it organizes and contextualizes existing methods rather than producing new benchmark results. |
| **RGB Benchmark** (Chen et al., 2024) | Introduces the **Retrieval-Augmented Generation Benchmark (RGB)**, an English + Chinese corpus that isolates four fundamental RAG abilities: **noise robustness, negative rejection, information integration, counterfactual robustness**. | Even strong LLMs struggle with negative rejection and counterfactual robustness; RAG helps but exposes clear capability bottlenecks per ability. | Scope limited to four constructed testbeds; results reflect the chosen retrieval corpus and the LLMs available at evaluation time. |

> **TL;DR.** The Gao et al. survey is the canonical map of the RAG landscape
> (Naive → Advanced → Modular). The Chen et al. RGB paper is the
> diagnostic: it shows *where* RAG breaks for current LLMs. Read the survey for
> vocabulary and architecture, RGB for evaluation design.

## Evidence

### Retrieval-Augmented Generation for Large Language Models: A Survey

**Pages 1–1** · matched: retrieval, augmented, generation

```text
This paper ... reviews the development paradigms of RAG in the era of LLMs,
summarizing three paradigms: Naive RAG, Advanced RAG, and the Modular RAG.
It meticulously scrutinizes the tripartite foundation of RAG frameworks, which
includes the retrieval, the generation and the augmentation techniques. ...
Furthermore, this paper introduces up-to-date evaluation framework and
benchmark. At the end, this article delineates the challenges currently faced
and points out prospective avenues for research and development.
```

### Benchmarking Large Language Models in Retrieval-Augmented Generation

**Pages 1–1** · matched: retrieval, augmented, generation, benchmark

```text
Retrieval-Augmented Generation (RAG) is a promising approach for mitigating the
hallucination of large language models (LLMs). However, existing research lacks
rigorous evaluation of the impact of retrieval-augmented generation on different
large language models ... We analyze the performance of different large language
models in 4 fundamental abilities required for RAG, including noise robustness,
negative rejection, information integration, and counterfactual robustness. To
this end, we establish Retrieval-Augmented Generation Benchmark (RGB), a new
corpus for RAG evaluation in both English and Chinese.
```

## Similar Work (auto-expanded from the top result)

- **Active Retrieval Augmented Generation** (2023): iterative retrieval during generation
- **Benchmarking Retrieval-Augmented Generation for Medicine** (2024): domain-specific RAG evaluation
- **Self-RAG / corrective-RAG family**: surfaced via relatedness expansion

## How to use this report

- For figures, tables, and layout review, open each PDF directly via its path.
- For text-based comparison, use `text_path` and `chunk_manifest_path`
  (relevance scores live in the manifest JSON).
- Cite the page range shown above each excerpt when extracting evidence.
- Everything here can be one-click synced into a Zotero collection with PDFs
  attached (`write_to_zotero=True`).
