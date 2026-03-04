import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("REPOEVOLVE_DATA_DIR", Path.home() / ".repoevolve"))
