"""UTC timestamps shared by native facts without changing their precision."""
from datetime import datetime, timezone


def utc_now() -> str:
    """ISO 8601 UTC with automatic fractional seconds and a ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
