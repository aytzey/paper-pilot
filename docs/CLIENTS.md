# Client & platform setup

Paper Pilot is a standard stdio MCP server, so it runs on any MCP client and on Windows, macOS, and
Linux. This guide gives the exact config for each client and OS, plus what each client can actually
do with the PDFs.

## Prerequisites (all platforms)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (it ships `uvx` too):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

PyMuPDF (the PDF engine) ships prebuilt wheels for Windows, macOS (Intel + Apple Silicon), and Linux,
so no C toolchain is needed.

## Two ways to launch

- **From PyPI (once published):** `command: "uvx"`, `args: ["paper-pilot"]`.
- **From a local checkout (works today):** `command: "uv"`, `args: ["--directory", "<abs path>", "run", "paper-pilot"]`.

Set `OPENALEX_EMAIL` and `UNPAYWALL_EMAIL` (free, no signup) so open-access PDF resolution works.

## Config file locations

| Client | macOS / Linux | Windows |
| --- | --- | --- |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS; **no official Linux build**, use Claude Code on Linux) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `.cursor/mcp.json` (this project) or `~/.cursor/mcp.json` (global) | same paths |
| Codex CLI | `~/.codex/config.toml` (`[mcp_servers.*]`) | same |
| Claude Code | `claude mcp add ...` (stored in `~/.claude.json`, or committable `.mcp.json` with `--scope project`) | same |

All JSON clients use the same shape: `{"mcpServers": {"paper-pilot": {"command": ..., "args": [...], "env": {...}}}}`.

## Claude Desktop

macOS:

```json
{
  "mcpServers": {
    "paper-pilot": {
      "command": "uvx",
      "args": ["paper-pilot"],
      "env": { "OPENALEX_EMAIL": "you@example.com", "UNPAYWALL_EMAIL": "you@example.com", "ZOTERO_LOCAL": "true" }
    }
  }
}
```

Windows (see the ENOENT note below):

```json
{
  "mcpServers": {
    "paper-pilot": {
      "command": "cmd",
      "args": ["/c", "uvx", "paper-pilot"],
      "env": { "OPENALEX_EMAIL": "you@example.com", "UNPAYWALL_EMAIL": "you@example.com", "ZOTERO_LOCAL": "true" }
    }
  }
}
```

For a local checkout, swap to `"command": "uv"`, `"args": ["--directory", "C:\\path\\to\\paper-pilot", "run", "paper-pilot"]` (escaped backslashes, or forward slashes). Restart Claude Desktop after editing. Logs: macOS `~/Library/Logs/Claude`, Windows `%APPDATA%\Claude\logs`.

## Cursor

Put this at `.cursor/mcp.json` (this repo) or `~/.cursor/mcp.json` (global), then enable it in Settings (`Cmd/Ctrl+Shift+J`) → Model Context Protocol (a green dot means connected). See [`examples/cursor.mcp.json`](../examples/cursor.mcp.json).

```json
{
  "mcpServers": {
    "paper-pilot": {
      "command": "uvx",
      "args": ["paper-pilot"],
      "env": { "OPENALEX_EMAIL": "you@example.com", "UNPAYWALL_EMAIL": "you@example.com", "ZOTERO_LOCAL": "true" }
    }
  }
}
```

On Windows use `"command": "cmd", "args": ["/c", "uvx", "paper-pilot"]`.

## Claude Code

```bash
claude mcp add --scope user \
  --env OPENALEX_EMAIL=you@example.com --env UNPAYWALL_EMAIL=you@example.com --env ZOTERO_LOCAL=true \
  paper-pilot -- uvx paper-pilot
```

Flags come before the name; `--` separates the name from the command. Verify with `claude mcp list` or `/mcp`. Claude Code reads local PDFs from `pdf_path` natively, so it uses every Paper Pilot feature.

## Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.paper_pilot]
command = "uvx"
args = ["paper-pilot"]

[mcp_servers.paper_pilot.env]
OPENALEX_EMAIL = "you@example.com"
UNPAYWALL_EMAIL = "you@example.com"
ZOTERO_LOCAL = "true"
```

Or add it from the CLI: `codex mcp add paper_pilot --env OPENALEX_EMAIL=you@example.com -- uvx paper-pilot`.

## Windows: "spawn uv ENOENT"

Claude Desktop and Cursor spawn the command without a shell, so a bare `uv`/`uvx` on Windows may not resolve. Two fixes:

1. Wrap with cmd: `"command": "cmd", "args": ["/c", "uvx", "paper-pilot"]`.
2. Use the full path: find it with `where uv` (typically `C:\Users\<you>\.local\bin\uv.exe`) and set that as `command`.

If a log shows an unexpanded `%APPDATA%`, add `"env": {"APPDATA": "C:\\Users\\<you>\\AppData\\Roaming\\"}`.

## What each client can do with the PDFs

Paper Pilot defaults to **file paths, not base64**, so it is light and works everywhere. How much of a PDF a client can show the model varies:

| Capability | Claude Code | Codex | Cursor | Claude Desktop |
| --- | --- | --- | --- | --- |
| Text chunks + report (JSON) | ✅ | ✅ | ✅ | ✅ |
| `get_pdf_page_text` (exact page text over the wire) | ✅ | ✅ | ✅ | ✅ |
| Open the PDF from `pdf_path` directly | ✅ (native Read) | ✅ (shell) | ⚠️ (if file access) | ❌ (no file tool) |
| `render_pdf_pages` images the model sees | ✅ | ✅ | ✅ | ✅ (≤ ~1 MB per image) |
| `read_pdf_document(embed_base64=True)` PDF bytes | ✅ via path | ✅ via path | ⚠️ | ❌ (embedded resources unreliable) |

Takeaways: text and `get_pdf_page_text` work on every client. For figures, `render_pdf_pages` returns images that Claude Desktop, Cursor, Claude Code, and Codex all show the model (keep `scale` modest on Claude Desktop to stay under its ~1 MB content cap). Inlined PDF bytes are unreliable on Claude Desktop, which is why the default is paths plus `get_pdf_page_text`.

## Suggested workflow (any client)

1. `healthcheck`
2. `research_topic` or `deep_read_topic`
3. `render_pdf_pages` when a figure or table matters
4. `get_pdf_page_text` for an exact detail on a known page
5. `write_to_zotero=true` only after Zotero health is confirmed

## Prompt starters

- `Research <topic>, deep-read the best OA papers, and summarize the evidence.`
- `Find the strongest papers on <topic>, render the important pages, and compare the figures.`
- `Check local Zotero, then create a collection and sync the report and PDFs.`
