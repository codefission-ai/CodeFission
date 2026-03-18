# Release Workflow

## Overview

CodeFission is published to PyPI as a Python package. The wheel bundles the
pre-built React frontend, so end users only need `uv` (or pip) and git — no Node.js.

Recommended install for end users:

```bash
uv tool install codefission
fission
```

## How to release

### 1. Bump version

Edit `pyproject.toml`:

```toml
version = "0.1.6"  # → "0.1.7"
```

Versioning convention:
- **Patch** (0.1.0 → 0.1.1): bug fixes
- **Minor** (0.1.0 → 0.2.0): new features
- **Major** (0.x.y → 1.0.0): breaking changes

### 2. Build and publish

```bash
make publish
```

This runs the full pipeline:

```
npm install + npm run build   → ui/dist/
cp ui/dist → codefission/static/
uv run hatch build            → dist/codefission-X.Y.Z-py3-none-any.whl
uv run hatch publish          → uploads to PyPI
```

### 3. Commit and push

```bash
git add pyproject.toml
git commit -m "Bump version to X.Y.Z"
git push
```

## PyPI auth

The PyPI API token is stored in `~/.zshrc`:

```bash
export HATCH_INDEX_USER=__token__
export HATCH_INDEX_AUTH=pypi-...
```

`uv run hatch publish` picks these up automatically.

To publish manually without hatch:

```bash
TWINE_USERNAME=__token__ TWINE_PASSWORD="$HATCH_INDEX_AUTH" \
  python -m twine upload dist/codefission-X.Y.Z*
```

Token management: https://pypi.org/manage/account/token/

> **Note:** Never paste tokens in plaintext in chats or public channels —
> PyPI's secret scanning may auto-revoke them.

## What's in the wheel

| Content | Source | Destination in wheel |
|---------|--------|---------------------|
| Python backend | `codefission/*.py` | `codefission/` |
| Agent bridge | `codefission/agentbridge/` | `codefission/agentbridge/` |
| Handlers | `codefission/handlers/` | `codefission/handlers/` |
| Store | `codefission/store/` | `codefission/store/` |
| Orchestrator | `codefission/orchestrator/` | `codefission/orchestrator/` |
| Frontend build | `ui/dist/` | `codefission/static/` |
| CLI entry point | `codefission/server.py:main` | → `fission` command |

Tests (`codefission/tests/`) are excluded from the wheel.

## Local development

```bash
make install       # editable install + build frontend
make dev           # same as install (rebuilds ui + reinstalls)
make deploy        # build ui + install fission globally via uv tool
fission            # run after install
fission --port 3000  # custom port
```

`make deploy` is useful when you want the `fission` command available globally
(outside the project venv) while still pointing at your local source:

```bash
uv tool install -e . --force
```

## Auto-update (end users)

`fission` checks PyPI once per 24 hours on startup. If a newer version is
available it prompts the user. Users can also check manually:

```bash
fission --update
```

The upgrade command is chosen automatically based on how fission was installed:
- Installed via `uv tool` → runs `uv tool upgrade codefission`
- Installed via `pip` → runs `pip install -U codefission`

## Prerequisites for end users

- Python 3.9+
- git
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`) and/or
  Codex CLI (`npm install -g @openai/codex`)
