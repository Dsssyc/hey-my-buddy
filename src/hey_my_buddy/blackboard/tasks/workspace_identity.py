"""Stable directory identities and read-only access to proven board aliases.

Only the explicit offline upgrade registers aliases. Comparisons with a live
checkout still take a freshly inspected path/inode; an alias never grants
allocation, reservation or removal permission on its own. Historical evidence
can compare recorded forms even after a checkout has been reclaimed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path

META_KEY_PREFIX = "workspace-identity:"
MIGRATION_META_KEY = "workspace-identity-migration"
_LOCAL = threading.local()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return isinstance(value, str) and len(value) == 64 and not set(value) - set("0123456789abcdef")


def stable_identity(path) -> str:
    directory = Path(path).resolve(strict=True)
    return hashlib.sha256(_json([str(directory), directory.stat().st_ino])).hexdigest()


def legacy_device(path, recorded):
    """Prove the current inode by exactly reconstructing its recorded digest.

    Legacy manifests saved no device/inode fields. Try the current device and a
    bounded 16-bit device range, both alone and under the current high bits.
    A SHA-256 match is evidence, not a guessed device value. Outside this range
    the migration retains the old value with legacy-inode-unproven.
    """
    directory = Path(path).resolve(strict=True)
    facts = directory.stat()
    prefix = _json([str(directory)])[:-1] + b","
    suffix = b"," + str(facts.st_ino).encode() + b"]"
    high = facts.st_dev & ~0xffff
    seen = {facts.st_dev}
    if hashlib.sha256(prefix + str(facts.st_dev).encode() + suffix).hexdigest() == recorded:
        return facts.st_dev
    for low in range(1 << 16):
        for candidate in (low, high | low):
            if candidate in seen:
                continue
            seen.add(candidate)
            if hashlib.sha256(prefix + str(candidate).encode() + suffix).hexdigest() == recorded:
                return candidate
    return None


def _valid_entry(entry, old):
    if (not isinstance(entry, dict) or type(entry.get("version")) is not int or entry["version"] != 1
            or entry.get("source") != "fixed-input-and-inode" or not digest(entry.get("newId"))
            or not isinstance(entry.get("anchors"), list) or not entry["anchors"]):
        return False
    for anchor in entry["anchors"]:
        if (not isinstance(anchor, dict) or not isinstance(anchor.get("directory"), str)
                or not Path(anchor["directory"]).is_absolute() or type(anchor.get("inode")) is not int
                or anchor["inode"] <= 0 or not digest(anchor.get("manifestSha256"))
                or not isinstance(anchor.get("workspaceId"), str) or not anchor["workspaceId"]
                or anchor.get("role") not in ("checkoutId", "repositoryId", "sourceCheckoutId")
                or type(anchor.get("legacyDevice")) is not int or anchor["legacyDevice"] < 0):
            return False
        if (hashlib.sha256(_json([anchor["directory"], anchor["legacyDevice"], anchor["inode"]])).hexdigest() != old
                or hashlib.sha256(_json([anchor["directory"], anchor["inode"]])).hexdigest() != entry["newId"]):
            return False
    return True


def load_alias_map(connection) -> dict[str, str]:
    mapping = {}
    for key, value in connection.execute(
            "SELECT key,value FROM meta WHERE key>=? AND key<?", (META_KEY_PREFIX, "workspace-identity;")):
        try:
            entry = json.loads(value)
        except (TypeError, ValueError):
            continue
        old = key[len(META_KEY_PREFIX):]
        if digest(old) and _valid_entry(entry, old):
            mapping[old] = entry["newId"]
    return mapping


def canonical_sql(expression):
    """Resolve only the exact alias value registered by the atomic upgrade.

    SQLite has no SHA-256 operation. The migration validates anchor hashes and
    records each approved encoded value in its preservation-checked report.
    SQL projection requires that exact binding, so a drifted target or anchor
    cannot be trusted merely because the old id was previously registered.
    """
    value = "CASE WHEN json_valid(wi.value) THEN wi.value ELSE '{}' END"
    proof = "CASE WHEN json_valid(proof.value) THEN proof.value ELSE '{}' END"
    new = f"json_extract({value}, '$.newId')"
    return f"""COALESCE((SELECT {new} FROM meta wi, meta proof,
          json_each({proof}, '$.entries') approved
        WHERE wi.key='{META_KEY_PREFIX}' || ({expression})
          AND proof.key='{MIGRATION_META_KEY}'
          AND json_type({proof}, '$.version')='integer' AND json_extract({proof}, '$.version')=1
          AND approved.key=({expression}) AND approved.type='text' AND approved.value=wi.value
          AND length(({expression}))=64 AND ({expression}) NOT GLOB '*[^0-9a-f]*'
          AND json_type({value}, '$.version')='integer' AND json_extract({value}, '$.version')=1
          AND json_extract({value}, '$.source')='fixed-input-and-inode'
          AND json_type({value}, '$.newId')='text' AND length({new})=64
          AND {new} NOT GLOB '*[^0-9a-f]*'
        ), ({expression}))"""


def identity_reason(connection, recorded):
    row = connection.execute("SELECT value FROM meta WHERE key=?", (MIGRATION_META_KEY,)).fetchone()
    try:
        report = json.loads(row[0]) if row else {}
        return report.get("kept", {}).get(recorded)
    except (ValueError, TypeError, AttributeError):
        return None


def identity_reason_sql(expression):
    # json_each avoids embedding caller-controlled identities in a JSON path.
    return f"""(SELECT reason.value FROM meta wr,
        json_each(CASE WHEN json_valid(wr.value) THEN wr.value ELSE '{{}}' END,'$.kept') reason
        WHERE wr.key='{MIGRATION_META_KEY}' AND reason.key=({expression}) AND reason.type='text')"""


def _board_aliases(state):
    if not state:
        return {}
    board = Path(state).resolve() / "board.sqlite3"
    if not board.is_file():
        return {}
    with closing(sqlite3.connect(board.as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
        return load_alias_map(connection)


def active_aliases() -> dict[str, str]:
    stack = getattr(_LOCAL, "stack", None)
    if stack:
        return stack[-1]
    # Native roles have no coordinator context. They already receive the exact
    # state root; never infer a board from cwd or fall back to the daily home.
    return _board_aliases(os.environ.get("BUDDY_STATE_DIR"))


@contextmanager
def aliases(mapping):
    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = _LOCAL.stack = []
    stack.append(dict(mapping or {}))
    try:
        yield
    finally:
        stack.pop()


@contextmanager
def board_aliases(state):
    if getattr(_LOCAL, "stack", None):
        yield
    else:
        with aliases(_board_aliases(state)):
            yield


def matches(recorded, actual, *, mapping=None):
    return recorded == actual or (active_aliases() if mapping is None else mapping).get(recorded) == actual


def canonical(recorded, *, mapping=None):
    return (active_aliases() if mapping is None else mapping).get(recorded, recorded)


def equivalent(recorded, other, *, mapping=None):
    if recorded == other:
        return True
    view = active_aliases() if mapping is None else mapping
    return view.get(recorded, recorded) == view.get(other, other)


def identity_variants(recorded, *, mapping=None):
    view = active_aliases() if mapping is None else mapping
    stable = view.get(recorded, recorded)
    return {recorded, stable} | {old for old, new in view.items() if new == stable}
