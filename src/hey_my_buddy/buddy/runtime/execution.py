"""One process owns one execution, its native work and durable result delivery."""

import os
import signal

# Preserve stops received while the execution imports its runtime and reads its
# private startup material. Importing this module as a library changes no handler.
_early_signals = []

def _defer_signal(sig, _frame):
    _early_signals.append(sig)

if __name__ == "__main__" and os.name != "nt":
    # The owned stop fallback and identity-verified orphan recovery send TERM.
    # Windowless Windows executions receive their stop only over C-Two.
    signal.signal(signal.SIGTERM, _defer_signal)

import argparse
import math
import sys
import threading
import time
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import c_two as cc

from ...text import configure_utf8_output
from ...paths import native_path
from ...errors import BoardError
from ...protocol.capabilities import model_start_unseen
from ...protocol.client import BoardClient
from ...protocol import activity as activity_module
from ...protocol import execution as execution_protocol
from ...protocol.workspace_manifest import record_reference
from ...protocol.turn_input import NONE_TURN_INPUT_VERSION
from ...protocol.contracts import ExecutionLive
from ...protocol.run_identity import RunIdentity
from ...protocol.owned_process import WorkingDirectoryRejected
from ...protocol.reasons import reason as make_reason
from ...json_codec import canonical_json, decode_strict_json
from ...protocol.stop import WRAP_UP_INITIATORS, TERMINATION_REASONS
from ..harnesses.base import AdapterOutcome
from ..harnesses.registry import ExecutionContext
from ..harnesses.c_two_live import CTwoLiveEndpoint
from ...protocol.internal_models import Hex64, InternalModel, NonNegativeInt, Text, fail
from ..harnesses.live import (LIVE_FIELDS, MAX_REQUEST_ID, MAX_OBSERVE_LIMIT,
    MIN_TRANSPORT_TIMEOUT_MS, MAX_TRANSPORT_TIMEOUT_MS, LiveReply, LiveRequest, LiveSnapshot)
from ..roles import controller as role_seam
from .receipts import read_receipt, write_receipt, deliver_receipt, receipt_directory, is_retryable, REFUSED

fsync_json = execution_protocol.fsync_json
BUDGET = execution_protocol.BUDGET
TIMEOUT_GRACE_SECONDS = 10.0
PREFLIGHT_RETRY_SECONDS = 15.0
PREFLIGHT_MAX_RETRY_SECONDS = 180.0
(
    TERMINATION_COMPLETED, TERMINATION_WRAP_UP, TERMINATION_GRACE_EXPIRED,
    TERMINATION_FORCED_STOP, TERMINATION_CONSOLE_STOP, TERMINATION_SERVICE_STOP,
    TERMINATION_UNKNOWN_STOP, TERMINATION_PAUSE, TERMINATION_DEADLINE, TERMINATION_HARNESS_ERROR,
    TERMINATION_TRANSPORT_ERROR, TERMINATION_INTERRUPTED, TERMINATION_WORKSPACE_ERROR,
) = TERMINATION_REASONS


_PERSON_NAMES = (
    "Ada", "Alan", "Amber", "Ana", "Aria", "Ari", "Arthur", "Asha", "Aurelia", "Axel",
    "Bao", "Bea", "Bo", "Bruno", "Cara", "Cass", "Cecilia", "Cedric", "Chen", "Cleo",
    "Cora", "Dahlia", "Dara", "Dax", "Delia", "Dev", "Diana", "Dora", "Dorian", "Dun",
    "Edith", "Elena", "Elio", "Elsa", "Emrys", "Enzo", "Esme", "Ezra", "Faye", "Fen",
    "Fiona", "Fleur", "Fox", "Gaia", "Gideon", "Gina", "Greta", "Hana", "Harvey", "Hazel",
    "Heidi", "Hera", "Hild", "Ida", "Idris", "Ilya", "Ines", "Iris", "Isolde", "Iva",
    "Ivo", "Jade", "Jalen", "Jasper", "Jia", "Joon", "Juno", "Kai", "Kara", "Keir",
    "Kenzie", "Kestrel", "Kira", "Klaus", "Laleh", "Lena", "Leo", "Lila", "Lin", "Lior",
    "Livia", "Loke", "Lucia", "Lum", "Lyra", "Mabel", "Mae", "Magnus", "Mali", "Manon",
    "Mara", "Marius", "Marlow", "Matis", "Mei", "Milo", "Mira", "Mireille", "Nadia", "Nara",
    "Nell", "Nia", "Noor", "Nox", "Odile", "Ola", "Omar", "Ona", "Orion", "Oskar",
    "Otto", "Paloma", "Petra", "Phoebe", "Piet", "Pim", "Quinn", "Rada", "Rafa", "Remy",
    "Rhea", "Rin", "Romy", "Rook", "Rune", "Saga", "Sage", "Sami", "Sasha", "Selene",
    "Sena", "Sian", "Silas", "Sina", "Sol", "Solveig", "Sonny", "Sora", "Sten", "Suri",
    "Sven", "Talia", "Tamar", "Taro", "Tessa", "Teo", "Thalia", "Theo", "Tilde", "Tove",
    "Ugo", "Una", "Uri", "Valda", "Vera", "Vero", "Vesper", "Vida", "Viggo", "Vinnie",
    "Vita", "Vivi", "Wade", "Wren", "Xu", "Yan", "Yara", "Yuki", "Yusuf", "Zadie",
    "Zane", "Zara", "Zia", "Zola", "Zora", "Zuri",
)

def random_person_name() -> str:
    """One random person name for a run's endpoint; display use only."""
    return secrets.choice(_PERSON_NAMES)

class ModelResponseWait:
    """Execution-owned start wait; the service's file deadline is a fallback."""

    def __init__(self, path: Path, binding: dict, seconds: int, *, notify=None):
        self.path = path
        self.binding = binding
        self.seconds = seconds
        self.notify = notify
        self.deadline = None
        self.timed_out = False
        self.failure = None
        self.lock = threading.RLock()

    def begin(self):
        with self.lock:
            self.deadline = time.monotonic() + self.seconds
            self.timed_out = False
            deadline = self.deadline
        fsync_json(self.path, {"binding": self.binding,
                              "deadline": deadline + TIMEOUT_GRACE_SECONDS})
        if self.notify is not None:
            self.notify()

    def raise_failure(self):
        with self.lock:
            if self.failure is not None:
                raise self.failure

    def responded(self, handle):
        with self.lock:
            # A driver may normalize this I/O failure; supervision retains it.
            self.raise_failure()
            if self.timed_out:
                return
            handle.model_responded = True
            if self.deadline is not None:
                try:
                    native_path(self.path).unlink()
                except Exception as error:
                    self.failure = error
                    raise
                self.deadline = None

    def finished(self):
        with self.lock:
            self.raise_failure()
            if self.deadline is not None:
                native_path(self.path).unlink()
                self.deadline = None
            return self.timed_out

    def expired(self, handle):
        with self.lock:
            self.raise_failure()
            if self.timed_out:
                return True
            if handle.model_responded:
                self.responded(handle)
            return self.expired_now()

    def expired_now(self):
        """Remember a deadline reached before starting a native call."""
        with self.lock:
            self.raise_failure()
            self.timed_out = self.timed_out or (self.deadline is not None
                                                and time.monotonic() >= self.deadline)
            return self.timed_out

    def mark_timeout(self, outcome: AdapterOutcome) -> None:
        outcome.status = "failed"
        outcome.error = f"the Router model did not start responding within {self.seconds}s"
        outcome.result = {**outcome.result, "status": "error", "code": "router-not-responding",
                          "modelResponded": False}


class LiveEndpointDescriptor(InternalModel):
    """Bounded private readiness facts; the credential is native opaque JSON.

    The holder consumes ``endpointCredential`` through C-Two's codec. Address,
    instance and the held process PID remain independent binding checks.
    Windows uses a kernel-managed credential in the native pipe domain.
    """

    address: Text(256)
    name: Text(128)
    instance_id: Hex64
    host_pid: NonNegativeInt
    endpoint_credential: Text(8192)

def _timeout_ms(value: Any, label: str) -> int:
    """The preserved transport window bound, checked exactly as the live seam does."""
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_TRANSPORT_TIMEOUT_MS <= value <= MAX_TRANSPORT_TIMEOUT_MS:
        raise fail(f"{label} must be an integer between {MIN_TRANSPORT_TIMEOUT_MS} and "
                   f"{MAX_TRANSPORT_TIMEOUT_MS}", field=label)
    return value

def _selected_fields(fields: Any) -> frozenset:
    if fields is None:
        return frozenset(LIVE_FIELDS)
    if isinstance(fields, str) or not isinstance(fields, (list, tuple, set, frozenset)):
        raise fail(f"fields must be a selection of {', '.join(LIVE_FIELDS)}", field="fields")
    selected = frozenset(fields)
    if not selected <= frozenset(LIVE_FIELDS):
        raise fail(f"fields must be a selection of {', '.join(LIVE_FIELDS)}", field="fields")
    return selected or frozenset(LIVE_FIELDS)

def _observe_arguments(after_seq: Any, limit: Any, fields: Any, inquiry_id: Any) -> dict:
    """The observe arguments validated exactly as the live seam validates them."""
    if inquiry_id is not None:
        if not isinstance(inquiry_id, str) or not inquiry_id \
                or len(inquiry_id.encode()) > MAX_REQUEST_ID:
            raise fail("inquiryId must be a nonempty string of at most "
                       f"{MAX_REQUEST_ID} bytes", field="inquiryId")
        return {"inquiry_id": inquiry_id}
    if after_seq is not None and (isinstance(after_seq, bool) or not isinstance(after_seq, int)
                                  or after_seq < 0):
        raise fail("afterSeq must be null or a nonnegative integer", field="afterSeq")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_OBSERVE_LIMIT:
        raise fail(f"limit must be an integer between 1 and {MAX_OBSERVE_LIMIT}", field="limit")
    selected = _selected_fields(fields)
    return {"after_seq": after_seq, "limit": limit,
            "fields": tuple(selected) if selected != frozenset(LIVE_FIELDS) else None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_termination(outcome, *, timed_out, cancel_requested,
                         stop_initiator=None, grace_expired=False):
    if outcome.status == "ok":
        return TERMINATION_COMPLETED
    if timed_out:
        return TERMINATION_DEADLINE
    if stop_initiator == "deadline":
        return TERMINATION_DEADLINE
    if cancel_requested:
        result = outcome.result
        return role_seam.stop_termination_reason(shutdown_confirmed=outcome.shutdown_confirmed,
            stop_initiator=stop_initiator, grace_expired=result.get("graceExpired", grace_expired),
            wrap_up_delivered=result.get("wrapUpDelivered", False))
    # The turn seal runs only after a completed native turn. Partial sealing and
    # cumulative patch capture can also fail while preserving an earlier failure.
    if (outcome.result.get("workspaceSealError") is not None
            or outcome.result.get("code") == WorkingDirectoryRejected.code):
        return TERMINATION_WORKSPACE_ERROR
    if outcome.result.get("code") == "transport-error":
        return TERMINATION_TRANSPORT_ERROR
    return TERMINATION_HARNESS_ERROR


def validate_claim_turn(claim: dict) -> None:
    """Check the service-owned claim once as it enters this process."""
    if not isinstance(claim, dict):
        raise BoardError("INVALID_ARGUMENT", "Execution claim must be an object")
    task, attempt = claim.get("task"), claim.get("attempt")
    if (not isinstance(task, dict) or not isinstance(task.get("taskId"), str) or not task["taskId"]
            or not isinstance(task.get("spec"), dict)
            or not isinstance(task["spec"].get("adapter"), str)
            or not isinstance(attempt, dict) or attempt.get("taskId") != task["taskId"]
            or not isinstance(attempt.get("attemptId"), str) or not attempt["attemptId"]
            or type(attempt.get("generation")) is not int or attempt["generation"] <= 0):
        raise BoardError("INVALID_ARGUMENT", "Execution claim has an invalid task or attempt")
    if task["spec"]["adapter"] == "decision":
        decision = claim.get("decisionInput")
        if (not isinstance(decision, dict) or decision.get("routingMode") not in ("fast", "review", "profile")
                or not isinstance(decision.get("buddy"), dict)
                or not isinstance(decision["buddy"].get("harness"), str)
                or not decision["buddy"]["harness"]):
            raise BoardError("INVALID_ARGUMENT", "Execution claim has invalid decision input")
        if task["spec"].get("decision", {}).get("kind") == "maintain":
            if (decision.get("kind") != "maintain" or decision["routingMode"] != "fast"
                    or decision.get("maintenanceRunId") != task["spec"]["decision"]["decisionId"]
                    or not isinstance(decision.get("input"), dict)):
                raise BoardError("INVALID_ARGUMENT", "Execution claim has invalid maintenance input")
        elif task["spec"].get("decision", {}).get("kind") == "profile":
            if (decision.get("kind") != "profile" or decision["routingMode"] != "profile"
                    or decision.get("profileRunId") != task["spec"]["decision"]["decisionId"]
                    or not isinstance(decision.get("input"), dict)):
                raise BoardError("INVALID_ARGUMENT", "Execution claim has invalid profile input")
        wait = task["spec"].get("modelResponseWaitSeconds")
        if type(wait) is not int or wait < 1:
            raise BoardError("INVALID_ARGUMENT", "Execution claim has invalid model response wait")
    if "turn" not in claim:
        return
    turn = claim["turn"]
    if not isinstance(turn, dict) or not isinstance(turn.get("turnId"), str) or not turn["turnId"]:
        raise BoardError("INVALID_ARGUMENT", "Execution claim has an invalid turn identity")
    turn_input = turn.get("input")
    if (not isinstance(turn_input, dict) or type(turn_input.get("version")) is not int
            or turn_input["version"] not in (4, NONE_TURN_INPUT_VERSION)):
        raise BoardError("INVALID_ARGUMENT", "Execution claim has an invalid turn input")
    workspace = turn_input.get("executionWorkspace")
    if turn_input["version"] == 4:
        if not isinstance(workspace, dict) or set(workspace) != {"workspaceId", "manifestSha256"}:
            raise BoardError("INVALID_ARGUMENT", "Execution claim has an invalid workspace")
    else:
        from ...protocol.turn_input import none_execution_workspace
        try:
            none_execution_workspace(workspace)
        except BoardError as error:
            raise BoardError("INVALID_ARGUMENT", "Execution claim has an invalid none workspace") from error
    if not isinstance(claim.get("agentCredential"), str) or not claim["agentCredential"]:
        raise BoardError("INVALID_ARGUMENT", "Execution claim has an invalid turn credential")


class _ExecutionLive:
    """The single C-Two resource owned for the full execution lifetime."""

    def __init__(self, execution):
        self.execution = execution
        attempt = execution.claim["attempt"]
        self.identity = RunIdentity(task_id=execution.claim["task"]["taskId"],
            attempt_id=attempt["attemptId"], generation=attempt["generation"],
            invocation_id="execution", turn_id=(execution.claim["turn"]["turnId"]
                                                if "turn" in execution.claim else None))
        self.name = random_person_name()
        self.instance_id = secrets.token_hex(32)
        self.token = secrets.token_hex(32)
        self._lock = threading.RLock()
        self._native: CTwoLiveEndpoint | None = None
        self._started = False

    def start(self, process_identity: dict) -> None:
        cc.register(ExecutionLive, self, name=self.name,
                    concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
        self._started = True
        address = cc.server_address()
        credential = cc.server_credential()
        if credential is None:
            raise BoardError("LIVE_UNAVAILABLE", "Registered execution endpoint credential is absent")
        descriptor = LiveEndpointDescriptor(address=address, name=self.name,
            instance_id=self.instance_id, host_pid=process_identity["pid"],
            endpoint_credential=credential.to_json())
        fsync_json(self.execution.directory / execution_protocol.LIVE, {
            "identity": self.identity.to_payload(), "processIdentity": process_identity,
            "descriptor": descriptor.to_payload(), "token": self.token})

    def bind_native(self, endpoint: CTwoLiveEndpoint) -> None:
        with self._lock:
            self._native = endpoint

    def unbind_native(self, endpoint: CTwoLiveEndpoint) -> None:
        with self._lock:
            if self._native is endpoint:
                self._native = None

    def finish(self) -> None:
        if self._started:
            try:
                cc.unregister(self.name)
            finally:
                cc.shutdown()
                native_path(self.execution.directory / execution_protocol.LIVE).unlink(missing_ok=True)

    def _auth(self, raw: str, fields: set[str]) -> dict:
        if not isinstance(raw, str) or len(raw.encode()) > 65536:
            raise BoardError("LIVE_UNAVAILABLE", "Invalid execution live frame")
        value = decode_strict_json(raw)
        if not isinstance(value, dict) or set(value) != {
                "identity", "instanceId", "token", *fields}:
            raise BoardError("LIVE_UNAVAILABLE", "Invalid execution live frame")
        if (RunIdentity.from_payload(value["identity"]) != self.identity
                or value["instanceId"] != self.instance_id
                or not isinstance(value["token"], str)
                or not secrets.compare_digest(value["token"], self.token)):
            raise BoardError("LIVE_UNAVAILABLE", "Execution live identity refused")
        return value

    def send(self, request_json: str) -> str:
        value = self._auth(request_json, {"request", "timeoutMs", "deadlineMonotonic"})
        request = LiveRequest.from_payload(value["request"])
        if request.identity != self.identity:
            return canonical_json(LiveReply(status="unavailable",
                                            reason_code="identity-mismatch").to_payload())
        timeout_ms = _timeout_ms(value["timeoutMs"], "timeoutMs")
        deadline = value["deadlineMonotonic"]
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline < 0:
            raise BoardError("INVALID_ARGUMENT", "Invalid live request deadline")
        with self._lock:
            native = self._native
        if native is None:
            return canonical_json(LiveReply(status="unavailable", reason_code="source-unavailable").to_payload())
        # The wire names the execution; the current run owns its own invocation
        # identity. Select it locally after the one outer authentication.
        local_request = request.model_copy(update={"identity": native.identity})
        return native.request_local(local_request, timeout_ms=timeout_ms,
                                    deadline_monotonic=deadline)

    def observe(self, request_json: str) -> str:
        value = self._auth(request_json, {"afterSeq", "limit", "fields", "inquiryId"})
        arguments = _observe_arguments(value["afterSeq"], value["limit"],
                                       value["fields"], value["inquiryId"])
        with self._lock:
            native = self._native
        if native is None:
            return canonical_json(LiveSnapshot(unavailable="source-unavailable", observed=False,
                                               reason="source-unavailable").to_payload())
        return native.observe_local(**arguments)

    def capabilities(self, request_json: str) -> str:
        self._auth(request_json, set())
        with self._lock:
            native = self._native
        if native is None:
            raise BoardError("LIVE_UNAVAILABLE", "No active native run")
        return native.capabilities_local()

    def close(self, request_json: str) -> str:
        self._auth(request_json, {"reason"})
        return canonical_json({"closed": True})

    def stop(self, request_json: str) -> str:
        value = self._auth(request_json, {"initiator", "graceDeadline", "force"})
        if value["initiator"] not in {"host", "lead", "console", "pause", "service", "deadline", "unknown"}:
            raise BoardError("INVALID_ARGUMENT", "Unknown stop initiator")
        if type(value["force"]) is not bool:
            raise BoardError("INVALID_ARGUMENT", "Invalid stop force fact")
        deadline = value["graceDeadline"]
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise BoardError("INVALID_ARGUMENT", "Invalid stop deadline")
        self.execution.request_stop(value["initiator"], deadline, force=value["force"])
        return canonical_json({"accepted": True})

class Execution:
    def __init__(self, claim: dict, state_dir: Path, nonce: str, *,
                 session_record_path: str | None = None, client=None, log=None):
        from ...protocol.rpc_config import configure_server, configure_client
        configure_server(state_dir)
        configure_client(state_dir)
        self.claim, self.state_dir, self.nonce = claim, Path(state_dir), nonce
        self.session_record_path = session_record_path
        self.client = client or BoardClient(self.state_dir, autostart=False)
        self._log = log or (lambda message: None)
        self.control = _ExecutionControl(self)
        self._hints: dict = {}
        self._hint_seqs: dict = {}
        self._hint_serial = 0
        self._hint_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._flusher = _HintFlusher(self)
        self._identity_pending: dict | None = None
        self._identity: dict | None = None
        self._next_hint_at = 0.0
        self._hint_delay = 0.25
        self.directory = receipt_directory(self.state_dir, execution_protocol.private_adapter(claim),
                                           claim["task"]["taskId"], claim["attempt"]["attemptId"])
        self.evidence_directory = (self.state_dir / "attempts" / claim["task"]["taskId"] /
                                   claim["attempt"]["attemptId"])
        self.live = _ExecutionLive(self)

    def log(self, message):
        self._log(f"[execution {self.claim['attempt']['attemptId']}] {message}")

    def request_stop(self, initiator="unknown", grace_deadline=None, *, force=False, from_signal=False):
        self.control.request_stop(initiator, grace_deadline, force=force, from_signal=from_signal)

    def stop_requested(self):
        return self.control.cancel_view is not None

    def hint(self, **data):
        """Record one hint under the short memory lock and wake the flusher.

        Every write allocates a strictly increasing, never-reused sequence for
        its key — including an explicit ``waitingReason=None`` clear — and the
        waiting reason's sequence travels beside it as ``waitingReasonSeq``.
        """
        with self._hint_lock:
            for key, value in data.items():
                if key == "activity":
                    # A slower observation never overwrites a newer activity.
                    current = self._hints.get(key)
                    if current is None or activity_module.is_newer(value, current):
                        self._hint_serial += 1
                        self._hints[key] = value
                        self._hint_seqs[key] = self._hint_serial
                    continue
                self._hint_serial += 1
                self._hints[key] = value
                self._hint_seqs[key] = self._hint_serial
        self._flusher.wake()

    def flush_hints(self, *, delivering: bool = False) -> bool:
        with self._flush_lock:
            with self._hint_lock:
                pending = {key: (self._hint_seqs[key], self._hints[key])
                           for key in tuple(self._hints)}
            if not pending and self._identity_pending is None:
                return True
            if time.monotonic() < self._next_hint_at:
                return False
            attempt = self.claim["attempt"]
            data = None
            if pending:
                data = {}
                for key, (seq, value) in pending.items():
                    data[key] = value
                    if key == "waitingReason":
                        data["waitingReasonSeq"] = seq
            try:
                # Windows caches its own identity and carries it with every
                # progress it already sends, so the first progress after a
                # service restart reaches the same no-handle verification.
                self.client.progress(attempt["attemptId"], attempt["generation"],
                                     self.nonce,
                                     process_identity=self._identity if os.name == "nt" else self._identity_pending,
                                     phase="executing" if self._identity_pending else None,
                                     data=data)
            except BoardError as error:
                if is_retryable(error):
                    return self._defer_hints(error)
                if delivering or error.code in REFUSED:
                    raise
                return self._defer_hints(error)
            except Exception as error:
                if delivering:
                    raise
                return self._defer_hints(error)
            self._next_hint_at = 0.0
            self._hint_delay = 0.25
            with self._hint_lock:
                self._identity_pending = None
                for key, (seq, _value) in pending.items():
                    # Only an unchanged sequence proves the acknowledged
                    # snapshot is still the newest value for the key: value
                    # equality would also drop a newer rewrite that happens
                    # to compare equal.
                    if self._hint_seqs.get(key) == seq:
                        self._hints.pop(key, None)
                        self._hint_seqs.pop(key, None)
            return True

    def _defer_hints(self, error: Exception) -> bool:
        self.log(f"execution progress pending: {error!r}")
        self._next_hint_at = time.monotonic() + self._hint_delay
        self._hint_delay = min(10.0, self._hint_delay * 2)
        return False

    def run(self):
        # The durable empty journal precedes the identity acknowledgement:
        # any acknowledged execution therefore has a native-spawn record to
        # inspect after a crash. Native work still waits for the acknowledgement.
        # A Windows root keeps no journal: its no-breakaway Job's stop evidence
        # already covers every native member and the whole creation window.
        from ...protocol.process_identity import install_native_process_journal, native_process_journal_path
        binding = {"taskId": self.claim["task"]["taskId"],
                   "attemptId": self.claim["attempt"]["attemptId"],
                   "generation": self.claim["attempt"]["generation"]}
        if os.name != "nt":
            install_native_process_journal(native_process_journal_path(self.directory), binding)
        delay = 0.25
        while self._identity_pending is not None:
            delivered = self.flush_hints(delivering=True)
            if self._identity_pending is not None:
                time.sleep(delay)
                if not delivered:
                    delay = min(10.0, delay * 2)
        # The identity is acknowledged: from here the flusher thread owns every
        # periodic progress report, so control never waits on the transport.
        self._flusher.start()
        try:
            receipt = self.execute()
            if receipt["report"]["shutdownConfirmed"] is True:
                native_path(self.directory / BUDGET).unlink(missing_ok=True)
            self.control.raise_fatal()
        finally:
            # The receipt bytes are durable; the flusher stops its periodic
            # loop so the delivery path below drains what is left itself.
            self._flusher.stop()
        return deliver_receipt(self.client, receipt, self.directory, nonce=self.nonce, log=self.log,
                               flush_hints=self._flush_delivery_hints)

    def _flush_delivery_hints(self):
        self.control.raise_fatal()
        return self.flush_hints(delivering=True)

    def execute(self) -> dict:
        """Run one attempt and always return a durable receipt.

        The whole body is guarded: if anything fails after the adapter was started,
        the owned process session is terminated and a receipt is still written, so a
        failure can never leave an orphaned child with no record.
        """
        claim = self.claim
        started_at = time.monotonic()
        attempt = claim["attempt"]
        task = claim["task"]
        spec = task["spec"]
        directory = self.directory
        pending = read_receipt(directory)
        if pending is not None:
            return pending
        # The holder records the live child handle the instant it exists. Recovery
        # decisions are made from that handle — never from whether a marker file
        # happened to be written, which can fail after a child is already running.
        holder: dict = {"handle": None, "adapter": None, "started": False, "start_invoked": False}
        try:
            return self._execute_guarded(claim, task, spec, attempt, directory, holder)
        except BaseException as error:  # noqa: BLE001 - every failure becomes a receipt
            handle = holder.get("handle") or getattr(error, "execution_handle", None)
            implementation = holder.get("adapter")
            spawn_evidence = getattr(error, "owned_popen_evidence", None)
            shutdown_confirmed = (holder.get("confirmed_stopped") is True or
                                  getattr(spawn_evidence, "shutdown_confirmed", False) is True)
            if not shutdown_confirmed and handle is not None and implementation is not None:
                try:
                    role_seam.worker_cancel(implementation, handle)
                    handle.wait(timeout=None)
                except Exception as cleanup_error:  # noqa: BLE001 - keep the honest default
                    self.log(f"could not confirm this attempt's child stopped: {cleanup_error!r}")
                try:
                    shutdown_confirmed = handle.shutdown_confirmed() is True
                except Exception as proof_error:  # noqa: BLE001 - an unreadable proof is unknown
                    self.log(f"could not inspect the native stop proof: {proof_error!r}")
            elif not shutdown_confirmed and not holder.get("start_invoked"):
                # Validation failed before start() could spawn anything.
                shutdown_confirmed = True
            self.log(
                f"execution failed after the attempt started: {error!r} "
                f"(shutdownConfirmed={shutdown_confirmed})"
            )
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": ({"status": "error", "code": error.code,
                                "usage": {"elapsedMs": round((time.monotonic() - started_at) * 1000),
                                          "toolCalls": None}}
                               if isinstance(error, BoardError) and not holder.get("started") else None),
                    "error": f"execution failure: {error!r}",
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": shutdown_confirmed,
                    "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR,
                },
                directory,
            )
        finally:
            self.control.stop()

    def _execute_guarded(
        self, claim: dict, task: dict, spec: dict, attempt: dict, directory: Path, holder: dict
    ) -> dict:
        from ...blackboard.service.harness_health import HARNESSES
        from ..harnesses.runtime_selection import bound

        name = spec['adapter']
        review = name == 'decision' and claim['decisionInput']['routingMode'] == 'review'
        if name == 'decision':
            name = claim['decisionInput']['buddy']['harness']
        control = self.control
        holder['preflightControl'] = control
        control.start()
        try:
            if name not in HARNESSES:
                return self._execute_selected(claim, task, spec, attempt, directory, holder)
            history = []
            record = None
            for retry in (False, True):
                unknown_reads = 0
                def cancelled(view):
                    return self._preflight_cancelled(claim, directory, record, view)
                while True:
                    control.raise_fatal()
                    if control.cancel_view is not None or self.stop_requested():
                        return cancelled(control.cancel_view)
                    command_id = f"harness-{attempt['attemptId']}-{int(retry)}"
                    if unknown_reads:
                        command_id += f"-unknown-{unknown_reads}"
                    try:
                        response = self.client.call('harness_prepare', {
                            'attemptId': attempt['attemptId'], 'generation': attempt['generation'],
                            'nonce': self._nonce(), 'commandId': command_id, 'retry': retry and not unknown_reads,
                            'healthRevision': record.get('revision') if record else None,
                        })
                    except BoardError as error:
                        if error.code == 'ATTEMPT_FINISHED':
                            view = control.cancel_view
                            if view is None:
                                try:
                                    view = self.client.call('task_get', {'runId': attempt['taskId']})['task']
                                except Exception:
                                    raise error
                            if view.get('cancelRequested'):
                                return cancelled(view)
                        # A forced probe can commit health before its command receipt.
                        if not is_retryable(error, pre_dispatch_only=retry and not unknown_reads):
                            raise
                        # The request may already be committed: replay its exact identity.
                        control.wait(min(PREFLIGHT_MAX_RETRY_SECONDS,
                                         PREFLIGHT_RETRY_SECONDS * 2 ** min(unknown_reads, 4)))
                        continue
                    record = response['harness']
                    if record is None or (record.get('status') != 'unknown'
                                          and (record.get('lastProbe') or {}).get('status') != 'unknown'):
                        break
                    unknown_reads += 1
                    control.wait(min(PREFLIGHT_MAX_RETRY_SECONDS,
                                     PREFLIGHT_RETRY_SECONDS * 2 ** min(unknown_reads - 1, 4)))
                if control.cancel_view is not None or self.stop_requested():
                    return cancelled(control.cancel_view)
                history.append({'retry': retry, 'harness': record})
                if record is None or record['status'] != 'ready':
                    if not retry:
                        continue
                    if review:
                        return self._review_unavailable(claim, directory, history, 'router-unavailable')
                    return self.receipt(claim, {'status': 'failed', 'result': {'harness': record, 'harnessAttempts': history},
                        'error': 'HARNESS_UNAVAILABLE',
                        'exitCode': None, 'signal': None, 'shutdownConfirmed': True, 'artifacts': [],
                        'terminationReason': TERMINATION_HARNESS_ERROR}, directory)
                holder['harnessHistory'] = history
                holder['harnessRetry'] = retry
                try:
                    with bound([record], environment={**os.environ, "HMB_STATE_DIR": str(self.state_dir)}):
                        return self._execute_selected(claim, task, spec, attempt, directory, holder)
                except (OSError, BoardError) as error:
                    if (isinstance(error, BoardError) and error.code == 'ATTEMPT_FINISHED'
                            and (control.cancel_view is not None or self.stop_requested())):
                        return cancelled(control.cancel_view)
                    # A start() error with no returned handle may still follow spawn.
                    # Retry only if start was never called or the previous child has
                    # independently produced complete stop proof.
                    if holder.get('start_invoked') and not holder.get('confirmed_stopped'):
                        raise
                    startup_failure = (isinstance(error, BoardError) and error.code in {'ADAPTER_UNAVAILABLE', 'HARNESS_UNAVAILABLE', 'HARNESS_PREMODEL_FAILED'})
                    startup_failure = startup_failure or (isinstance(error, OSError) and error.filename in record.get('command', []))
                    if retry and not holder.get('started') and startup_failure:
                        self._harness_failed(attempt, record)
                        if review:
                            return self._review_unavailable(claim, directory, history, 'router-unavailable')
                    if retry or holder.get('started') or not startup_failure:
                        raise
            raise AssertionError('unreachable harness retry')
        finally:
            control.stop()
            control.join()
            holder.pop('preflightControl', None)

    def _preflight_cancelled(self, claim, directory, record, view):
        return self.receipt(claim, {'status': 'cancelled', 'result': {'harness': record} if record else None,
            'error': None, 'exitCode': None, 'signal': None, 'shutdownConfirmed': True,
            'artifacts': [], 'terminationReason': role_seam.stop_termination_reason(
                shutdown_confirmed=True, stop_initiator=(view or {}).get('stopInitiator'))}, directory)

    def _review_unavailable(self, claim, directory, history, code):
        from ...blackboard.routing.router import routing_facts
        return self.receipt(claim, {'status': 'failed', 'result': {
            'status': 'error', 'code': 'router-review-unavailable', 'reasonCode': code,
            'reason': 'The review harness is unavailable',
            'modelResponded': False, 'harnessAttempts': history,
            **routing_facts(claim['decisionInput'])},
            'error': code, 'exitCode': None, 'signal': None, 'shutdownConfirmed': True,
            'artifacts': [], 'terminationReason': TERMINATION_HARNESS_ERROR}, directory)

    def _harness_failed(self, attempt, record):
        self.client.call('harness_prepare', {'attemptId': attempt['attemptId'],
            'generation': attempt['generation'], 'nonce': self._nonce(), 'failedOnly': True,
            'healthRevision': record['revision'], 'commandId': f"harness-failed-{attempt['attemptId']}"})

    def _execute_selected(
        self, claim: dict, task: dict, spec: dict, attempt: dict, directory: Path, holder: dict
    ) -> dict:
        from ...install import runtime
        from ..roles.run_execution import set_maintenance_publisher, set_profile_publisher
        set_maintenance_publisher(self._maintenance_answer)
        set_profile_publisher(self._profile_answer)

        def notify_model_wait():
            # The budget file is already durable when this runs; recording the
            # paired-seq hint wakes the flusher, whose progress call triggers
            # the service's attempt wake. Nothing here waits on the transport.
            self.hint(waitingReason=None)

        # The start wait judges only a protocol that can show the model's start
        # as it happens. A harness declared unable to see it — DSH's ACP channel
        # emits each block only after the model's whole step ends — runs without
        # the wait, bounded by the harness's own stream idle timeout instead, so
        # a silent first step is never read as a model that did not begin.
        wait_seconds = None
        if spec["adapter"] == "decision" and model_start_unseen(
                claim["decisionInput"]["buddy"]["harness"]) is None:
            wait_seconds = spec["modelResponseWaitSeconds"]
        model_wait = (ModelResponseWait(self.directory / BUDGET,
            {"taskId": task["taskId"], "attemptId": attempt["attemptId"], "generation": attempt["generation"]},
            wait_seconds, notify=notify_model_wait) if wait_seconds is not None else None)
        context = ExecutionContext(
            task_id=task["taskId"],
            attempt_id=attempt["attemptId"],
            generation=attempt["generation"],
            spec=spec,
            directory=self.evidence_directory,
            runtime=runtime.resolve_runtime(),
            environment={**os.environ, "HMB_STATE_DIR": str(self.state_dir)},
            decision_input=claim["decisionInput"] if spec["adapter"] == "decision" else None,
            # The service-owned turn identity and bounded context; the adapter stages
            # them for the runner. The scoped credential is written to a private file
            # and exported by path, never into the model-visible turn input.
            turn=claim["turn"] if "turn" in claim else None,
            agent_credential=claim["agentCredential"] if "turn" in claim else None,
            session_record_path=self.session_record_path,
            model_response_wait=model_wait,
        )
        implementation = role_seam.worker_executor(spec["adapter"])
        control = holder['preflightControl']
        preparation = role_seam.prepare_worker_run(implementation, context)
        unknown_reads = 0
        waited = False
        try:
            while preparation.error is not None and preparation.error.code == 'router-input-unknown':
                control.raise_fatal()
                waited = True
                self._wait_control_observation(claim, control, unknown_reads)
                unknown_reads += 1
                preparation = role_seam.prepare_worker_run(implementation, context)
        finally:
            if waited:
                # Leaving the wait loop — normally or on cancellation/error — clears
                # its hint so receipt delivery cannot flush an already-ended wait.
                self.hint(waitingReason=None)
        if not preparation.available:
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": None,
                    "error": preparation.reason,
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": True,
                    "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR,
                },
                directory,
            )
        if preparation.error is not None:
            error = preparation.error
            if holder.get('harnessHistory') and error.code == 'HARNESS_UNAVAILABLE':
                # The captured BoardError itself: the guarded harness retry above
                # matches on this code, so the original object and code must reach it.
                raise error
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": {"status": "error", "code": error.code,
                               "stopEvidence": {"shutdownConfirmed": True,
                                                "native": {"shutdownConfirmed": True}}},
                    "error": f"{error.code}: {error.message}",
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": True,
                    "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR,
                },
                directory,
            )
        started = time.monotonic()
        if model_wait is not None:
            model_wait.begin()
        else:
            self.hint(waitingReason=None)
        holder["adapter"] = implementation
        # A previous pre-model retry may have proved its own child stopped. That
        # proof cannot cover this start(): it might spawn before raising a handle.
        holder.pop("confirmed_stopped", None)
        holder.pop("handle", None)
        control.raise_fatal()
        if self.stop_requested():
            # A stop that won the race past the last wait check must not launch
            # a native child at all; the receipt records the cancelled stop.
            return self._preflight_cancelled(claim, directory, None, self.control.cancel_view)
        if model_wait is not None and model_wait.expired_now():
            outcome = AdapterOutcome("failed", result={"nativeRequestSent": False}, shutdown_confirmed=True)
            model_wait.mark_timeout(outcome)
            report = outcome.to_report()
            report["terminationReason"] = TERMINATION_DEADLINE
            report["elapsedSeconds"] = round(time.monotonic() - started, 1)
            return self.receipt(claim, report, directory)
        holder["start_invoked"] = True
        handle = role_seam.worker_start(implementation, context)
        holder["handle"] = handle
        holder["started"] = True
        control.attach(handle, implementation)
        timed_out = False
        while True:
            control.raise_fatal()
            finished = handle.wait(timeout=0.25) is not None
            if finished:
                break
            if model_wait is not None and model_wait.expired(handle):
                timed_out = True
                self.log("Router model did not start responding; cancelling this owned process session")
                role_seam.worker_cancel(implementation, handle)
                handle.wait(timeout=None)
                break
            if (handle.grace_deadline is not None and time.monotonic() >= handle.grace_deadline
                    and not handle.force_stop_requested):
                handle.grace_expired = True
                self.log("Host stop grace reached; forcing the owned run to stop")
                role_seam.worker_cancel(implementation, handle)
        # The native session has ended: retire the live handle so a stop that
        # arrives after the settled result cannot cancel it and distort the
        # collected facts.
        control.detach()
        if model_wait is not None and not timed_out:
            timed_out = model_wait.finished()
        unknown_reads = 0
        waited = False
        try:
            while True:
                try:
                    outcome = role_seam.worker_collect(implementation, handle, context)
                    break
                except BoardError as error:
                    if error.code != 'router-input-unknown':
                        raise
                    holder['confirmed_stopped'] = True
                    waited = True
                    self._wait_control_observation(claim, control, unknown_reads)
                    unknown_reads += 1
        finally:
            if waited:
                # Same clearing as the prepare loop: cancellation or any other
                # exit must not leave the ended wait queued for receipt delivery.
                self.hint(waitingReason=None)
        holder["confirmed_stopped"] = outcome.shutdown_confirmed is True
        result = getattr(handle, "result", None)
        if result is not None and result.activity is not None:
            self.hint(activity=result.activity.value)
        if outcome.result.get("code") == WorkingDirectoryRejected.code:
            rejection = result.end.working_directory_rejection
            outcome.result["reason"] = WorkingDirectoryRejected.reason_for(rejection.cwd, rejection.system_reason)
        # A Router projection must retain a failure to preserve its native evidence.
        retention_failure = getattr(handle, "evidence_retention_failure", None)
        if isinstance(retention_failure, dict):
            outcome.result = {**outcome.result, "evidenceRetention": retention_failure}
        if (holder.get('harnessHistory') and not timed_out
                and not handle.cancel_requested and outcome.shutdown_confirmed and outcome.status == 'failed'
                and outcome.result.get('nativeRequestSent') is False
                and outcome.result.get('code') in {'harness-unavailable', 'invalid-native-result', 'invalid-protocol', 'transport-error', 'native-rpc-error', 'protocol-error', 'native-exit', 'connection-closed'}):
            holder['started'] = False
            holder['confirmed_stopped'] = True
            holder.pop('handle', None)
            raise BoardError('HARNESS_PREMODEL_FAILED', 'The native protocol failed before any model input was sent')
        if timed_out:
            model_wait.mark_timeout(outcome)
        # The genuine reason this attempt stopped. It is recorded with the receipt,
        # so a cancelled task, an expired deadline, a harness failure and a completed
        # turn stay distinguishable even after a transport outage.
        termination_reason = classify_termination(
            outcome,
            timed_out=timed_out,
            cancel_requested=bool(handle.cancel_requested),
            stop_initiator=handle.stop_initiator,
            grace_expired=handle.grace_expired,
        )
        from .partial_outputs import enrich
        if enrich(context, outcome) and termination_reason == TERMINATION_COMPLETED:
            termination_reason = TERMINATION_WORKSPACE_ERROR
        if termination_reason in (TERMINATION_WRAP_UP, TERMINATION_GRACE_EXPIRED, TERMINATION_FORCED_STOP,
                                  TERMINATION_CONSOLE_STOP, TERMINATION_SERVICE_STOP, TERMINATION_UNKNOWN_STOP,
                                  TERMINATION_PAUSE):
            outcome.status = "cancelled"
        if "partialOutput" in outcome.result:
            outcome.result["partialOutput"]["reason"] = termination_reason
        for key in ("workspaceSeal", "partialWorkspaceSeal"):
            if isinstance(outcome.result.get(key), dict):
                outcome.result[key] = record_reference(self.state_dir, outcome.result[key])
        report = outcome.to_report()
        if holder.get('harnessHistory'):
            report['result'] = {**(report.get('result') or {}), 'harnessAttempts': holder['harnessHistory']}
        report["terminationReason"] = termination_reason
        report["elapsedSeconds"] = round(time.monotonic() - started, 1)
        log_paths = (context.log_paths() if spec["adapter"] == "command" else {
            "stdout": str(self.evidence_directory / "execution.stdout.log"),
            "stderr": str(self.evidence_directory / "execution.stderr.log")})
        report["logPaths"] = {name: path for name, path in log_paths.items() if native_path(Path(path)).is_file()}
        report["runtimeIdentity"] = context.runtime.get("identity")
        return self.receipt(claim, report, directory)

    def _maintenance_answer(self, raw, round_index, observations):
        from ...protocol import maintenance as files
        run_id = self.claim["decisionInput"]["maintenanceRunId"]
        reference = files.publish(self.state_dir, run_id, {"answer": raw, "observations": observations})
        attempt = self.claim["attempt"]
        try:
            response = self.client.call("execution_maintenance", {
                "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": self.nonce,
                "round": round_index, "answer": reference})
            return files.read(self.state_dir, run_id, response["feedback"])
        finally:
            files.discard_reference(self.state_dir, run_id, reference)

    def _profile_answer(self, raw, round_index, observations):
        from ...protocol import profile as files
        run_id = self.claim["decisionInput"]["profileRunId"]
        reference = files.publish(self.state_dir, run_id, {"answer": raw, "observations": observations})
        attempt = self.claim["attempt"]
        # The service owns submitted round files until the final receipt; a
        # structurally valid answer is still pending its tool-evidence judgment.
        response = self.client.call("execution_profile", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": self.nonce, "round": round_index, "answer": reference})
        return files.read(self.state_dir, run_id, response["feedback"])

    def _wait_control_observation(self, claim, control, retry):
        control.raise_fatal()
        if control.cancel_view is not None or self.stop_requested():
            raise BoardError('ATTEMPT_FINISHED', 'Stop requested while waiting for a control observation')
        self.hint(waitingReason=make_reason('router-input-unknown'))
        control.wait(min(PREFLIGHT_MAX_RETRY_SECONDS, PREFLIGHT_RETRY_SECONDS * 2 ** min(retry, 4)))
        control.raise_fatal()
        if control.cancel_view is not None or self.stop_requested():
            raise BoardError('ATTEMPT_FINISHED', 'Stop requested while waiting for a control observation')

    def _nonce(self) -> str:
        return self.nonce

    def receipt(self, claim: dict, report: dict, directory: Path) -> dict:
        attempt = claim["attempt"]
        receipt = {
            "version": 2,
            "attemptId": attempt["attemptId"],
            "taskId": attempt["taskId"],
            "generation": attempt["generation"],
            "report": report,
            "createdAt": _now(),
        }
        write_receipt(directory, receipt)
        self.log(f"durable completion receipt written for attempt {attempt['attemptId']}")
        return receipt


class _HintFlusher(threading.Thread):
    """The execution's one progress reporter, on its own thread.

    A suspended service or connection can hold only this thread: the control
    thread keeps applying stops and recording activity while the flusher is
    inside ``client.progress``, and a refused hint becomes the execution's
    fatal error through the control's failure channel.
    """

    def __init__(self, execution):
        super().__init__(name="hey-my-buddy-execution-hints", daemon=True)
        self.execution = execution
        self._wake = threading.Event()
        self._done = threading.Event()

    def wake(self):
        self._wake.set()

    def stop(self):
        self._done.set()
        self._wake.set()

    def run(self):
        try:
            while not self._done.is_set():
                self.execution.flush_hints()
                self._wake.wait(0.25)
                self._wake.clear()
        except BaseException as error:  # noqa: BLE001 - the refusal must end the run
            self.execution.control.fail(error)


class _ExecutionControl(threading.Thread):
    """Observe only local native activity and received signals; no lease or stop polling."""
    def __init__(self, execution):
        super().__init__(name="hey-my-buddy-execution-control", daemon=True)
        self.execution = execution
        self._done = threading.Event()
        self._wake = threading.Event()
        self._interrupted = threading.Event()
        self._lock = threading.RLock()
        self.handle = self.implementation = None
        self.cancel_view = None
        self.deadline = None
        self.requests = 0
        self.applied = 0
        self._activity = None
        self.fatal: BaseException | None = None

    def raise_fatal(self):
        if self.fatal is not None:
            raise self.fatal

    def fail(self, error):
        # The hint flusher reports a refused hint here; the next checkpoint of
        # the main flow raises it exactly like the control loop's own failure.
        self.fatal = error
        self._interrupted.set()
        self._wake.set()

    def request_stop(self, initiator, deadline, *, force=False, from_signal=False):
        # Signal handlers only update lock-free local facts; the control thread
        # sets the condition-backed events and runs native cancellation.
        if self.cancel_view is not None and not force:
            if (self.deadline is None or initiator in WRAP_UP_INITIATORS
                    or initiator == self.cancel_view["stopInitiator"] == "pause"):
                return
        self.requests += 2 if force else 1
        self.cancel_view = {"cancelRequested": True, "stopInitiator": initiator}
        self.deadline = min(self.deadline, deadline) if self.deadline is not None and deadline is not None else deadline
        if not from_signal:
            self._interrupted.set()
            self._wake.set()

    def attach(self, handle, implementation):
        with self._lock:
            self.handle, self.implementation = handle, implementation
            self.applied = 0
            self._activity = None
        self._wake.set()

    def detach(self):
        """Retire the live handle once its native work has settled.

        A stop request arriving after the collected result is complete must not
        reach ``worker_stop``/``worker_cancel`` and distort that result.
        """
        with self._lock:
            self.handle = self.implementation = None

    def wait(self, seconds):
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            if self.fatal is not None or self.cancel_view is not None or self.execution.stop_requested():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._interrupted.wait(min(0.25, remaining))

    def run(self):
        try:
            self._run_control()
        except BaseException as error:
            self.fail(error)

    def _run_control(self):
        # This loop never touches the transport: reporting a hint is the
        # flusher thread's work, so a suspended service or connection can
        # never delay a stop or the light activity collection here.
        while not self._done.is_set():
            with self._lock:
                handle, implementation = self.handle, self.implementation
                # Retiring a handle waits for any stop already being applied.
                if handle is not None and self.requests > self.applied:
                    requested = self.requests
                    if self.applied or requested > 1:
                        handle.stop_initiator = self.cancel_view["stopInitiator"]
                        role_seam.worker_cancel(implementation, handle)
                    else:
                        role_seam.worker_stop(implementation, handle,
                            stop_initiator=self.cancel_view["stopInitiator"], grace_deadline=self.deadline)
                    self.applied = requested
            if handle is not None:
                try:
                    endpoint = getattr(handle, "live_endpoint", None)
                    if endpoint is not None:
                        activity = endpoint.current_activity()
                        if activity is not None and activity_module.is_newer(activity, self._activity):
                            self.execution.hint(activity=activity)
                            self._activity = activity
                except Exception as error:
                    # A failed live observation never disables native stop handling.
                    self.execution.log(f"live activity unavailable: {error!r}")
            self._wake.wait(0.25)
            self._wake.clear()

    def stop(self):
        self._done.set()
        self._interrupted.set()
        self._wake.set()


def main():
    configure_utf8_output()
    if not os.environ.get("HMB_STATE_DIR"):
        sys.stderr.write("hey-my-buddy execution: HMB_STATE_DIR is required\n")
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--startup", required=True)
    args = parser.parse_args()
    from ...private_dirs import open_regular_fd
    with os.fdopen(open_regular_fd(Path(args.startup), os.O_RDONLY), 'rb') as stream:
        material = decode_strict_json(stream.read())
    validate_claim_turn(material["claim"])
    execution = Execution(material["claim"], Path(material["stateDir"]), material["nonce"],
                          session_record_path=material["sessionRecordPath"],
                          log=lambda message: print(message, flush=True))
    def interrupted(_signal, _frame):
        execution.request_stop("unknown", None, force=True, from_signal=True)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, interrupted)
    for pending in _early_signals:
        execution.request_stop("unknown", None, force=True)
    _early_signals.clear()
    from ...private_dirs import ensure_private_dir
    ensure_private_dir(execution.directory)
    ensure_private_dir(execution.evidence_directory)
    if os.name == "nt":
        # The marker is published before this process was resumed; it names the
        # created root (the venv forwarder), whose Job also covers this interpreter.
        root_identity = execution_protocol.execution_root_identity(material)
        identity = execution_protocol.self_process_identity(root_identity["jobName"])
    else:
        identity = execution_protocol.self_process_identity(material.get("jobName"))
        root_identity = identity
    from ..roles.run_execution import set_execution_live_server
    set_execution_live_server(execution.live)
    try:
        execution.live.start(root_identity)
        execution._identity = identity
        execution._identity_pending = identity
        execution.run()
    finally:
        try:
            execution.live.finish()
        finally:
            set_execution_live_server(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
