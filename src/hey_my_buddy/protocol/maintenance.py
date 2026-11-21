"""Content-addressed private files for maintenance and profile runs."""
from __future__ import annotations

from pathlib import Path

from ..paths import native_path
from ..private_dirs import linked, remove_tree, snapshot_root
from ..private_files import discard_snapshot_reference, publish_snapshot, read_snapshot


def publish(state: Path, run_id: str, value: object, *, before_write=None, namespace="maintenance") -> dict:
    reference, _ = publish_snapshot(state, namespace, run_id, value, before_write=before_write)
    return reference


def read(state: Path, run_id: str, reference: object, *, namespace="maintenance"):
    return read_snapshot(state, namespace, run_id, reference)


def discard(state: Path, run_id: str, *, namespace="maintenance") -> None:
    """Delete a finished run's transient material."""
    remove_tree(snapshot_root(state, namespace, run_id))


def discard_reference(state: Path, run_id: str, reference: dict, *, namespace="maintenance") -> None:
    discard_snapshot_reference(state, namespace, run_id, reference)


def discard_orphans(state: Path, active_run_ids, *, namespace="maintenance") -> None:
    """Reconcile material left by a service that died before its transaction hooks."""
    root = snapshot_root(state, namespace)
    if linked(root):
        return
    active = {snapshot_root(state, namespace, run_id).name for run_id in active_run_ids}
    for path in native_path(root).glob("*"):
        if path.name not in active:
            remove_tree(path)
