"""Bounded native observations shared by the Claude and ZCode adapters.

Only raw material is produced here. A native counter is accepted as an
observation or dropped; it is never estimated, and the canonical attempt-scoped
projection itself stays in :mod:`buddy.usage`. The retained assistant text keeps
the full text's byte count and SHA-256 even when only its head is stored, so the
Host can tell a bounded retention from the original output.
"""
from __future__ import annotations

import hashlib

from ..usage import MAX_ASSISTANT_MESSAGE_BYTES, MAX_COUNT

__all__ = ["MAX_ASSISTANT_MESSAGE_BYTES", "bound_native_text", "native_counter"]


def native_counter(value: object) -> int | None:
    """One non-negative native integer counter, or ``None`` when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        return None
    return value


def bound_native_text(text: object, *, source_id: object = None) -> dict | None:
    """One native root assistant text as bounded retention evidence.

    The returned document carries the full text's ``sourceBytes`` and ``sha256``
    plus a head-truncated ``text`` at :data:`buddy.usage.MAX_ASSISTANT_MESSAGE_BYTES`,
    exactly the shape :func:`buddy.usage.normalize_last_assistant_message`
    validates. Blank, non-text or NUL-bearing values are not a fact and return
    ``None``; nothing is substituted for them.
    """
    if not isinstance(text, str) or not text.strip() or "\0" in text:
        return None
    raw = text.encode()
    kept = text
    truncated = False
    if len(raw) > MAX_ASSISTANT_MESSAGE_BYTES:
        candidate = raw[:MAX_ASSISTANT_MESSAGE_BYTES]
        while candidate:
            try:
                kept = candidate.decode()
                break
            except UnicodeDecodeError:
                candidate = candidate[:-1]
        else:
            kept = ""
        truncated = True
    document = {"text": kept, "sourceBytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "truncated": truncated}
    if isinstance(source_id, str) and source_id.strip():
        document["sourceId"] = source_id.strip()[:256]
    return document
