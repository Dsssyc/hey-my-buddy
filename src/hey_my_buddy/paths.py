"""Path rules for files on the machine running this process."""
from __future__ import annotations

import os
from pathlib import Path


def is_absolute_path(value: str | os.PathLike[str]) -> bool:
    """Use the local filesystem syntax, including a drive and root on Windows."""
    return Path(value).is_absolute()
