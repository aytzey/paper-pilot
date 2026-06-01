# Publishing

This repository is set up for two distribution paths:

1. GitHub Releases with attached wheel and sdist artifacts
2. PyPI publishing through a manual GitHub Actions workflow

## Local Build

```bash
uv build
```

Artifacts are written to `dist/`:

- `paper_pilot-<version>.tar.gz`
- `paper_pilot-<version>-py3-none-any.whl`

## GitHub Release Flow

Push a version tag:

```bash
git tag -a v0.2.0 -m "v0.2.0"
git push origin main --tags
```

The release workflow will:

1. run tests
2. build wheel and sdist
3. attach them to the GitHub Release

## PyPI Publishing

The repository includes a manual `publish-pypi.yml` workflow designed for PyPI Trusted Publishing.

Recommended setup:

1. Create the `paper-pilot` project on PyPI (the name is currently unclaimed)
2. In PyPI, add a Trusted Publisher for this GitHub repository and the `publish-pypi.yml` workflow (environment `pypi`)
3. Run the `Publish to PyPI` workflow from GitHub Actions

See [launch/PYPI_PUBLISH.md](launch/PYPI_PUBLISH.md) for the full step-by-step.

The workflow can target:

- `pypi`
- `testpypi`

## Why Manual PyPI Publish

Manual dispatch is safer for an early-stage project because it avoids accidental failed releases before PyPI Trusted Publishing is configured.
