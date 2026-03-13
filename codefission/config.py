import contextvars
import json
import os
from pathlib import Path

# Fixed location for the bootstrap config (read before DB exists)
CONFIG_FILE = Path.home() / ".codefission.json"

def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}

def save_config(updates: dict):
    """Merge updates into the config file."""
    cfg = _load_config()
    cfg.update(updates)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")

_cfg = _load_config()

# Global data directory: config file > env var > default (~/.codefission for global settings only)
DATA_DIR = Path(
    _cfg.get("data_dir")
    or os.environ.get("CODEFISSION_DATA_DIR")
    or str(Path.home() / ".codefission")
)

# ── Per-project context (set per-connection, inherited by async tasks) ──

_project_path_var: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "project_path", default=None
)


def set_project_path(path: Path):
    """Set the active project path for the current async context."""
    _project_path_var.set(path)


def get_project_path() -> Path:
    """Get the active project path. Raises if not set."""
    p = _project_path_var.get()
    if p is None:
        raise RuntimeError("No project path set in current context")
    return p


def get_project_dir() -> Path:
    """Get the .codefission dir for the active project."""
    return get_project_path() / ".codefission"


def get_global_db_path() -> Path:
    """Return the path to the single global database."""
    return DATA_DIR / "codefission.db"
