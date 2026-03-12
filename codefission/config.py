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

# Data directory: config file > env var > default
DATA_DIR = Path(
    _cfg.get("data_dir")
    or os.environ.get("CODEFISSION_DATA_DIR")
    or str(Path.home() / ".codefission")
)
