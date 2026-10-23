"""One attempt-bound, shared-locked inquiry journal reader for both processes."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .. import locking
from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json
from ..private_dirs import open_regular_fd
from .run_identity import RunIdentity

#: Byte cap of the inquiry journal one attempt replays; the controller writer
#: and every session-tool reader agree on this bound.
MAX_JOURNAL_BYTES = 1024 * 1024

#: The only inquiry-journal record format the writers write and the readers
#: replay. Every reader requires exactly this version and the record's attempt
#: identity, so a malformed, foreign or unbound line is ignored instead of
#: merged.
INQUIRY_JOURNAL_VERSION = 1


def read_shared_snapshot(path, max_bytes: int) -> bytes | None:
    """One bounded, shared-locked read of the inquiry journal file.

    This is the reader side of the journal's cross-process barrier (POSIX flock,
    the same primitive the service uses for its lifetime locks): while the
    controller writer holds the exclusive side through its append/fsync/commit
    transaction, this read waits, so a reader can never observe a record that
    the writer has not fully committed — neither an in-flight append nor the
    remains of a failed one. A missing file raises ``FileNotFoundError``;
    other open failures return ``None``. Reads at most ``max_bytes + 1``
    bytes to distinguish empty from over-limit snapshots.
    """
    try:
        fd = open_regular_fd(Path(path), os.O_RDONLY)
    except FileNotFoundError:
        raise
    except (OSError, BoardError, ValueError):
        return None
    try:
        locking.lock(fd, shared=True)
        chunks = bytearray()
        while len(chunks) <= max_bytes:
            block = os.read(fd, min(65536, max_bytes + 1 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        return bytes(chunks[:max_bytes + 1])
    except OSError:
        return None
    finally:
        os.close(fd)  # closing releases the shared lock


def _journal_rejection_reason(record: dict, bound: dict) -> str:
    for key, reason in (("taskId", "journal-task-mismatch"),
                        ("attemptId", "journal-attempt-mismatch")):
        value = record.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or value != bound[key]:
            return reason
    return "journal-attempt-unbound"


def read_inquiry_journal(identity: RunIdentity | dict, journal_path: str | Path | None) -> dict:
    """The journal as one availability fact over the shared-locked snapshot read.

    An absent path or file is not written; an open or read failure is
    unreadable. An ordinary file is read under the writer's lock up to the
    byte cap plus one. ``entries`` counts deduplicated well-formed questions,
    including rejected ones.

    Each question's fact follows its final effective record: a latest legal
    record clears the id's older rejection and contributes its bound
    records (their source continuity intact), a latest foreign record
    contributes exactly one rejection with its own reason — never an
    accumulation of every historical refusal, and never an assumption that
    the last write was legal.
    """
    if not journal_path:
        return {"available": False, "reason": "journal-not-written", "entries": 0,
                "records": [], "rejections": []}
    try:
        raw = read_shared_snapshot(journal_path, MAX_JOURNAL_BYTES)
    except FileNotFoundError:
        return {"available": False, "reason": "journal-not-written", "entries": 0,
                "records": [], "rejections": []}
    if raw is None:
        return {"available": False, "reason": "journal-unreadable", "entries": 0,
                "records": [], "rejections": []}
    if len(raw) > MAX_JOURNAL_BYTES:
        return {"available": False, "reason": "journal-exceeds-limit", "entries": 0,
                "records": [], "rejections": []}
    bound = (identity if isinstance(identity, dict) else
             {"taskId": identity.task_id, "attemptId": identity.attempt_id,
              "generation": identity.generation, "turnId": identity.turn_id})
    bound_records: dict[str, list[dict]] = {}
    rejection: dict[str, str] = {}
    anchors: dict[str, dict] = {}
    for raw_line in raw.splitlines():
        try:
            line = raw_line.decode('utf-8').strip()
            if not line:
                continue
            record = decode_strict_json(line)
            # Escaped unpaired surrogates can survive JSON decoding even when
            # the input bytes are UTF-8. Such a value was never a writable
            # journal record and must not reach SQLite or an answer projection.
            canonical_json(record).encode('utf-8')
        except (ValueError, RecursionError):
            continue  # a torn line is ignored, never fatal
        if not isinstance(record, dict):
            continue
        question_id = record.get("inquiryId")
        if not isinstance(question_id, str) or not question_id:
            continue
        if type(record.get("version")) is not int or record["version"] != INQUIRY_JOURNAL_VERSION or any(
                key not in record or type(record[key]) is not type(bound[key]) or record[key] != bound[key]
                for key in ("taskId", "attemptId", "generation", "turnId")):
            rejection[question_id] = _journal_rejection_reason(record, bound)
            continue
        previous = anchors.get(question_id, {})
        question = record.get("question", previous.get("question"))
        digest = record.get("questionSha256", previous.get("questionSha256"))
        try:
            question_digest = hashlib.sha256(question.encode("utf-8")).hexdigest() if isinstance(question, str) else None
        except UnicodeEncodeError:
            rejection[question_id] = "journal-record-invalid"
            continue
        if ((isinstance(question, str) and question_digest != digest)
                or (previous.get("questionSha256") is not None and digest != previous["questionSha256"])):
            rejection[question_id] = "journal-record-invalid"
            continue
        # This legal record is the id's latest fact: it clears any older
        # rejection and keeps the id's valid-record source continuity.
        bound_records.setdefault(question_id, []).append(record)
        anchors[question_id] = {**previous, **record}
        rejection.pop(question_id, None)
    # The final effective record per id decides: an id whose latest record was foreign
    # contributes its one rejection and none of its earlier records.
    records = [record for question_id, question_records in bound_records.items()
               if question_id not in rejection
               for record in question_records]
    rejections = [{"questionId": question_id, "reason": rejection[question_id]}
                  for question_id in rejection]
    return {"available": True, "reason": None,
            "entries": len(bound_records.keys() | rejection.keys()),
            "records": records, "rejections": rejections}



def merged_inquiry_records(records: list[dict]) -> dict[str, dict]:
    """Keep each question's committed hash, delivery and answer across appends."""
    entries: dict[str, dict] = {}
    for record in records:
        inquiry_id = record["inquiryId"]
        entries[inquiry_id] = {**entries.get(inquiry_id, {}), **record}
    return entries


def normalize_inquiry_record(record: dict, *, was_delivered: bool = False) -> dict | None:
    """Validate one journal/live fact and normalize it for the message writer."""
    from . import schemas
    from .reasons import reason

    state = record.get("state")
    if state not in ("queued", "claimed", "delivered", "answered", "discarded", "unavailable"):
        return None
    patch: dict = {"state": state}
    delivery = dict(record["delivery"]) if isinstance(record.get("delivery"), dict) else {}
    for key in ("deliveredAt", "via", "toolCallId", "nativeCorrelation"):
        if record.get(key) is not None:
            delivery[key] = record[key]
    if delivery:
        patch["delivery"] = delivery
    answer = record.get("answer")
    if isinstance(answer, dict) and isinstance(answer.get("text"), str) and answer["text"].strip():
        try:
            size = len(answer["text"].encode("utf-8"))
        except UnicodeEncodeError:
            return None
        if size > schemas.MAX_ANSWER_BYTES:
            return None
        patch["answer"] = {
            "text": answer["text"], "bytes": size,
            "via": answer.get("via") if isinstance(answer.get("via"), str) else None,
            "toolCallId": answer.get("toolCallId") if isinstance(answer.get("toolCallId"), str) else None,
            "at": answer.get("at") if isinstance(answer.get("at"), str) else None,
            "truncated": answer.get("truncated") is True,
            "source": "bridge-journal",
        }
    if state == "answered" and "answer" not in patch:
        patch["state"] = "delivered"
        patch["reason"] = reason("inquiry-answer-empty")
    elif state == "unavailable":
        if was_delivered or delivery.get("deliveredAt"):
            patch["reason"] = reason("inquiry-delivered-at-attempt-end")
        else:
            patch["reason"] = reason("inquiry-queued-at-attempt-end")
    return patch
