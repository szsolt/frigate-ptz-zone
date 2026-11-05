"""Writing the service's JSON files."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write(path: Path, data: Any, indent: int | None = None) -> None:
    """Through a temporary file, so neither a reader nor a stop mid-write ever leaves half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=indent))
    os.replace(tmp, path)
