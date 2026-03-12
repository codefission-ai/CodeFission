.PHONY: dev build-frontend build publish clean test

# Development: builds frontend + starts server with hot reload
dev:
	./run.sh

# Build frontend static assets
build-frontend:
	cd frontend && npm install && npm run build

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

# Clean build artifacts
clean:
	rm -rf frontend/dist dist codefission/static
