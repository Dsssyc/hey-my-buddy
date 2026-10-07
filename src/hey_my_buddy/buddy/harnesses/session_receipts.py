"""Receipt, byte-budget and shared-log primitives of one governed session.

The Worker role's session tools and the native drivers that verify what those
tools returned share exactly this module and nothing else (ADR-025 decision 7's
seam): the role decides what a receipt, a refusal or a pending inquiry means,
the native side independently verifies the signature, the attempt binding and
the root evidence, and both use one HMAC signing rule, one canonical JSON
encoder, one set of wire byte budgets and one shared-locked journal read. There
is deliberately no second canonical encoder, decoder, or journal reader here —
``json_codec`` and ``locking`` stay the single implementations. Since the
ADR-025 step-5 cleanup the finish-receipt, tool-refusal and inquiry-receipt
verification of the native drivers also lives only here: both protocol modules
run exactly the functions below and merely convert :class:`ReceiptError` into
their own failure type at their seam. The drivers' few genuinely different
judgments are carried as registered :class:`ReceiptRules` values the callers
pass and the shared core reads — never a harness-name branch and never a
second implementation.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Callable, NamedTuple

from ... import locking
from ...json_codec import canonical_json, decode_strict_json

#: The byte bound of one governed outcome document, shared by the Worker role's
#: six-field validator, the MCP carrier's input frames and the refusal budgets
#: below. It lives here — the common receipt primitive — so both sides take it
#: from one place and the roles do not own a byte budget the native verification
#: also enforces.
MAX_OUTCOME_BYTES = 65536

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


# -- the shared session-receipt verification --------------------------------------


#: Wire bound of one signed finish receipt. The role mints at
#: ``MAX_OUTCOME_BYTES``; the verifier keeps the same bounded headroom over the
#: raw payload that the refusal budget below carries, and the bound is a frame
#: limit, never a trimming instruction.
MAX_FINISH_RECEIPT_BYTES = 70000


class ReceiptError(Exception):
    """One failed shared receipt verification: a run reason code and bounded message.

    The native protocol modules keep their own ``NativeError`` types for their
    run loops, so each converts this failure at its own seam; the code and the
    message cross unchanged, and no judgment is duplicated.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ReceiptRules(NamedTuple):
    """The registered judgment differences of the native drivers' receipt rules.

    The two drivers verify with one shared implementation, but they genuinely
    registered different judgments on three points, so each driver's seam passes
    its own value of this bundle and every field is read by the core: whether a
    refusal ``detail`` is bounded beyond its type (non-blank,
    ``MAX_TOOL_REFUSAL_DETAIL_BYTES``, no NUL), which message the
    reason/receiptId/detail stage raises, whether a non-string native tool name
    is refused at the envelope stage or falls through to the tool-binding stage,
    and whether an inquiry id is judged after stripping whitespace. The bundles
    are data, not branches on a harness name. ZCode registers
    :data:`BOUNDED_REFUSAL_DETAIL`, DSH registers :data:`TYPED_REFUSAL_DETAIL`.
    """

    detail_bounded: bool
    refusal_record_error: str
    refuse_unusable_native_tool: bool
    strip_inquiry_ids: bool


#: The rule with a bounded refusal detail: the detail must be non-blank, within
#: its byte budget and NUL-free, and the record stage names the bound it failed;
#: a non-string native tool name is trusted to fail at the tool-binding stage
#: and inquiry ids are judged by their byte content.
BOUNDED_REFUSAL_DETAIL = ReceiptRules(
    detail_bounded=True,
    refusal_record_error="the tool refusal envelope failed its bounded reason validation",
    refuse_unusable_native_tool=False,
    strip_inquiry_ids=False,
)

#: The rule with a type-checked refusal detail: only ``isinstance`` is judged and
#: the record stage names the unusable record; a non-string native tool name is
#: refused at the envelope stage and inquiry ids are judged after stripping.
TYPED_REFUSAL_DETAIL = ReceiptRules(
    detail_bounded=False,
    refusal_record_error="the tool refusal envelope carries an unusable refusal record",
    refuse_unusable_native_tool=True,
    strip_inquiry_ids=True,
)


def verify_receipt(raw: object, configuration: dict,
                   validate_outcome: Callable[[object], str | None]) -> dict:
    """Verify the signed finish receipt; malformed and forged failures stay distinct.

    Every stage keeps the fatal ``invalid-finish`` code — only a fully verified
    receipt is a success — but the bounded message distinguishes an unusable
    payload (no bounded JSON, wrong object shape) from a signature, attempt-
    identity or outcome failure, without ever quoting the raw content. The
    six-field outcome rule itself belongs to the Worker role: the current
    caller injects its narrow validator, so this verification checks the
    receipt with it instead of re-deciding what a legal outcome is, and a
    role-signed receipt still never skips the signature, binding or order
    checks here.
    """
    if not isinstance(raw, str) or len(raw.encode()) > MAX_FINISH_RECEIPT_BYTES:
        raise ReceiptError("invalid-finish", "the finish tool returned no bounded JSON receipt")
    try:
        receipt = decode_strict_json(raw)
        if not isinstance(receipt, dict) or set(receipt) != {"version", "identity", "inputSha256", "outcome", "receiptId", "signature"}:
            raise ReceiptError("invalid-finish", "the finish tool receipt was not the current signed receipt object")
        signature = receipt.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(receipt, configuration["key"])):
            raise ReceiptError("invalid-finish", "the finish tool receipt failed its signature verification")
        if receipt["version"] != 1 or receipt["identity"] != configuration["identity"] or receipt["inputSha256"] != configuration["inputSha256"]:
            raise ReceiptError("invalid-finish", "the finish tool receipt failed its attempt-identity binding")
        if not isinstance(receipt["receiptId"], str) or len(receipt["receiptId"]) != 32 or validate_outcome(receipt["outcome"]):
            raise ReceiptError("invalid-finish", "the finish tool receipt failed its outcome validation")
        return receipt
    except ReceiptError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ReceiptError("invalid-finish", "the finish tool receipt was malformed") from None


def _refusal_payload(raw: object) -> dict | None:
    """The candidate refusal object, tolerating only the wrapper's bounded framing.

    The installed native wrappers deliver an MCP ``isError`` text as a tool
    result prefixed with one bounded plain header line. This extracts the JSON
    candidate starting at the first ``{`` — no prose is interpreted, no
    substring is searched — and accepts it only when it decodes to a complete
    object (nothing but whitespace may follow) that explicitly claims the
    refusal format. Whether that candidate is genuine is decided solely by
    :func:`verify_tool_refusal`'s signature and binding checks.
    """
    if not isinstance(raw, str) or len(raw.encode()) > MAX_TOOL_REFUSAL_BYTES:
        return None
    text = raw.lstrip()
    start = text.find("{")
    if start < 0 or len(text[:start].encode()) > MAX_TOOL_REFUSAL_PREFIX_BYTES:
        return None
    try:
        value = decode_strict_json(text[start:])
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and value.get("kind") == "tool-refusal" else None


def refusal_shaped(raw: object) -> bool:
    """True only when ``raw`` carries an object explicitly claiming the refusal format.

    This is dispatch between the two signed formats our own MCP emits, never
    prose recognition: a refusal-shaped payload still has to pass
    :func:`verify_tool_refusal` before anything recovers, and anything else
    keeps flowing to the (fatal) receipt verification.
    """
    return _refusal_payload(raw) is not None


def verify_tool_refusal(raw: object, configuration: dict, native_tool: str | None,
                        mounted_tools: tuple[str, ...] | list[str], *, rules: ReceiptRules) -> dict:
    """Verify one signed tool-refusal envelope against this attempt, input and tool.

    The session tools mint these for expected argument, attention and inquiry
    refusals because the native wrappers surface an MCP ``isError`` response as
    a *successful* tool result framed with a plain header; the signature binds
    the attempt identity, the turn input and the exact session tool, so a
    tampered, cross-attempt or cross-tool envelope fails here fatally instead
    of becoming a recoverable refusal. The tool binding is checked against the
    session tools this run actually mounted — never a fixed global set — so a
    refusal verifies only for the service the driver really bound. A verified
    refusal only ever means "correct the call and retry in this same native
    turn"; it can never carry an outcome or mutate state. The ``rules`` bundle
    carries the caller's registered differences: whether a non-string native
    tool name fails at this envelope stage or at the tool-binding stage below,
    and whether the refusal record stage bounds the detail beyond its type —
    with each stage's message kept exactly as the registering driver raised it.
    """
    tool = native_tool.rsplit("__", 1)[-1] if isinstance(native_tool, str) else None
    envelope = _refusal_payload(raw)
    if envelope is None or (rules.refuse_unusable_native_tool and tool is None):
        raise ReceiptError("invalid-tool-refusal", "the session tool returned no bounded JSON refusal envelope")
    try:
        if set(envelope) != {"version", "kind", "identity", "inputSha256",
                             "tool", "reason", "detail", "receiptId", "signature"}:
            raise ReceiptError("invalid-tool-refusal", "the tool refusal envelope was not the current signed refusal object")
        signature = envelope.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(envelope, configuration["key"])):
            raise ReceiptError("invalid-tool-refusal", "the tool refusal envelope failed its signature verification")
        if (envelope["version"] != 1 or envelope["kind"] != "tool-refusal"
                or envelope["identity"] != configuration["identity"]
                or envelope["inputSha256"] != configuration["inputSha256"]):
            raise ReceiptError("invalid-tool-refusal", "the tool refusal envelope failed its attempt-identity binding")
        if (not isinstance(envelope["tool"], str) or envelope["tool"] not in tuple(mounted_tools)
                or envelope["tool"] != tool or not native_tool.endswith("__" + envelope["tool"])):
            raise ReceiptError("invalid-tool-refusal", "the tool refusal envelope was signed for a different session tool")
        detail_usable = isinstance(envelope["detail"], str) and (
            not rules.detail_bounded or (bool(envelope["detail"].strip())
                                         and len(envelope["detail"].encode()) <= MAX_TOOL_REFUSAL_DETAIL_BYTES
                                         and "\0" not in envelope["detail"]))
        if (envelope["reason"] not in TOOL_REFUSAL_REASONS
                or not isinstance(envelope["receiptId"], str) or len(envelope["receiptId"]) != 32
                or not detail_usable):
            raise ReceiptError("invalid-tool-refusal", rules.refusal_record_error)
        return envelope
    except ReceiptError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ReceiptError("invalid-tool-refusal", "the tool refusal envelope was malformed") from None


def _valid_inquiry_id(value: object, rules: ReceiptRules) -> bool:
    """A bounded inquiry identifier under the caller's registered blank rule.

    Both rules refuse an over-bound id; the registered difference is the
    emptiness judgment — byte content (:data:`BOUNDED_REFUSAL_DETAIL`) or
    stripped content (:data:`TYPED_REFUSAL_DETAIL`, which refuses whitespace-only
    ids).
    """
    if not isinstance(value, str) or len(value.encode()) > MAX_INQUIRY_ID_BYTES:
        return False
    return bool(value.strip()) if rules.strip_inquiry_ids else len(value.encode()) > 0


def _valid_question_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def verify_inquiry_receipt(raw: object, configuration: dict, kind: str, *, rules: ReceiptRules) -> dict:
    """Verify one signed checkpoint or answer receipt from the session tools.

    The session tool only ever returns a tentative signed receipt; this is the
    controller-side authority check. The signature binds the attempt identity
    and the exact payload, so a tampered, stale or cross-attempt receipt fails
    here before any bridge state changes. ``rules`` carries the caller's
    registered inquiry-id blank rule; every other stage is the shared judgment.
    """
    if kind not in ("inquiry-checkpoint", "inquiry-answer"):
        raise ValueError("unknown inquiry receipt kind")
    fields = ({"version", "kind", "identity", "inquiries", "receiptId", "signature"} if kind == "inquiry-checkpoint"
              else {"version", "kind", "identity", "inquiryId", "questionSha256", "answer", "receiptId", "signature"})
    #: A checkpoint receipt may name how many queued questions did not fit its
    #: serialized batch budget, so explicit batching never silently hides them.
    optional = {"morePending"} if kind == "inquiry-checkpoint" else set()
    if not isinstance(raw, str) or len(raw.encode()) > MAX_INQUIRY_RECEIPT_BYTES:
        raise ReceiptError("invalid-inquiry-receipt", f"the {kind} tool returned no bounded JSON receipt")
    try:
        receipt = decode_strict_json(raw)
        if (not isinstance(receipt, dict) or set(receipt) - optional != fields or not fields <= set(receipt)
                or ("morePending" in receipt
                    and (type(receipt["morePending"]) is not int or not 0 <= receipt["morePending"] <= MAX_INQUIRIES))):
            raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt was not the current signed receipt object")
        signature = receipt.pop("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_receipt(receipt, configuration["key"])):
            raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt failed its signature verification")
        if receipt["version"] != 1 or receipt["kind"] != kind or receipt["identity"] != configuration["identity"]:
            raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt failed its attempt-identity binding")
        if not isinstance(receipt["receiptId"], str) or len(receipt["receiptId"]) != 32:
            raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt has no usable receipt identity")
        if kind == "inquiry-answer":
            if (not _valid_inquiry_id(receipt["inquiryId"], rules) or not _valid_question_sha(receipt["questionSha256"])
                    or not isinstance(receipt["answer"], str) or not receipt["answer"].strip()
                    or len(receipt["answer"].encode()) > MAX_ANSWER_BYTES):
                raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt failed its answer binding")
        else:
            inquiries = receipt["inquiries"]
            if not isinstance(inquiries, list) or len(inquiries) > MAX_INQUIRIES:
                raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt failed its inquiry-list binding")
            for item in inquiries:
                if (not isinstance(item, dict)
                        or set(item) - {"inquiryId", "question", "questionSha256", "state", "askedAt", "deliveredAt"}
                        or not {"inquiryId", "question", "questionSha256", "state", "askedAt"} <= set(item)
                        or not _valid_inquiry_id(item["inquiryId"], rules) or not _valid_question_sha(item["questionSha256"])
                        or item["state"] not in ("queued", "delivered")
                        or not isinstance(item["question"], str) or not item["question"].strip()
                        or len(item["question"].encode()) > MAX_QUESTION_BYTES
                        or not isinstance(item["askedAt"], str)
                        or not isinstance(item.get("deliveredAt", ""), str)):
                    raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt failed its inquiry-entry binding")
        return receipt
    except ReceiptError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ReceiptError("invalid-inquiry-receipt", f"the {kind} receipt was malformed") from None
