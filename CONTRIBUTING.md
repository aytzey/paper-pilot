# Contributing

Thanks for contributing to `paper-pilot`.

## Development Setup

```bash
uv venv
source .venv/bin/activate
uv sync
```

Run tests before opening a pull request:

```bash
uv run pytest
```

## What Contributions Are Useful

- new scholarly source adapters
- better OA resolution and fallback logic
- Zotero local integration improvements
- PDF parsing and rendering improvements
- documentation and example MCP client configs
- tests that cover network edge cases

## Pull Request Guidelines

1. Keep changes focused
2. Add or update tests when behavior changes
3. Update docs when user-facing behavior changes
4. Explain the motivation and expected impact in the PR description

## Reporting Bugs

Please include:

- the MCP client used
- the command or tool call you ran
- relevant environment variables with secrets removed
- network constraints such as proxy or custom CA usage
- the exact error message or traceback
