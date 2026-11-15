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

The GitHub installation also needs [Git](https://git-scm.com/downloads).

PyMuPDF (the PDF engine) ships prebuilt wheels for Windows, macOS (Intel + Apple Silicon), and Linux,
so no C toolchain is needed.

## Two ways to launch

- **From GitHub:** `command: "uvx"`, `args: ["--from", "git+https://github.com/aytzey/paper-pilot", "paper-pilot"]`. No manual clone or PyPI release is needed.
- **From a local checkout (works today):** `command: "uv"`, `args: ["--directory", "<abs path>", "run", "paper-pilot"]`.

Email configuration is optional. Unpaywall uses `UNPAYWALL_EMAIL`, then `OPENALEX_EMAIL`,
then `nomail@mail.com` when both are missing or blank.

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
      "args": ["--from", "git+https://github.com/aytzey/paper-pilot", "paper-pilot"]
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
      "args": ["/c", "uvx", "--from", "git+https://github.com/aytzey/paper-pilot", "paper-pilot"]
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
      "args": ["--from", "git+https://github.com/aytzey/paper-pilot", "paper-pilot"]
    }
  }
}
```

On Windows use `"command": "cmd", "args": ["/c", "uvx", "--from", "git+https://github.com/aytzey/paper-pilot", "paper-pilot"]`.

## Claude Code

```bash
claude mcp add --scope user paper-pilot -- uvx --from git+https://github.com/aytzey/paper-pilot paper-pilot
```

Flags come before the name; `--` separates the name from the command. Verify with `claude mcp list` or `/mcp`. Text is available through MCP even when the client cannot open a local PDF.

## Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.paper_pilot]
command = "uvx"
args = ["--from", "git+https://github.com/aytzey/paper-pilot", "paper-pilot"]
```

Or add it from the CLI: `codex mcp add paper_pilot -- uvx --from git+https://github.com/aytzey/paper-pilot paper-pilot`.

## Windows: "spawn uv ENOENT"

Claude Desktop and Cursor spawn the command without a shell, so a bare `uv`/`uvx` on Windows may not resolve. Two fixes:

1. Wrap with cmd: `"command": "cmd", "args": ["/c", "uvx", "--from", "git+https://github.com/aytzey/paper-pilot", "paper-pilot"]`.
2. Use the full path: find it with `where.exe uvx` (typically `C:\Users\<you>\.local\bin\uvx.exe`) and set that as `command`.

If a log shows an unexpanded `%APPDATA%`, add `"env": {"APPDATA": "C:\\Users\\<you>\\AppData\\Roaming\\"}`.

## PDF delivery and client limits

| Delivery | Requirement |
|---|---|
| `read_pdf_text` / `get_pdf_page_text` | An MCP client that can read tool text responses |
| `render_pdf_pages` | A client/model that accepts image content from MCP tools |
| `read_pdf_document` local path | The client can access the server's filesystem |
| PDF resource link / optional base64 | The client supports fetching or consuming PDF resources |

The recorded checks verify MCP transport. They do not certify every client version or GUI.
If a client cannot consume PDF resources, use sequential text and supported page images.

## Optional settings

Basic research works without these settings. Add them only for a feature you use:

- `UNPAYWALL_EMAIL` or `OPENALEX_EMAIL`: your contact email; otherwise Unpaywall uses `nomail@mail.com`.
- `OPENALEX_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY`: free keys that lift the search limits of those two
  sources. Without them OpenAlex allows about 100 searches a day per network and Semantic Scholar is
  usually rate-limited; search then continues on the other sources and says which one was skipped.
- `PAPER_PILOT_DATA_DIR`: where downloads, extracted text and reports go. The default is
  `~/.paper-pilot`, one folder per user, so nothing is written into the project your agent is
  working in and a paper downloaded once is reused everywhere. Set it to a folder inside your
  workspace if your client only lets the agent open files there and you want it to read
  `pdf_path` or `text_path` with its own file tools; text and page images sent over MCP need no file access.
- `ZOTERO_LOCAL=true`: enable local Zotero. Run `healthcheck` before requesting writes;
  full attachment/collection writes need the bridge described in [AGENTS.md](../AGENTS.md#zotero-rules).

JSON clients accept an `env` object alongside `command` and `args`. Codex TOML uses
`[mcp_servers.paper_pilot.env]`. Full configuration is in the [reading guide](READING.md#configuration).

## Update or use a local checkout

For an existing GitHub installation, refresh the cached package and restart your MCP client:

```bash
uvx --refresh-package paper-pilot --from git+https://github.com/aytzey/paper-pilot paper-pilot --help
```

For development:

```bash
git clone https://github.com/aytzey/paper-pilot.git
cd paper-pilot
uv sync
```

Then use `command: "uv"` and `args: ["--directory", "<absolute checkout path>", "run", "paper-pilot"]`.
A server waiting for stdio input is normal; it does not open a chat UI.

## Suggested workflow (any client)

For practical design decisions, start with the research workflow sent in the MCP initialization
instructions: clarify constraints, make discipline-specific `search_literature` queries with
`sort_by="newest", open_access_only=False`, follow the needed source `next_request` arguments,
and read selected papers completely. Compare conditions and contrary evidence before recommending
an approach and its first validation experiment. The server cannot certify that a model followed
these instructions. See [the shared workflow](../README.md#research-a-practical-decision).

For a quick reading pack:

1. `healthcheck`
2. `deep_read_topic`: read the first batch in every `deep_reads[*].full_text`
3. `read_pdf_text` with each PDF's `next_cursor.start_page` / `start_char`, repeatedly until null
4. `render_pdf_pages` for figures/tables; disclose pages without extractable text
5. `write_to_zotero=true` only after Zotero health is confirmed

No client needs filesystem access or PDF-resource support for `read_pdf_text`: the text is in the
tool response. A batch holds up to 20,000 text characters and 20 page segments, which stays under the
tool-output limit of Codex (about 10,000 tokens) and Claude Code (25,000 tokens).
An oversized page resumes at the exact character.
Read from page 1, char 0 through every continuation before claiming complete reading, and disclose
unresolved extraction gaps. PDFs need a readable text layer; figures/formulas still need visual inspection.

## Prompt starters

- `Research <topic>, deep-read the best OA papers, and summarize the evidence.`
- `Find the strongest papers on <topic>, render the important pages, and compare the figures.`
- `Check local Zotero, then create a collection and sync the report and PDFs.`
