"""Provider cooldowns derived only from committed quota rejections."""
from datetime import datetime, timedelta, timezone

from ..store.db import utc_now


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def _iso(stamp):
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _view(row):
    return {"provider": row["provider"], "coolingUntil": row["cooling_until"],
            "consecutiveRejections": row["consecutive_rejections"]}


def record_rejection(connection, harness, provider, failure, *, now=None):
    """Count one new quota rejection inside the attempt's result transaction."""
    present = _time(now or utc_now())
    row = connection.execute(
        "SELECT consecutive_rejections FROM provider_cooling WHERE harness=? AND provider=?",
        (harness, provider),
    ).fetchone()
    count = row["consecutive_rejections"] + 1 if row else 1
    seconds = 86400 if count >= 7 else min(1800 * 2 ** (count - 1), 86400)
    until = present + timedelta(seconds=seconds)
    reset = _time(failure.get("resetsAt"))
    if reset is not None and reset > present:
        until = min(until, reset)
    connection.execute(
        "INSERT INTO provider_cooling(harness,provider,consecutive_rejections,cooling_until,last_rejected_at)"
        " VALUES(?,?,?,?,?) ON CONFLICT(harness,provider) DO UPDATE SET"
        " consecutive_rejections=excluded.consecutive_rejections,cooling_until=excluded.cooling_until,"
        " last_rejected_at=excluded.last_rejected_at",
        (harness, provider, count, _iso(until), _iso(present)),
    )


def clear_on_success(connection, harness, provider, *, started_at):
    """A success begun after the last rejection ends its rejection sequence."""
    connection.execute(
        "DELETE FROM provider_cooling WHERE harness=? AND provider=?"
        " AND julianday(?)>julianday(last_rejected_at)",
        (harness, provider, started_at),
    )


def provider_state(connection, harness, provider):
    row = connection.execute(
        "SELECT * FROM provider_cooling WHERE harness=? AND provider=?", (harness, provider),
    ).fetchone()
    return _view(row) if row else None


def providers(connection, harness):
    return [_view(row) for row in connection.execute(
        "SELECT * FROM provider_cooling WHERE harness=? ORDER BY provider", (harness,))]


def cooling(connection, buddy, *, now=None):
    """The active cooldown of this buddy's provider, independent of its model."""
    if buddy is None:
        return None
    state = provider_state(connection, buddy["harness"], buddy["provider"])
    if state is not None and state["coolingUntil"] is not None and _time(state["coolingUntil"]) > _time(now or utc_now()):
        return state
    return None


def redetect(connection, harness, provider):
    """Clear the deadline while preserving the consecutive rejection count."""
    changed = connection.execute(
        "UPDATE provider_cooling SET cooling_until=NULL"
        " WHERE harness=? AND provider=? AND cooling_until IS NOT NULL", (harness, provider),
    )
    return {"harness": harness, "provider": provider, "cleared": changed.rowcount == 1}
