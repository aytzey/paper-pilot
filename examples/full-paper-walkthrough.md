# From a paper title to the full document

This example checks whether a whole paper reaches the agent over MCP. It is a recorded
transport check from 15 November 2026, not a simulated conversation or a claim about an AI's understanding.

## Try it with your agent

After [installing Paper Pilot](../README.md#quick-start), ask:

> Use Paper Pilot to find “Attention Is All You Need”. Read the full paper, inspect the
> architecture figure, and explain the method and its limitations with PDF page citations.
> Tell me which version you read and whether any pages were unreadable.

For a repeatable document choice, use [arXiv 1706.03762v7](https://arxiv.org/abs/1706.03762v7).
Search results and PDF versions can change, so compare the actual file hash before comparing counts.

## Recorded result

The [verification JSON](full-text-verification.json) contains the paper identity, PDF hash,
batch cursors, byte-transfer checks, and limitations. Machine-specific local paths were removed
from the original local receipts; the measured values were retained.

| Text response | Characters | PDF pages touched | Next position |
|---|---:|---|---|
| 1 | 20,000 | 1–7 | Page 7, character 1,885 |
| 2 | 19,498 | 7–15 | End of document |

The same page can span two responses. Concatenating the responses produced exactly the
39,498 characters extracted from all 15 pages, including the references. The original PDF
contained 2,215,244 bytes; both MCP resource retrieval and requested base64 embedding returned
identical bytes. A rendered page also reached the client as an image block.

The first response comes from `download_paper` itself; the largest response in the check was
23,114 characters, under the output limit of both Codex and Claude Code.

## Reproduce the transport check

These are calls for an MCP client, not Python functions to paste into a shell:

1. Call `download_paper(pdf_url="https://arxiv.org/pdf/1706.03762v7")`. Its `full_text` is the
   first batch, starting at page 1, character 0.
2. Call `read_pdf_text(pdf_path=<returned pdf_path>)` with `full_text.next_cursor`.
3. Save every response's text segments in order. Pass the same `pdf_path` and each
   `next_cursor.start_page` / `next_cursor.start_char` into the next call until the cursor is null.
4. Check extraction warnings. Compare the joined text with extraction from every page of the
   downloaded file; PDF page labels in formatted reports are not part of the extracted text.
5. Call `read_pdf_document` and retrieve its resource, or request `embed_base64=true` if supported.
   Compare the delivered PDF bytes with the downloaded file. Use `render_pdf_pages` for figures.

The [reading guide](../docs/READING.md) explains response limits and PDF delivery choices.

## A case across disciplines

The same reading path was checked with two papers relevant to the soil-pH design prompt:

| Paper | Copy checked | Pages | Characters | Responses |
|---|---|---:|---:|---:|
| [Distributable screen-printed soil pH sensor demonstrates robust response across variable soil conditions](https://doi.org/10.1038/s41598-026-57457-7) | Accepted manuscript | 24 | 60,332 | 4 |
| [Espial: Electrochemical Soil pH Sensor for In Situ Real-Time Monitoring](https://doi.org/10.3390/mi14122188) | Downloaded article | 13 | 35,831 | 2 |

Both complete extracted texts were delivered: 37 pages, 96,163 characters, six responses.
This does not select a sensor or validate a circuit. Comparing measurement conditions,
calibration, and contrary results is the next research task for the agent.

## Papers that are hard to reach

A PDF link in a search index often does not work for a script. The same check called
`download_paper` on papers chosen because the previous release could not read them:

| Situation | Call | Result |
|---|---|---|
| Publisher answers scripts with a bot challenge | `doi="10.3390/s110100573"` | 26 pages, 72,081 characters from the PubMed Central open-data PDF |
| Paywalled at the publisher; author manuscript in PubMed Central | `doi="10.1021/acs.biochem.9b00573"` | 45,218 characters. PubMed Central holds text only, so the file is that text laid out as a PDF and a warning says page numbers are not the publisher's |
| Publisher answers 403; a `doi.org` link pasted as the URL | `pdf_url="https://doi.org/10.1016/j.omtm.2020.09.002"` | 13 pages, 60,152 characters, plus links to two supplement PDFs |
| arXiv abstract page pasted as the URL | `pdf_url="https://arxiv.org/abs/1706.03762"` | The arXiv PDF, 15 pages |
| Paywalled, no open copy anywhere | `doi="10.1109/jsen.2024.3514735"` | Not downloaded. The error says no open copy is known and that the paper is probably paywalled |

In every downloaded case the joined responses equalled the text extracted from the file.

A wider sample gives the honest picture. Taking the first 12 results from each of five topic
searches (60 papers, paywalled work included), the previous release reached the full text of 32
and this one reaches 40. The other 20 are paywalled articles or dead links. A failed download now
returns in about a second instead of 7 to 37, because a 403 or a 404 is no longer retried.

## Two real agent sessions

The transport check says what the server sends. To see what an agent does with it, a Claude Code
session and a Codex CLI session (codex-cli 0.169.0) each got this prompt on 15 November 2026, with
no hint about how to use the tools:

> Use the paper-pilot tools to get the paper with DOI 10.3390/s110100573 and answer from the paper
> itself, not from memory or the abstract: (1) After calibration, what range of mean absolute errors
> does the paper report? (2) What does the paper suggest installing to deal with blocking by residues
> and weed roots? (3) What is the number, and who are the authors, of the last entry in the reference list?

The answers are on PDF pages 22 and 26 of 26, and the publisher's own PDF link is closed to scripts.
Both agents called `download_paper` once and `read_pdf_text` three times, reached `next_cursor: null`,
and answered all three questions correctly with page numbers: 0.28 to 0.40 pH units, a video camera,
and reference 60 by Schirrmann and Domsch. Both named the PubMed Central copy they had read. Codex
also rendered pages 22 and 26 to check them visually.

This is one paper and one run per client. It shows the reading path works end to end in both
clients; it is not a benchmark.

## What this proves

Paper Pilot can deliver complete extracted text and original PDF bytes through MCP, and a Claude Code
and a Codex session each read a 26-page paper to the end with it. The checks do not prove model
comprehension in general, correct figure interpretation, or exhaustive literature search.
Scans without readable text remain explicit gaps.
