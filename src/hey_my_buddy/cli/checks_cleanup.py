"""Deletion policy only for the checks runner's private test roots."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat


def remove_private_test_root(directory: Path) -> None:
    """Remove a stopped fixture, retrying only Windows READONLY regular files.

    The caller must first close its resources and confirm its work has stopped.
    A sharing violation or any other deletion failure preserves the remaining tree.
    """
    directory = Path(directory)

    def readonly_file(operation, name, error):
        if os.name != "nt" or operation is not os.unlink or getattr(error, "winerror", None) != 5:
            raise error
        path = Path(name)
        # Never chmod a link/reparse target, including through a replaced ancestor.
        for part in (path, *path.parents):
            info = part.lstat()
            attributes = getattr(info, "st_file_attributes", 0)
            if stat.S_ISLNK(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise error
            if part == path and (not stat.S_ISREG(info.st_mode) or not attributes & stat.FILE_ATTRIBUTE_READONLY):
                raise error
            if part == directory:
                break
        else:
            raise error
        os.chmod(path, stat.S_IWRITE)
        operation(name)

    shutil.rmtree(directory, onexc=readonly_file)
