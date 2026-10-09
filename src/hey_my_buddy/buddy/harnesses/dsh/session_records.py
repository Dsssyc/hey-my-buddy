"""The one reader of DSH's private session records, whole or resumed.

The optional ``session.v3`` rollout a DSH process writes under the pinned
sessions root is this harness's only source of the observed model identity,
the per-step usage and the retained root assistant text. Everything here is
bounded and streaming — the project's pinned ``zstandard`` library for the
compressed variant, proportionate total/line/step caps, a bound stop marks the
read truncated instead of pretending the file ended — and every failure mode
keeps the known facts standing: a missing, foreign, malformed or truncated
record is an unknown or partial fact, never a zero and never a run failure.

A resumed turn cannot read the rollout the way a fresh session does: the file
already carries the previous turns' records, and their usage, assistant text,
model identity and turn ends are not this turn's facts. The reader therefore
freezes the target session's existing record lines — order and content, as
per-line digests — after the native resume answer and before the prompt, and
after the turn it projects only the provably appended segment: the frozen
prefix must still stand there byte-for-byte, the matching file set must be
unchanged, and an appended line that repeats a frozen line is a replay, not a
new record. Anything else — a missing or rewritten file, a shortened prefix, a
duplicated line, an extra matching file, an unreliable freeze — leaves the
whole projection unknown; the boundary is never guessed by subtracting
cumulative counters.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path
from typing import Iterator

from .protocol import decode_json

#: The record file suffixes and header version the scan and the fold accept.
_RECORD_SUFFIXES = (".v3.jsonl.zstd", ".v3.jsonl")
_RECORD_VERSION = 3
_MAX_RECORD_FILES = 64
_MAX_RECORD_DEPTH = 4
#: Decompressed proportionate bounds: a record is read streaming, never wholly
#: into memory, and a bound stop is an honestly partial fact, never a guess.
_MAX_RECORD_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_RECORD_LINE_BYTES = 4 * 1024 * 1024
_MAX_RECORD_STEPS = 512
_RECORD_USAGE_FIELDS = ("inputTokens", "outputTokens", "totalTokens",
                        "cacheReadTokens", "cacheWriteTokens", "reasoningTokens")


class RecordStream:
    """Bounded streaming lines of one record file, compressed or plain.

    The ``.zstd`` variant streams through the project's pinned ``zstandard``
    library — never a system ``zstd`` command, never a whole-record buffer —
    and a missing library surfaces as the read fault it is. Every read stops
    at its proportionate bound; a bound stop marks the record truncated
    instead of pretending the file ended cleanly.
    """

    def __init__(self, path: Path):
        self.path = path
        self.truncated = False

    def lines(self) -> Iterator[bytes]:
        source = self._decompressed if self.path.name.endswith(".zstd") else self._plain
        pending = b""
        total = 0
        try:
            for chunk in source():
                total += len(chunk)
                if total > _MAX_RECORD_TOTAL_BYTES:
                    self.truncated = True
                    return
                pending += chunk
                while True:
                    index = pending.find(b"\n")
                    if index < 0:
                        break
                    line, pending = pending[:index], pending[index + 1:]
                    if len(line) > _MAX_RECORD_LINE_BYTES:
                        self.truncated = True
                        continue
                    yield line
                if len(pending) > _MAX_RECORD_LINE_BYTES:
                    self.truncated = True
                    pending = b""
            if pending.strip():
                yield pending
        except Exception:  # noqa: BLE001 - an unreadable record is a partial fact, never a run failure
            self.truncated = True

    def _plain(self) -> Iterator[bytes]:
        with self.path.open("rb") as handle:
            while chunk := handle.read(262144):
                yield chunk

    def _decompressed(self) -> Iterator[bytes]:
        import zstandard
        with self.path.open("rb") as handle:
            reader = zstandard.ZstdDecompressor().stream_reader(handle)
            while chunk := reader.read(262144):
                yield chunk


class RecordAccumulator:
    """The fold of this projection's matched session records.

    The projection rules are the existing usage observer's, applied to the
    private record instead of the in-process events: per-field sums over
    non-negative safe integers, a field a contributing record lacked is
    omitted instead of read as zero, cached input derives from the record's
    own counters, and only a seen, completed turn end with no missing usage
    and an untruncated read is complete. Everything else stays partial.
    """

    def __init__(self) -> None:
        self.sums: dict[str, int] = {field: 0 for field in _RECORD_USAGE_FIELDS}
        self.missed: set[str] = set()
        self.records = 0
        self.missing_usage = False
        self.turn_end_seen = False
        self.turn_end_completed = False
        self.truncated = False
        self.failure: dict | None = None
        self.last_assistant: str | None = None
        self.last_assistant_id: str | None = None
        self.model: dict | None = None
        self.steps: list[dict] = []
        self.sessions: list[str] = []

    def complete_field(self, field: str) -> bool:
        return self.records > 0 and field not in self.missed

    def fold_usage(self, usage: object, step: dict) -> None:
        usable = False
        if isinstance(usage, dict):
            entry = dict(step)
            for field in _RECORD_USAGE_FIELDS:
                value = usage.get(field)
                if type(value) is int and value >= 0:
                    self.sums[field] += value
                    entry[field] = value
                    usable = True
                else:
                    self.missed.add(field)
            if usable:
                self.records += 1
                if len(self.steps) < _MAX_RECORD_STEPS:
                    self.steps.append(entry)
                return
        self.missing_usage = True

    def cached_input(self) -> int | None:
        if all(self.complete_field(field) for field in
               ("totalTokens", "inputTokens", "outputTokens")):
            derived = self.sums["totalTokens"] - self.sums["inputTokens"] - self.sums["outputTokens"]
            if derived >= 0:
                return derived
        if self.complete_field("cacheReadTokens") and self.complete_field("cacheWriteTokens"):
            return self.sums["cacheReadTokens"] + self.sums["cacheWriteTokens"]
        return None

    def usage(self) -> dict | None:
        if not self.records:
            return None
        complete = (not self.missing_usage and self.turn_end_seen
                    and self.turn_end_completed and not self.truncated)
        projected: dict = {"source": "dsh/session-record", "inputBasis": "excludes-cached",
                           "nativeRecords": self.records,
                           "completeness": "complete" if complete else "partial"}
        for field in ("inputTokens", "outputTokens"):
            if self.complete_field(field):
                projected[field] = self.sums[field]
        cached = self.cached_input()
        if cached is not None:
            projected["cachedInputTokens"] = cached
        if self.complete_field("reasoningTokens"):
            projected["reasoningOutputTokens"] = self.sums["reasoningTokens"]
        return projected


def record_candidates(root: Path) -> list[Path]:
    """The bounded, deterministic scan for record files under the sessions root."""
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > _MAX_RECORD_DEPTH or len(found) >= _MAX_RECORD_FILES:
            return
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            return
        for entry in ordered:
            if len(found) >= _MAX_RECORD_FILES:
                return
            try:
                if entry.is_file(follow_symlinks=False):
                    if entry.name.endswith(_RECORD_SUFFIXES):
                        found.append(Path(entry.path))
                elif entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), depth + 1)
            except OSError:
                continue

    walk(root, 0)
    return found


def _record_text(value: object) -> str:
    """The plain text blocks of a record message's content array, concatenated."""
    blocks = value if isinstance(value, list) else []
    return "".join(block["text"] for block in blocks
                   if isinstance(block, dict) and block.get("type") == "text"
                   and isinstance(block.get("text"), str))


def _fold_event(event: dict, session: str, acc: RecordAccumulator) -> None:
    """Fold one already-attributed record event line into the accumulator."""
    kind = event.get("type")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if kind == "assistant/message":
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        source = message.get("source") if isinstance(message.get("source"), dict) else {}
        acc.fold_usage(data.get("usage"), {
            "session": session,
            "turn": data.get("turn") if type(data.get("turn")) is int else None,
            "step": data.get("step") if type(data.get("step")) is int else None})
        if source.get("kind") == "model":
            text = _record_text(message.get("content"))
            if text.strip():
                acc.last_assistant = text
                acc.last_assistant_id = message.get("id") if isinstance(message.get("id"), str) else None
            if isinstance(source.get("provider"), str) and isinstance(source.get("model"), str):
                acc.model = {"provider": source["provider"], "model": source["model"]}
    elif kind == "turn/end":
        reason = data.get("reason") if isinstance(data.get("reason"), dict) else {}
        # The last turn end is this projection's terminal reason.
        acc.turn_end_seen = True
        acc.turn_end_completed = reason.get("kind") == "completed"
        if reason.get("kind") == "error":
            error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
            code = error.get("code")
            # Only the machine classification: a provider message can carry
            # credentials and is never copied anywhere.
            acc.failure = {"kind": "error",
                           "code": code if type(code) is str and 0 < len(code) <= 64 else None}


def fold_record(path: Path, session_ids: frozenset, acc: RecordAccumulator) -> bool:
    """Fold one record file in; return whether its header named one of the run's sessions.

    The header's own session id is the only attribution: a foreign session's
    record is skipped, never folded into this attempt's facts. A malformed
    event line marks the read partial and the fold continues with the rest.
    """
    stream = RecordStream(path)
    session = None
    for raw in stream.lines():
        try:
            event = decode_json(raw)
        except (ValueError, RecursionError):
            acc.truncated = True
            continue
        if not isinstance(event, dict):
            continue
        if session is None:
            if (event.get("type") != "session" or event.get("version") != _RECORD_VERSION
                    or not isinstance(event.get("id"), str)):
                continue
            if event["id"] not in session_ids:
                return False
            session = event["id"]
            acc.sessions.append(session)
            continue
        _fold_event(event, session, acc)
    if stream.truncated:
        acc.truncated = True
    return session is not None


def session_record_facts(sessions_root: Path, session_ids) -> dict | None:
    """This run's private session-record facts, or ``None`` when none matched.

    The record is the optional source of the observed model identity, the
    per-step usage and the retained root assistant text. Every failure mode —
    a missing directory, a missing decompression library, a foreign or
    malformed record, a bound stop — keeps the known facts standing, reports
    the partial marker, and never raises into the run; an absent record is an
    unknown, never a zero.
    """
    sessions = frozenset(s for s in session_ids if isinstance(s, str) and s)
    root = Path(sessions_root)
    if not sessions or not root.is_dir():
        return None
    acc = RecordAccumulator()
    try:
        candidates = record_candidates(root)
    except OSError:
        return None
    try:
        for path in candidates:
            fold_record(path, sessions, acc)
    except OSError:
        acc.truncated = True
    usage = acc.usage()
    if usage is None and acc.model is None and acc.last_assistant is None and acc.failure is None:
        return None
    return {"usage": usage, "model": acc.model, "lastAssistant": acc.last_assistant,
            "lastAssistantSourceId": acc.last_assistant_id,
            "failure": acc.failure, "steps": acc.steps, "sessions": acc.sessions,
            "recordsRead": len(candidates), "truncated": acc.truncated}


# -- the resumed-turn boundary -------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FrozenRecord:
    """One frozen record file: its path and the ordered digests of its lines."""

    path: str
    digests: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class RecordBaseline:
    """The pre-prompt freeze of one session's existing record files.

    ``reliable`` is false when the freeze read itself hit a bound or an
    unreadable file: the boundary could not be established, and the post-turn
    projection must stay unknown instead of guessing a prefix.
    """

    session_id: str
    files: tuple[FrozenRecord, ...]
    reliable: bool


def _matching_lines(path: Path, session_id: str) -> tuple[tuple[str, ...], bool]:
    """The ordered line digests of one matching record file, and its reliability.

    A file matches when its header names the target session. Reading stops at
    the header when the file belongs to another session, so a foreign record
    costs one header read, never a full scan: a confirmed foreign file is
    skipped whole and never makes the read unreliable, however its own lines
    continue. A file whose header cannot be read — empty, malformed, or a read
    that stopped before any header line — cannot be attributed to any session,
    so it keeps the boundary honestly unreliable instead of being assumed
    absent or foreign.
    """
    stream = RecordStream(path)
    digests: list[str] = []
    matched = False
    for raw in stream.lines():
        try:
            event = decode_json(raw)
        except (ValueError, RecursionError):
            digests.append(hashlib.sha256(raw).hexdigest())
            continue
        if not isinstance(event, dict):
            digests.append(hashlib.sha256(raw).hexdigest())
            continue
        if not matched:
            if (event.get("type") != "session" or event.get("version") != _RECORD_VERSION
                    or not isinstance(event.get("id"), str)):
                return (), False
            if event["id"] != session_id:
                return (), True
            matched = True
        digests.append(hashlib.sha256(raw).hexdigest())
    if not matched:
        return (), False
    return tuple(digests), not stream.truncated


def _matching_files(root: Path, session_id: str) -> tuple[list[tuple[Path, tuple[str, ...]]], bool]:
    """Every matching record file under the root, with an overall reliability fact."""
    try:
        candidates = record_candidates(root)
    except OSError:
        return [], False
    matched: list[tuple[Path, tuple[str, ...]]] = []
    reliable = True
    for path in candidates:
        try:
            digests, clean = _matching_lines(path, session_id)
        except OSError:
            reliable = False
            continue
        if not clean:
            reliable = False
        if digests:
            matched.append((path, digests))
    return matched, reliable


def freeze_session_records(sessions_root: Path, session_id: str) -> RecordBaseline:
    """Freeze the target session's existing record lines before the prompt.

    The freeze is taken after the native resume answer and before this turn's
    prompt, so resume-triggered loading has already settled and everything
    appended after it is this turn's. A missing root or a session with no
    record yet is a valid empty boundary; only an unreadable or bound-stopped
    read makes the baseline unreliable.
    """
    root = Path(sessions_root)
    if not root.is_dir():
        return RecordBaseline(session_id=session_id, files=(), reliable=True)
    matched, reliable = _matching_files(root, session_id)
    return RecordBaseline(
        session_id=session_id,
        files=tuple(FrozenRecord(path=str(path), digests=digests) for path, digests in matched),
        reliable=reliable)


def _unknown_resumed_facts(baseline: RecordBaseline, reason: str, records_read: int) -> dict:
    """The honest unknown projection: no usage, no old facts, and the reason."""
    return {"usage": None, "model": None, "lastAssistant": None, "lastAssistantSourceId": None,
            "failure": None, "steps": [], "sessions": [baseline.session_id],
            "recordsRead": records_read, "truncated": False,
            "resumeBoundary": {"proven": False, "reason": reason,
                               "baselineFiles": len(baseline.files),
                               "baselineLines": sum(len(frozen.digests) for frozen in baseline.files)}}


def resumed_session_record_facts(sessions_root: Path, baseline: RecordBaseline) -> dict | None:
    """This resumed turn's record facts: only the provably appended segment.

    The frozen prefix must still stand in the same file, unchanged and in
    order; the file set that matches the session must be exactly the frozen
    one; no appended line may repeat a frozen line. Only then are the
    appended lines folded — with the previous turns' usage, assistant text,
    model identity and turn ends left out of this turn's facts. Any other
    shape, and a baseline that could not be read reliably, keeps the whole
    projection unknown with its reason; nothing is ever derived by
    subtracting cumulative counters.
    """
    root = Path(sessions_root)
    if not root.is_dir():
        return _unknown_resumed_facts(baseline, "sessions-root-missing", 0)
    try:
        records_read = len(record_candidates(root))
    except OSError:
        records_read = 0
    matched, final_clean = _matching_files(root, baseline.session_id)
    if not baseline.reliable:
        return _unknown_resumed_facts(baseline, "baseline-unreadable", records_read)
    frozen_paths = [frozen.path for frozen in baseline.files]
    if sorted(str(path) for path, _ in matched) != sorted(frozen_paths):
        return _unknown_resumed_facts(
            baseline, "matching-record-set-changed", records_read)
    acc = RecordAccumulator()
    acc.sessions.append(baseline.session_id)
    frozen_by_path = {frozen.path: frozen for frozen in baseline.files}
    frozen_digests = frozenset(digest for frozen in baseline.files for digest in frozen.digests)
    baseline_lines = 0
    appended = 0
    for path, digests in matched:
        frozen = frozen_by_path[str(path)]
        baseline_lines += len(frozen.digests)
        if len(digests) < len(frozen.digests):
            # A read that stopped inside the frozen prefix is an unreadable
            # boundary; a clean file that no longer carries the prefix was
            # genuinely shortened. Neither is ever read as a partial guess.
            return _unknown_resumed_facts(
                baseline, "final-read-unreadable" if not final_clean else "record-shortened",
                records_read)
        for index, digest in enumerate(frozen.digests):
            if digests[index] != digest:
                return _unknown_resumed_facts(baseline, "frozen-prefix-rewritten", records_read)
        new_digests = digests[len(frozen.digests):]
        if any(digest in frozen_digests for digest in new_digests):
            return _unknown_resumed_facts(baseline, "appended-line-replays-frozen-record",
                                          records_read)
        appended += len(new_digests)
    # The boundary is proven: fold only the appended lines of each file, as
    # raw events — the header is already established by the match above.
    for path, digests in matched:
        frozen = frozen_by_path[str(path)]
        stream = RecordStream(path)
        index = 0
        for raw in stream.lines():
            if index >= len(frozen.digests):
                try:
                    event = decode_json(raw)
                except (ValueError, RecursionError):
                    acc.truncated = True
                    continue
                if isinstance(event, dict):
                    _fold_event(event, baseline.session_id, acc)
            index += 1
        if stream.truncated:
            acc.truncated = True
    usage = acc.usage()
    if usage is None and acc.model is None and acc.last_assistant is None and acc.failure is None:
        facts = _unknown_resumed_facts(baseline, "no-appendable-records", records_read)
    else:
        facts = {"usage": usage, "model": acc.model, "lastAssistant": acc.last_assistant,
                 "lastAssistantSourceId": acc.last_assistant_id,
                 "failure": acc.failure, "steps": acc.steps,
                 "sessions": acc.sessions, "recordsRead": records_read,
                 "truncated": acc.truncated,
                 "resumeBoundary": {"proven": True, "reason": None,
                                    "baselineFiles": len(baseline.files),
                                    "baselineLines": baseline_lines,
                                    "appendedLines": appended}}
    return facts


__all__ = [
    "RecordAccumulator", "RecordBaseline", "RecordStream", "fold_record", "freeze_session_records",
    "record_candidates", "resumed_session_record_facts", "session_record_facts",
]
