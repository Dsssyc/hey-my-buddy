"""Preserve observed skipped objects when a later removal in the phase fails."""
from contextlib import contextmanager

from ...errors import BoardError


@contextmanager
def collect_skipped():
    skipped = []
    try:
        yield skipped
    except (BoardError, OSError) as error:
        error.deletion_skipped = [*skipped, *getattr(error, "deletion_skipped", ())]
        raise
