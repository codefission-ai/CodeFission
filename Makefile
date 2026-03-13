SHELL := /bin/bash

# Ensure compatible Node.js (Vite 7 requires >=20.19 or >=22.12)
NVM_INIT := source "$${NVM_DIR:-$$HOME/.nvm}/nvm.sh" 2>/dev/null && nvm use 22 2>/dev/null &&

.PHONY: dev build-frontend build publish clean test install deploy

# First-time setup: editable install + build frontend
install:
	uv pip install -e .
	$(NVM_INIT) cd frontend && npm install && npm run build
	rm -rf codefission/static
	cp -r frontend/dist codefission/static

# Development: install editable + rebuild frontend
dev: install

# Build frontend static assets
build-frontend:
	$(NVM_INIT) cd frontend && npm install && npm run build

# Bundle frontend into Python package, then build wheel + sdist
build: build-frontend
	rm -rf codefission/static
	cp -r frontend/dist codefission/static
	uv run hatch build

# Publish to PyPI (builds first if needed)
publish: build
	uv run hatch publish

# Run tests
test:
	uv run pytest

# Build frontend + install fission globally so it works from any repo
deploy: build-frontend
	rm -rf codefission/static
	cp -r frontend/dist codefission/static
	uv tool install -e . --force

# Clean build artifacts
clean:
	rm -rf frontend/dist dist codefission/static
