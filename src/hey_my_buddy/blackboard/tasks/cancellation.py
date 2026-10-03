"""Read-only attribution from the first durable cancellation of one run."""

from __future__ import annotations

import json
import re


_ACTOR = re.compile(r"(?:host:[^\x00-\x1f\x7f]{1,256}|console:[^\x00-\x1f\x7f]{1,128})\Z")


def for_run(connection, run_id: str) -> dict | None:
    # A helper cancelled by its owner has its own event. Never borrow a later
    # cancellation from an ancestor or a subsequent explicit cancel command.
    row = connection.execute(
        "SELECT payload_json FROM events WHERE task_id=?"
        " AND kind IN ('workflow.cancelled','workflow.helper_cancelled')"
        " AND seq > COALESCE((SELECT MAX(seq) FROM events WHERE task_id=?"
        " AND kind IN ('workflow.continued','workflow.helper_resumed','workflow.auto_continued')),0)"
        " ORDER BY seq LIMIT 1", (run_id, run_id),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    def bounded(key: str, limit: int) -> str | None:
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value[:limit] if value else None

    actor = bounded("actor", 261) if isinstance(payload.get('actor'), str) and len(payload['actor']) <= 261 else None
    if actor != "service-stop" and (actor is None or _ACTOR.fullmatch(actor) is None):
        actor = None
    return {"actor": actor, "reason": bounded("reason", 4000)}
