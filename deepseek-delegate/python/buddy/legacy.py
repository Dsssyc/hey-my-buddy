"""Offline, transactional, idempotent import of the removed Node implementation's records.

The import is one SQLite transaction: any conflict rolls the whole thing back and
leaves the source files untouched. It is offline by construction — it refuses to run
while the old owner is alive — and it never invents a specification or turns an
active legacy run into a completed one.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import socket
import tempfile
from pathlib import Path
from typing import Any

from . import schemas
from .db import canonical_json, utc_now
from .errors import BoardError
from .store import BoardStore

RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LEGACY_STATUSES = ("running", "cancelling", "completing", "completed", "cancelled", "failed", "interrupted")
LEGACY_TERMINAL = ("completed", "cancelled", "failed", "interrupted")
ACTIVE_STATUSES = ("running", "cancelling", "completing")
RECOVERY_LIMIT = (
    "This record was imported from the removed Node implementation without its task text, so the original "
    "specification cannot be shown or re-verified. The request ID is retained so it can never launch a duplicate, "
    "and the fingerprint is kept as the only remaining evidence of what was requested."
)


class LegacyImporter:
    def __init__(self, store: BoardStore):
        self.store = store

    # -- entry point ---------------------------------------------------------
    def run(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"sourceDir", "dryRun", "strict", "requestIds", "snapshot"}, "legacy.import")
        source = schemas.required_string(params, "sourceDir", max_length=4096)
        dry_run = schemas.optional_bool(params, "dryRun", True)
        strict = schemas.optional_bool(params, "strict", True)
        snapshot = schemas.optional_bool(params, "snapshot", False)
        only = params.get("requestIds")
        if only is not None:
            if not isinstance(only, list) or not all(isinstance(item, str) for item in only):
                raise BoardError("INVALID_ARGUMENT", "requestIds must be a list of strings")
        directory = Path(source).expanduser()
        if not directory.is_dir():
            raise BoardError("INVALID_ARGUMENT", "sourceDir must be an existing directory", sourceDir=str(directory))
        self.assert_owner_stopped(directory)
        if self._owns_this_directory(directory):
            # Importing the live target directory would read records this service is
            # concurrently writing. The old-owner exclusion is never relaxed: the
            # operator stops the old service, takes an immutable snapshot, and the
            # snapshot is imported instead.
            if not snapshot:
                raise BoardError(
                    "LEGACY_SOURCE_IS_LIVE_TARGET",
                    "sourceDir is this service's own state directory. Copy the old records to an immutable "
                    "snapshot after stopping the old service, or re-run with snapshot=true to import a "
                    "read-only copy taken outside this directory.",
                    sourceDir=str(directory),
                )
            directory = self._snapshot(directory)
            report_snapshot = str(directory)
        else:
            report_snapshot = None
        records, conflicts, notes = self.scan(directory, only)
        report = {
            "sourceDir": str(directory),
            "snapshotDir": report_snapshot,
            "dryRun": dry_run,
            "candidates": len(records),
            "conflicts": conflicts,
            "notes": notes,
            "activeRuns": [record["runId"] for record in records if record["status"] in ACTIVE_STATUSES],
            "counts": {
                "tasks": len(records),
                "withTaskText": sum(1 for record in records if record["task"] is not None),
                "withoutTaskText": sum(1 for record in records if record["task"] is None),
                "outcomes": sum(1 for record in records if record["status"] in LEGACY_TERMINAL),
                "inquiries": sum(len(record.get("inquiries") or {}) for record in records),
            },
            "sourceFilesUnchanged": True,
        }
        active = report["activeRuns"]
        if active:
            raise BoardError(
                "LEGACY_ACTIVE_RUNS",
                "The legacy state directory still holds active runs; an active run is never imported as completed",
                activeRuns=active,
                rollback=True,
            )
        if conflicts and strict:
            raise BoardError(
                "LEGACY_CONFLICT",
                "The legacy records do not validate; nothing was imported and the source files were not modified",
                conflicts=conflicts[:20],
                conflictCount=len(conflicts),
                rollback=True,
            )
        if dry_run:
            report["imported"] = 0
            report["wouldImport"] = len(records) - len(conflicts)
            report["note"] = "Dry run: no record was written. Re-run with dryRun=false to import in one transaction."
            return report
        imported, duplicates = self.import_records(records, conflicts)
        report.update({"imported": imported, "duplicates": duplicates, "wouldImport": None})
        report["note"] = (
            "Imported in a single transaction. Original task IDs, request IDs, fingerprints, outcomes, inquiry and "
            "acceptance evidence and timestamps were preserved; the source files were only read."
        )
        return report

    # -- safety --------------------------------------------------------------
    def _owns_this_directory(self, directory: Path) -> bool:
        try:
            return directory.resolve() == self.store.directory.resolve()
        except OSError:
            return False

    def _snapshot(self, directory: Path) -> Path:
        """Copy only the legacy records into a private immutable snapshot directory."""
        target = Path(tempfile.mkdtemp(prefix="legacy-snapshot-", dir=self.store.directory))
        os.chmod(target, 0o700)
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir() or not RUN_ID_PATTERN.match(entry.name):
                continue
            record = entry / "record.json"
            if not record.is_file():
                continue
            destination = target / entry.name
            destination.mkdir(mode=0o700)
            shutil.copy2(record, destination / "record.json")
            task_file = entry / "task.txt"
            if task_file.is_file():
                shutil.copy2(task_file, destination / "task.txt")
        return target


    def assert_owner_stopped(self, directory: Path) -> None:
        """Refuse to import while any old owner could still be writing."""
        endpoint = directory / "control.json"
        if endpoint.is_file():
            try:
                value = json.loads(endpoint.read_text())
                address = value.get("address")
            except (OSError, ValueError):
                address = None
            if isinstance(address, str) and address.startswith("ipc://"):
                raise BoardError(
                    "LEGACY_OWNER_RUNNING",
                    "The legacy service still owns this state directory; stop it before importing",
                    sourceDir=str(directory),
                )
        for lock_name in ("control-daemon.lock", "board-owner.lock"):
            lock_path = directory / lock_name
            if not lock_path.exists():
                continue
            fd = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BoardError(
                    "LEGACY_OWNER_RUNNING",
                    "A service still holds this state directory's lock; stop it before importing",
                    lock=lock_name,
                ) from None
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)
        legacy_socket = directory / "service.sock"
        if legacy_socket.exists():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                try:
                    probe.connect(str(legacy_socket))
                except OSError:
                    pass
                else:
                    raise BoardError(
                        "LEGACY_OWNER_RUNNING",
                        "The legacy Node service is still listening in this state directory",
                        sourceDir=str(directory),
                    )

    # -- scan ----------------------------------------------------------------
    def scan(self, directory: Path, only: list[str] | None) -> tuple[list[dict], list[dict], list[str]]:
        records: list[dict] = []
        conflicts: list[dict] = []
        notes: list[str] = []
        seen_request_ids: dict[str, str] = {}
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir() or not RUN_ID_PATTERN.match(entry.name):
                continue
            record_path = entry / "record.json"
            if not record_path.is_file():
                if entry.name.startswith("."):
                    continue
                notes.append(f"ignored directory without record.json: {entry.name}")
                continue
            try:
                record = json.loads(record_path.read_text())
            except (OSError, ValueError) as exc:
                conflicts.append({"runId": entry.name, "reason": f"record.json is unreadable: {exc}"})
                continue
            problem = self._validate(entry.name, record)
            if problem:
                conflicts.append({"runId": entry.name, "reason": problem})
                continue
            if only is not None and record["requestId"] not in only:
                continue
            if record["requestId"] in seen_request_ids:
                conflicts.append(
                    {
                        "runId": entry.name,
                        "reason": f"requestId duplicates run {seen_request_ids[record['requestId']]}",
                    }
                )
                continue
            seen_request_ids[record["requestId"]] = entry.name
            task_text = self._task_text(entry)
            legacy_input = dict(record["input"])
            legacy_input["task"] = task_text if task_text is not None else record["input"].get("task")
            verifiable = task_text is not None
            if verifiable:
                expected = schemas.legacy_fingerprint(
                    {
                        "cwd": record["input"]["cwd"],
                        "task": task_text,
                        "timeoutSeconds": record["input"].get("timeoutSeconds", 1800),
                        "workspace": record["input"].get("workspace", True),
                        **{
                            name: record["input"][name]
                            for name in ("model", "provider", "effort")
                            if name in record["input"]
                        },
                    }
                )
                if expected != record["inputHash"]:
                    conflicts.append(
                        {
                            "runId": entry.name,
                            "reason": "task.txt does not reproduce the recorded legacy input fingerprint",
                            "recordedFingerprint": record["inputHash"],
                            "recomputedFingerprint": expected,
                        }
                    )
                    continue
            notes.append(
                f"{entry.name}: {'verified against task.txt' if verifiable else 'imported as readable history only'}"
            )
            records.append({**record, "runId": entry.name, "task": task_text, "directory": entry})
        return records, conflicts, notes

    @staticmethod
    def _validate(run_id: str, record: Any) -> str | None:
        if not isinstance(record, dict):
            return "record.json is not an object"
        if record.get("runId") != run_id:
            return "record.runId does not match its directory"
        status = record.get("status")
        if status not in LEGACY_STATUSES:
            return f"unknown legacy status {status!r}"
        cwd = record.get("cwd")
        if not isinstance(cwd, str) or not cwd.startswith("/"):
            return "cwd is not an absolute path"
        request_id = record.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            return "requestId is missing or too long"
        fingerprint = record.get("inputHash")
        if not isinstance(fingerprint, str) or not schemas.SHA256_PATTERN.match(fingerprint):
            return "inputHash is not a sha256 digest"
        revision = record.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            return "revision is not a positive integer"
        for name in ("createdAt", "updatedAt"):
            value = record.get(name)
            if not isinstance(value, str) or not _parses(value):
                return f"{name} is missing or unparseable"
        if not isinstance(record.get("resultAvailable"), bool) or not isinstance(record.get("shutdownConfirmed"), bool):
            return "resultAvailable/shutdownConfirmed must be booleans"
        if record["resultAvailable"] and not isinstance(record.get("result"), dict):
            return "resultAvailable is true but result is not an object"
        input_value = record.get("input")
        if not isinstance(input_value, dict) or not isinstance(input_value.get("cwd"), str):
            return "input.cwd is missing"
        if input_value.get("task") is not None and not isinstance(input_value.get("task"), str):
            return "input.task must be a string or absent"
        if record.get("inquiries") is not None and not isinstance(record["inquiries"], dict):
            return "inquiries must be an object or absent"
        return None

    @staticmethod
    def _task_text(directory: Path) -> str | None:
        path = directory / "task.txt"
        try:
            return path.read_text()
        except OSError:
            return None

    # -- write ---------------------------------------------------------------
    def import_records(self, records: list[dict], conflicts: list[dict]) -> tuple[int, int]:
        skipped = {item["runId"] for item in conflicts}
        imported = 0
        duplicates = 0
        with self.store.db.write() as connection:
            for record in records:
                if record["runId"] in skipped:
                    continue
                existing = connection.execute(
                    "SELECT task_id, request_id, legacy FROM tasks WHERE request_id = ?", (record["requestId"],)
                ).fetchone()
                if existing is not None:
                    if existing["legacy"]:
                        duplicates += 1
                        continue
                    raise BoardError(
                        "LEGACY_CONFLICT",
                        "A non-legacy task already owns this requestId; nothing was imported",
                        requestId=record["requestId"],
                        existingTaskId=existing["task_id"],
                        rollback=True,
                    )
                self._insert(connection, record)
                imported += 1
        return imported, duplicates

    def _insert(self, connection, record: dict) -> None:
        task_id = record["runId"]
        created = record["createdAt"]
        updated = record["updatedAt"]
        task_text = record["task"]
        spec = {
            "adapter": "dsh",
            "cwd": record["cwd"],
            "task": task_text,
            "timeoutSeconds": int(record["input"].get("timeoutSeconds", 1800)),
            "workspace": bool(record["input"].get("workspace", True)),
            "requiredCapabilities": [],
            "exclusiveResources": [],
            "legacy": True,
        }
        for name in ("model", "provider", "effort"):
            if name in record["input"]:
                spec[name] = record["input"][name]
        status = record["status"]
        state = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "interrupted": "reconciliation-needed",
        }.get(status, "reconciliation-needed")
        connection.execute(
            "INSERT INTO tasks(task_id, request_id, owner, spec_json, spec_canonical_json, input_fingerprint,"
            " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, timeout_seconds, state,"
            " queue_reason, revision, created_at, updated_at, accepted_at, acceptance_note, acceptance_verdict, legacy,"
            " legacy_fingerprint, legacy_fingerprint_version)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                record["requestId"],
                "legacy",
                canonical_json(spec),
                canonical_json(spec),
                record["inputHash"],
                schemas.FINGERPRINT_VERSION_LEGACY,
                "dsh",
                "[]",
                record["cwd"],
                "[]",
                spec["timeoutSeconds"],
                state,
                RECOVERY_LIMIT if task_text is None else None,
                int(record["revision"]),
                created,
                updated,
                record.get("acceptedAt"),
                record.get("acceptanceNote"),
                "accepted" if record.get("acceptedAt") else None,
                1,
                record["inputHash"],
                schemas.FINGERPRINT_VERSION_LEGACY,
            ),
        )
        if record.get("resultAvailable") and isinstance(record.get("result"), dict):
            attempt_id = f"legacy-{task_id}"
            shutdown_confirmed = bool(record.get("shutdownConfirmed"))
            connection.execute(
                "INSERT INTO attempts(attempt_id, task_id, generation, worker_id, worker_identity,"
                " capability_version, nonce_verifier, claim_request_id, execution_state, ownership, adapter,"
                " result_json, shutdown_confirmed, exit_code, started_at, finished_at, created_at, updated_at, revision)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    attempt_id,
                    task_id,
                    1,
                    "legacy",
                    "legacy-node-engine",
                    schemas.FINGERPRINT_VERSION_LEGACY,
                    "legacy",
                    f"legacy-{task_id}",
                    "finished",
                    "owned",
                    "dsh",
                    canonical_json(record["result"]),
                    1 if shutdown_confirmed else 0,
                    record.get("exitCode"),
                    created,
                    updated,
                    created,
                    updated,
                ),
            )
            connection.execute(
                "UPDATE tasks SET selected_attempt_id=?, active_attempt_id=NULL WHERE task_id=?", (attempt_id, task_id)
            )
        for inquiry_id, inquiry in (record.get("inquiries") or {}).items():
            if not isinstance(inquiry, dict):
                continue
            answer = inquiry.get("answer")
            delivered = bool(inquiry.get("delivery"))
            state = inquiry.get("state") or ("answered" if answer else "unavailable")
            if state not in ("queued", "claimed", "delivered", "answered", "discarded", "unavailable"):
                state = "unavailable"
            if state == "answered" and not (isinstance(answer, dict) and answer.get("text")):
                state = "unavailable"
            connection.execute(
                "INSERT OR IGNORE INTO messages(message_id, task_id, attempt_id, inquiry_id, direction, author,"
                " recipient, correlation_id, body, body_bytes, payload_hash, state, reason, delivery_json, answer_json,"
                " attempts_count, created_at, updated_at, revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    f"legacy-{task_id}-{inquiry_id}",
                    task_id,
                    None,
                    inquiry_id,
                    "question",
                    "legacy-operator",
                    None,
                    inquiry_id,
                    inquiry.get("questionPreview") or "(legacy question text was not retained)",
                    int(inquiry.get("questionBytes") or 0),
                    inquiry.get("questionSha256") or "0" * 64,
                    state,
                    inquiry.get("reason"),
                    canonical_json(inquiry.get("delivery")) if delivered else None,
                    canonical_json(answer) if isinstance(answer, dict) else None,
                    int(inquiry.get("attempts") or 0),
                    inquiry.get("submittedAt") or created,
                    inquiry.get("updatedAt") or updated,
                ),
            )
        self.store._append_event(  # noqa: SLF001 - the importer is part of the store layer
            connection,
            "task.imported",
            task_id=task_id,
            revision=int(record["revision"]),
            payload={
                "legacyRunId": task_id,
                "legacyStatus": record["status"],
                "taskTextAvailable": task_text is not None,
                "recoveryLimit": RECOVERY_LIMIT if task_text is None else None,
                "importedAt": utc_now(),
            },
        )


def _parses(value: str) -> bool:
    from datetime import datetime

    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False
