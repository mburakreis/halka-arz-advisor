"""A tiny best-effort ``.env`` loader.

Deliberately not a dependency on ``python-dotenv`` for one file's worth
of ``KEY=VALUE`` parsing — see the project's general dependency-wariness.
Real environment variables always win over anything in the file.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
