"""Routing snapshot publication and reclamation under the board writer."""
from __future__ import annotations

import re

from ... import private_dirs, private_files
from ...paths import native_path
from .router_history import ACTIVE_ATTEMPT_STATES
from .router_sequence import request_snapshot


def publish_round(store, connection, decision_id: str, round: int) -> dict:
    """Publish the frozen query input shared by every dispatch of this round."""
    document = request_snapshot(connection, decision_id, round)["baseInput"]
    reference, created = private_files.publish_snapshot(
        store.directory, "routing", decision_id, document, round=round)
    if created:
        def rollback():
            # The rollback released SQLite's original lock. Reacquire it and
            # check whether an intervening writer adopted this round's file.
            with store.db.write() as writer:
                if writer.execute("SELECT 1 FROM router_requests WHERE decision_id=? AND round=?",
                                  (decision_id, round)).fetchone() is None:
                    private_files.discard_snapshot_reference(
                        store.directory, "routing", decision_id, reference, round=round)
        store.db.after_rollback(connection, rollback)
    return reference


def retained_rounds(connection, state) -> set:
    """Return physical round directories that an active decision or attempt owns."""
    states = ",".join("?" for _ in ACTIVE_ATTEMPT_STATES)
    rows = connection.execute(
        "SELECT r.decision_id,r.round FROM router_requests r"
        " JOIN evaluation_decisions d ON d.decision_id=r.decision_id"
        " WHERE d.status IN ('queued','running')"
        " UNION SELECT d.decision_id,d.round FROM router_dispatches d"
        " JOIN attempts a ON a.task_id=d.task_id"
        f" WHERE a.execution_state IN ({states}) AND a.shutdown_confirmed IS NOT 1",
        ACTIVE_ATTEMPT_STATES)
    # Generate names without descending into a potentially linked on-disk owner.
    return {private_dirs.snapshot_root(state, "routing", row[0]) / str(row[1]) for row in rows}


def inventory(state):
    """Yield managed round paths and protection reasons for unsafe entries."""
    root = private_dirs.snapshot_root(state, "routing")
    if private_dirs.linked(root) or (native_path(root).exists() and not native_path(root).is_dir()):
        yield root, ["snapshot-directory-unproven"]
        return
    for owner in sorted(native_path(root).glob("*")):
        owner = root / owner.name
        if (re.fullmatch(r"[0-9a-f]{64}", owner.name) is None
                or private_dirs.linked(owner) or not native_path(owner).is_dir()):
            yield owner, ["snapshot-directory-unproven"]
            continue
        for entry in sorted(native_path(owner).glob("*")):
            path = owner / entry.name
            safe = (re.fullmatch(r"[1-9][0-9]*", path.name) is not None
                    and not private_dirs.linked(path) and native_path(path).is_dir())
            yield path, [] if safe else ["snapshot-directory-unproven"]


def remove_round(path) -> None:
    """Remove a reclaimed round and prune its now-empty owner directory."""
    private_dirs.remove_tree(path)
    owner = native_path(path.parent)
    if owner.is_dir() and not any(owner.iterdir()):
        owner.rmdir()


def recover_files(store) -> None:
    """Sweep abandoned round directories while admissions cannot publish."""
    root = private_dirs.snapshot_root(store.directory, "routing")
    if not native_path(root).exists() or private_dirs.linked(root):
        return
    with store.db.write() as connection:
        retained = retained_rounds(connection, store.directory)
        for path, reasons in list(inventory(store.directory)):
            if not reasons and path not in retained:
                remove_round(path)
