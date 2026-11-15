# Launch drafts

Prepared for the repository owner. These messages have not been posted. Use the updated
default branch after the PR is merged, and check the destination's current posting rules.
The [growth decision](GITHUB_GROWTH.md) records the audience, scope, and open questions.

## For developers using Codex or Claude

**Title:** Paper Pilot: give Codex and Claude access to full research papers

I built Paper Pilot for questions that need evidence before implementation: which RAG
approach fits this corpus, or how to build an inexpensive soil-pH measurement circuit.

It connects your existing agent to six academic databases, downloads accessible PDFs,
and lets the agent read the full text in order or inspect the original PDF and page images.
The research instructions ask it to compare methods, seek contrary evidence, and cite pages.

You can add it to Codex with:

```bash
codex mcp add paper_pilot -- uvx --from git+https://github.com/aytzey/paper-pilot paper-pilot
```

Install uv and Git first. Paper Pilot needs no separate account, API key, or Zotero setup;
your AI client provides the model.

A recorded MCP check delivered all 15 pages of Attention Is All You Need: 39,498 extracted
characters across two responses. The repository includes the counts, cursors, PDF hash,
and reproduction steps. This is a transport check, not a model-quality benchmark.

[Try Paper Pilot](https://github.com/aytzey/paper-pilot#quick-start) or
[inspect the example](https://github.com/aytzey/paper-pilot/blob/main/examples/full-paper-walkthrough.md).
If you try it, a DOI and the step that worked or failed would help. Star it if you want to keep it handy.

## For researchers

**Title:** Let your AI assistant inspect the methods, figures, and references of a paper

Paper Pilot connects Codex, Claude, and other MCP clients to academic search and full-paper
access. You can ask for a comparison across disciplines, inspect a local PDF, and optionally
save papers in Zotero.

For example: investigate soil-pH measurement across soil science, electrode materials,
and electronics. Read the decisive papers, compare calibration and drift under comparable
conditions, and identify the next experiment. The tool provides access; the agent still has
to evaluate the evidence.

It searches six databases and uses Unpaywall when a PDF needs resolving. Pages without
extractable text and failed sources are reported. It does not guarantee exhaustive coverage.

[Setup and a recorded example](https://github.com/aytzey/paper-pilot#quick-start).
I would like to hear about papers you know well: what did the assistant miss, and which page
would have changed its answer?

## Maintainer notes

- Publish only claims supported by [the verification record](../examples/full-text-verification.json).
  Do not describe the CLI reading pack as a finished literature review or advertise a PyPI command yet.
- The first launch downloads dependencies. An idle stdio process is expected until an MCP client connects.
- For connection failures, request the client, command, and error with credentials removed.
  For PDF failures, request the DOI and access warning. A provider rate limit is not an empty literature search.
- Review incoming cases before sharing them as success stories; an uploaded report alone does not prove a full read.
- Keep the main promise and install command consistent with the README. Start with one relevant channel
  whose rules permit the post; use the feedback before preparing another submission.
