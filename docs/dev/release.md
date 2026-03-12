# Release Workflow

## Overview

CodeFission is published to PyPI as a Python package. The wheel bundles the
pre-built React frontend, so end users only need Python and git — no Node.js.

```
pip install codefission
fission
```

## How to release

### 1. Bump version

Edit `pyproject.toml`:

```toml
version = "0.1.1"  # → "0.2.0"
```

Versioning convention:
- **Patch** (0.1.0 → 0.1.1): bug fixes
- **Minor** (0.1.0 → 0.2.0): new features
- **Major** (0.x.y → 1.0.0): breaking changes

### 2. Publish

```bash
make publish
```

This runs the full pipeline:

```
npm install + npm run build   → frontend/dist/
cp frontend/dist → codefission/static/
hatch build                   → dist/codefission-X.Y.Z-py3-none-any.whl
hatch publish                 → uploads to PyPI
```

### 3. Commit and push

```bash
git add pyproject.toml
git commit -m "Release vX.Y.Z"
git push codefission main
```

## PyPI auth

The PyPI API token is stored in `~/.zshrc` as environment variables:

```bash
export HATCH_INDEX_USER=__token__
export HATCH_INDEX_AUTH=pypi-...
```

Token management: https://pypi.org/manage/account/token/

## What's in the wheel

| Content | Source | Destination in wheel |
|---------|--------|---------------------|
| Python backend | `codefission/*.py` | `codefission/` |
| Service modules | `codefission/services/` | `codefission/services/` |
| Provider modules | `codefission/providers/` | `codefission/providers/` |
| Frontend build | `frontend/dist/` | `codefission/static/` |
| CLI entry point | `codefission/cli.py` | → `fission` command |

Tests (`codefission/tests/`) are excluded from the wheel.

## Local development

```bash
make install       # editable install + build frontend
make dev           # install + build + run fission
fission            # run directly (after install)
fission 3000       # custom port
```

For global install (available outside the project venv):

```bash
uv tool install -e .
fission
```

## Prerequisites for end users

- Python 3.12+
- git
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)

The `fission` command checks for git and claude at startup and prints
install instructions if missing.
