"""Shared fixtures for RepoEvolve tests."""

import sys
from pathlib import Path

# Make backend importable
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
