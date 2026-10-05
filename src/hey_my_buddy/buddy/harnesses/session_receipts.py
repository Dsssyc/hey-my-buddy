"""Receipt, byte-budget and shared-log primitives of one governed session.

The Worker role's session tools and the native drivers that verify what those
tools returned share exactly this module and nothing else (ADR-025 decision 7's
seam): the role decides what a receipt, a refusal or a pending inquiry means,
the native side independently verifies the signature, the attempt binding and
the root evidence, and both use one HMAC signing rule, one canonical JSON
encoder, one set of wire byte budgets and one shared-locked journal read. There
is deliberately no second canonical encoder, decoder, or journal reader here —
``json_codec`` and ``locking`` stay the single implementations.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from ... import locking
from ...json_codec import canonical_json
from ..roles.turn_io import MAX_OUTCOME_BYTES

#: Shared inquiry bounds, identical to the board and the DSH bridge. The role's
#: session tools, the native verification and the observation bridge all enforce
#: the same budget so a bounded value can never be rejected after mutation.
MAX_QUESTION_BYTES = 4000
MAX_ANSWER_BYTES = 4000
MAX_INQUIRIES = 32
MAX_INQUIRY_ID_BYTES = 128

#: Byte cap of the inquiry journal one attempt replays; the controller writer
#: and every session-tool reader agree on this bound.
MAX_JOURNAL_BYTES = 1024 * 1024

#: The only inquiry-journal record format the writers write and the readers
#: replay. Every reader requires exactly this version and the record's attempt
#: identity, so a malformed, foreign or unbound line is ignored instead of
#: merged.
INQUIRY_JOURNAL_VERSION = 1

#: Honest bounds of the signed inquiry receipts the session tools return. A
#: checkpoint receipt may carry every queued question, so its budget covers
#: ``MAX_INQUIRIES`` x ``MAX_QUESTION_BYTES`` plus framing.
MAX_INQUIRY_RECEIPT_BYTES = 200 * 1024

#: The closed set of reasons a signed tool-refusal envelope may carry. Every
#: member is an expected, bounded refusal the role's tools mint for a
#: correctable caller mistake or an outstanding Host condition; anything else is
#: not a refusal a native verifier will recover from.
TOOL_REFUSAL_REASONS = ("invalid-arguments", "attention-outstanding", "inquiry-pending",
                        "unknown-inquiry", "inquiry-state", "inquiry-channel-absent")

#: A refusal detail is the same bounded correction text a plain tool error
#: carried (validator output, the pending-inquiry refusal, the attention note),
#: so it inherits the outcome byte bound; the envelope adds only fixed framing.
MAX_TOOL_REFUSAL_DETAIL_BYTES = MAX_OUTCOME_BYTES
MAX_TOOL_REFUSAL_BYTES = MAX_OUTCOME_BYTES + 4096

#: The installed native wrapper frames an MCP ``isError`` text with one bounded
#: plain header line (observed: ``MCP tool returned an error:``) before the
#: content. The verifier tolerates exactly that much transport framing when
#: dispatching to refusal verification; the framing never enters the signature,
#: and only the full HMAC over the attempt, input and tool binding decides.
MAX_TOOL_REFUSAL_PREFIX_BYTES = 256


def sign_receipt(payload: dict, key: str) -> str:
    return hmac.new(bytes.fromhex(key), canonical_json(payload).encode(), hashlib.sha256).hexdigest()


def serialized_footprint(text: str) -> int:
    """The byte length ``text`` occupies inside a canonical JSON document.

    The raw UTF-8 length cannot bound a signed envelope's wire size: JSON
    escaping costs six bytes per C0 control character (``\\u0001``), two per
    quote or backslash, while non-ASCII text stays literal under
    ``ensure_ascii=False``. Every budget that has to survive signing plus the
    native wrapper's framing is computed on this footprint, never the raw
    length.
    """
    return len(canonical_json(text).encode()) - 2


def read_shared_snapshot(path, max_bytes: int) -> bytes | None:
    """One bounded, shared-locked read of the inquiry journal file.

    This is the reader side of the journal's cross-process barrier (POSIX flock,
    the same primitive the service uses for its lifetime locks): while the
    controller writer holds the exclusive side through its append/fsync/commit
    transaction, this read waits, so a reader can never observe a record that
    the writer has not fully committed — neither an in-flight append nor the
    remains of a failed one. Returns ``None`` when the file cannot be opened and
    ``b""`` when it exceeds its byte bound.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        locking.lock(fd, shared=True)
        if os.fstat(fd).st_size > max_bytes:
            return b""
        chunks = bytearray()
        while len(chunks) <= max_bytes:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.extend(block)
        return bytes(chunks[:max_bytes + 1])
    finally:
        os.close(fd)  # closing releases the shared lock
