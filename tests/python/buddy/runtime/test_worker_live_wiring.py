"""The real Worker main loop's four live wirings (DSH native resume, C1).

The live runtime itself was proven by ADR-025 step five; these cases pin only
that the loop is wired to it, driving the loop's own entries — the renewal
tick, ``_renew``/``_recover``, ``Worker.run`` and ``Worker.execute`` — and
never ``WorkerLiveRuntime`` directly. Each wiring was additionally verified
red against a source copy with exactly that call removed.

The tick cases reuse ``LiveActivityForwardTests`` verbatim through subclassing,
extended with the board calls the wirings add (a client that records
``live_attach``/``live_detach``) and a legal nonce seeded into the renewal
thread's spool intent. The endpoint-closure cases run real controller peers in
their own processes — each leading its own session, because one process hosts
exactly one C-Two socket file — and after the assertions the fixture recycles
exactly the two endpoints it created: the owned file through the production
cleanup primitive once its group is observed gone, the foreign peer through its
own clean stop, with every per-object fact journalled beside the run. Verified
boundary: no board daemon, no harness binary and no model; the Worker's own
C-Two server and the peers' sockets are the real transport.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import AdapterOutcome, ProcessHandle
from hey_my_buddy.buddy.harnesses.c_two_live import (
    C_TWO_IPC_DIRECTORY,
    ConfirmedProcessGone,
    LiveEndpointDescriptor,
    cleanup_abandoned_socket,
)
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.buddy.runtime import worker as worker_module
from hey_my_buddy.buddy.runtime.worker import Worker

from buddy.runtime import test_worker_invariants as invariants

PEER = Path(__file__).resolve().parent / "fixtures" / "live_runtime_peer.py"


class WorkerLiveWiringTests(invariants.LiveActivityForwardTests):
    """V-C1/V-C2: the first ready tick registers; renew and reconcile re-register.

    The donor class contributes its fixture only; its own cases keep running in
    their own module (a non-callable attribute is not collected by the loader).
    """

    test_activity_flows_through_the_channel_and_never_repeats = None
    test_a_foreign_attempts_endpoint_is_never_forwarded = None
    test_a_stored_request_of_another_invocation_never_binds_or_bypasses = None
    test_an_unextracted_command_has_no_live_activity_or_file_fallback = None
    test_a_channel_unavailability_is_not_a_stop = None

    class RecordingClient(invariants.LiveActivityForwardTests.RecordingClient):
        """The donor's board client plus the live-registry calls the wirings make."""

        def __init__(self):
            super().__init__()
            self.live_attachments = []
            self.live_detachments = []
            self.renew_calls = []
            self.reconcile_calls = []
            self.next_renew = {}

        def live_attach(self, attachment):
            self.live_attachments.append(attachment)
            return {"attached": True}

        def live_detach(self, withdrawal):
            self.live_detachments.append(withdrawal)
            return {"detached": True}

        def renew(self, worker_id, attempt_id, generation, nonce, **kwargs):
            self.renew_calls.append({"workerId": worker_id, "attemptId": attempt_id,
                                     "generation": generation, "nonce": nonce})
            return dict(self.next_renew)

        def reconcile(self, worker_id, attempt_id, generation, nonce, **kwargs):
            self.reconcile_calls.append({"workerId": worker_id, "attemptId": attempt_id,
                                         "generation": generation, "nonce": nonce})
            return {"attempt": {"executionState": "executing"}}

    def renewal(self, *, with_request=True, harness="zcode", task_id="task-live",
                attempt_id="attempt-live", generation=1):
        # The donor fixture builds its worker without a startup intent, so the
        # renewal thread would present an empty nonce; seed the same worker's
        # spool first with the legal nonce these wirings act under.
        from hey_my_buddy.protocol.client import new_nonce

        self.legal_nonce = new_nonce()
        worker_module.fsync_json(
            worker_module.ReceiptSpool(self.state, "w-live").startup_path,
            {"workerId": "w-live", "instanceId": "w-live-wiring-fixture",
             "nonce": self.legal_nonce, "claimRequestId": "claim-live-wiring",
             "attemptId": None, "createdAt": worker_module._now()},
        )
        return super().renewal(with_request=with_request, harness=harness, task_id=task_id,
                               attempt_id=attempt_id, generation=generation)

    def test_the_first_ready_tick_registers_the_live_binding_once(self):
        renewal, directory = self.renewal()
        client = renewal.worker.client
        self.addCleanup(renewal.stop)
        # The runtime's own recycling: an unregistered name cannot collide
        # with a later test's still-registered endpoint (a duplicate random
        # person name makes the next register fail and bind swallow it).
        self.addCleanup(renewal.worker.live.stop)
        self.assertEqual(client.live_attachments, [], "nothing is registered before a ready tick")
        renewal._forward_activity()
        self.assertEqual(len(client.live_attachments), 1)
        attachment = client.live_attachments[0]
        self.assertEqual((attachment.worker_id, attachment.attempt_id, attachment.generation),
                         ("w-live", "attempt-live", 1))
        self.assertEqual(attachment.nonce, self.legal_nonce)
        self.assertEqual(attachment.identity, renewal.handle.role_run_identity)
        renewal._forward_activity()
        self.assertEqual(len(client.live_attachments), 1,
                         "a controller found ready is registered on its first tick only")

    def test_a_successful_renew_registers_the_same_binding_again(self):
        renewal, directory = self.renewal()
        client = renewal.worker.client
        self.addCleanup(renewal.stop)
        # The runtime's own recycling: an unregistered name cannot collide
        # with a later test's still-registered endpoint (a duplicate random
        # person name makes the next register fail and bind swallow it).
        self.addCleanup(renewal.worker.live.stop)
        renewal._forward_activity()
        self.assertEqual(len(client.live_attachments), 1)
        self.assertTrue(renewal._renew())
        self.assertEqual(client.renew_calls[0]["nonce"], self.legal_nonce)
        self.assertEqual(len(client.live_attachments), 2,
                         "a renewed lease re-registers the holder's live binding")
        self.assertIs(client.live_attachments[1], client.live_attachments[0],
                      "the re-registration is the very same holder attachment")

    def test_a_successful_reconcile_registers_the_same_binding_again(self):
        renewal, directory = self.renewal()
        client = renewal.worker.client
        self.addCleanup(renewal.stop)
        # The runtime's own recycling: an unregistered name cannot collide
        # with a later test's still-registered endpoint (a duplicate random
        # person name makes the next register fail and bind swallow it).
        self.addCleanup(renewal.worker.live.stop)
        renewal._forward_activity()
        self.assertEqual(len(client.live_attachments), 1)
        # The service demands reconciliation while the child handle is still
        # live: the recovery path reconciles and re-registers with one identity.
        renewal.handle.shutdown_confirmed = lambda: False
        client.next_renew = {"reconciliationRequired": True}
        self.assertTrue(renewal._renew())
        self.assertEqual(client.reconcile_calls[0]["nonce"], self.legal_nonce)
        self.assertEqual(len(client.live_attachments), 2,
                         "a reconciled attempt re-registers the holder's live binding")
        self.assertIs(client.live_attachments[1], client.live_attachments[0])


class LoopRecordingClient(WorkerLiveWiringTests.RecordingClient):
    """The wiring client plus the worker-loop calls ``Worker.run`` itself makes."""

    def __init__(self):
        super().__init__()
        self.registrations = []
        self.claims = []

    def register_worker(self, worker_id, **kwargs):
        self.registrations.append(worker_id)
        return {"workerId": worker_id}

    def claim(self, worker_id, claim_request_id, nonce, **kwargs):
        self.claims.append({"workerId": worker_id, "claimRequestId": claim_request_id,
                            "nonce": nonce})
        return {"reason": "no-queued-work"}


class EndingControllerHandle:
    """The holder's live handle over the owned ProcessHandle of a controller peer.

    ``wait`` first holds the attempt open until the worker's real renewal tick
    has registered the binding, then ends the controller's whole owned group
    through the project's own stop mechanism — abruptly, with no clean C-Two
    stop — so its captured socket file is left behind exactly as a controller
    that died on its own leaves it. ``shutdown_confirmed`` is the real
    group-gone observation of that ProcessHandle, never the leader's exit.
    """

    def __init__(self, control, identity, owned: ProcessHandle, client):
        self.role_run_control = control
        self.role_run_identity = identity
        self.owned = owned
        # The fields the spawn marker, the live seam and the end path read off
        # a handle; the group identity is the one captured while the peer leads
        # its own session.
        self.process = owned.process
        self.pid = owned.pid
        self.pgid = owned.pgid
        self.cancel_requested = False
        self._client = client

    def wait(self, timeout=None):
        if not self.owned.finished:
            deadline = time.monotonic() + 2.0
            while not self._client.live_attachments and time.monotonic() < deadline:
                time.sleep(0.01)
            self.owned.terminate(grace_seconds=1.0)
        return self.owned.wait(timeout)

    def shutdown_confirmed(self):
        return self.owned.shutdown_confirmed()


class EndingControllerExecutor:
    name = "fixture-controller"

    def __init__(self, handle):
        self.handle = handle

    def available(self):
        return True, None

    def prepare(self, context):
        return None

    def start(self, context):
        return self.handle

    def collect(self, handle, context):
        return AdapterOutcome(status="ok", result={"status": "ok", "modelStarted": True},
                              shutdown_confirmed=handle.shutdown_confirmed())

    def cancel(self, handle, *, grace_seconds=None):
        handle.cancel_requested = True
        handle.owned.terminate(grace_seconds=grace_seconds if grace_seconds is not None else 3.0)


class WorkerEndpointClosureTests(unittest.TestCase):
    """V-C3/V-C4: exit closes the Worker's own endpoint; a reaped owned
    controller's captured socket is cleared and nobody else's file is touched."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-worker-endpoint-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.work = self.root / "work"
        self.work.mkdir()
        # Retained private materials (peer journals and stderr); no teardown.
        self.materials = Path(tempfile.mkdtemp(
            prefix="live-wiring-", dir=os.environ.get("BUDDY_CHECKS_TMPDIR", tempfile.gettempdir())))

    def controller_peer(self, identity, label):
        """Start a session-leading controller peer; read its real endpoint facts.

        The peer leads its own session and process group, so the fixture's stop
        and group-gone observation address exactly the group it created. A peer
        whose start exchange fails is ended and closed here — its handle is
        never left behind for a failing assertion to lose.
        """
        environment = {key: value for key, value in os.environ.items()
                       if key not in invariants.SANITIZED_VARIABLES}
        environment.update(C2_RELAY_ANCHOR_ADDRESS="", C2_ENV_FILE="", PYTHONDONTWRITEBYTECODE="1")
        stderr = (self.materials / f"{label}-stderr.log").open("x")
        process = subprocess.Popen([sys.executable, str(PEER), "controller"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr, text=True, env=environment,
                                   start_new_session=True)
        owned = ProcessHandle(process, own_group=True, log_paths={})
        try:
            command = {"op": "start", "identity": identity.to_payload(),
                       "journal": str(self.materials / f"{label}-journal.jsonl")}
            process.stdin.write(canonical_json(command) + "\n")
            process.stdin.flush()
            reply = json.loads(process.stdout.readline())
            if not reply.get("ok"):
                raise RuntimeError(f"the controller peer {label!r} did not start")
        except BaseException:
            owned.terminate(grace_seconds=1.0)
            self.close_peer_pipes(process, stderr)
            raise
        return {"label": label, "process": process, "owned": owned, "stderr": stderr,
                "token": reply["token"],
                "descriptor": LiveEndpointDescriptor.from_payload(reply["descriptor"])}

    def controller_pair(self, identity):
        """Two real peers; a failure while creating the second ends the first."""
        owned = self.controller_peer(identity, "owned")
        try:
            foreign_identity = identity.model_copy(update={"task_id": "task-foreign",
                                                           "attempt_id": "attempt-foreign"})
            foreign = self.controller_peer(foreign_identity, "foreign")
        except BaseException:
            self.reclaim_owned_endpoint(owned)
            raise
        return owned, foreign

    @staticmethod
    def close_peer_pipes(process, stderr):
        process.stdin.close()
        process.stdout.close()
        stderr.close()

    def record_disposition(self, record: dict) -> None:
        """Leave this run's exact per-object creation/stop/socket facts."""
        with (self.materials / "endpoint-dispositions.jsonl").open("a") as journal:
            journal.write(json.dumps(record, sort_keys=True) + "\n")

    def reclaim_owned_endpoint(self, peer) -> dict:
        """End this test's peer and clear exactly its captured endpoint file.

        The socket identity is the one this peer's own descriptor captured at
        creation, and the file is touched only through the production cleanup
        primitive — never by scanning the shared directory — and only after the
        group this fixture created is observed gone.
        """
        owned = peer["owned"]
        if not owned.finished:
            owned.terminate(grace_seconds=1.0)
        exit_code = owned.process.poll()
        group_gone = owned.shutdown_confirmed()
        cleanup = cleanup_abandoned_socket(
            peer["descriptor"], ConfirmedProcessGone(pid=owned.pid, exit_code=exit_code,
                                                     group_gone=group_gone))
        self.close_peer_pipes(peer["process"], peer["stderr"])
        record = {"peer": peer["label"], "pid": owned.pid, "pgid": owned.pgid,
                  "exitCode": exit_code, "groupGone": group_gone,
                  "socketPath": peer["descriptor"].socket.path,
                  "socketExistsAfter": Path(peer["descriptor"].socket.path).exists(),
                  "outcome": cleanup.outcome, "reason": cleanup.reason}
        self.record_disposition(record)
        return record

    def stop_foreign_peer(self, peer) -> dict:
        """The other real peer ends through its own clean C-Two lifecycle."""
        process = peer["process"]
        stopped_cleanly = False
        try:
            if process.poll() is None:
                process.stdin.write('{"op": "stop"}\n')
                process.stdin.flush()
                process.wait(timeout=10)
            stopped_cleanly = process.poll() is not None
        except Exception:
            stopped_cleanly = False
        if stopped_cleanly:
            self.close_peer_pipes(process, peer["stderr"])
            record = {"peer": peer["label"], "pid": process.pid, "pgid": peer["owned"].pgid,
                      "returnCode": process.poll(),
                      "socketPath": peer["descriptor"].socket.path,
                      "socketExistsAfter": Path(peer["descriptor"].socket.path).exists()}
            self.record_disposition(record)
            return record
        # A peer that would not stop cleanly is ended and its captured endpoint
        # cleared exactly like the owned one; this test leaves nothing behind.
        return self.reclaim_owned_endpoint(peer)

    @unittest.skipUnless(os.name == "posix", "the recorded C-Two socket layout is POSIX")
    def test_worker_exit_closes_its_own_c_two_socket(self):
        worker = Worker("w-exit", self.state, client=LoopRecordingClient(), log=invariants.silent)
        descriptor = worker.live.start()
        socket = Path(C_TWO_IPC_DIRECTORY) / (descriptor.address.removeprefix("ipc://") + ".sock")
        self.addCleanup(worker.live.stop)
        self.assertTrue(socket.is_socket(), "a started Worker runtime owns a live C-Two socket")
        worker.run(max_iterations=1)
        self.assertEqual(worker.client.registrations, ["w-exit"])
        self.assertEqual(len(worker.client.claims), 1, "the loop really ran one claim round")
        self.assertFalse(socket.exists(),
                         "the Worker main loop's exit path closes its own C-Two endpoint")

    @unittest.skipUnless(os.name == "posix", "the recorded C-Two socket layout is POSIX")
    def test_controller_end_through_execute_clears_only_the_captured_socket(self):
        from hey_my_buddy.buddy.harnesses.run_contract import (
            FrozenJson, PrivateStatePaths, RunBudget, RunConfiguration, RunIdentity, RunRequest,
            encode_run_request,
        )
        from hey_my_buddy.protocol.client import new_nonce

        client = LoopRecordingClient()
        worker = Worker("w-end", self.state, client=client, log=invariants.silent)
        task_id, attempt_id = "task-end", "attempt-end"
        identity = RunIdentity(task_id=task_id, attempt_id=attempt_id, generation=1,
                               invocation_id="invocation-end", turn_id="turn-end",
                               input_sha256="a" * 64)
        worker_module.fsync_json(
            worker_module.ReceiptSpool(self.state, "w-end").startup_path,
            {"workerId": "w-end", "instanceId": "w-end-fixture", "nonce": new_nonce(),
             "claimRequestId": "claim-end", "attemptId": None,
             "createdAt": worker_module._now()},
        )
        directory = self.state / "attempts" / task_id / attempt_id
        directory.mkdir(mode=0o700, parents=True)
        request_file = directory / "role-run-request.json"
        request = RunRequest(
            identity=identity, harness="zcode",
            configuration=RunConfiguration(provider="fixture-zcode", model="fixture-glm",
                                            effort="low"),
            cwd=str(self.work),
            private_state=PrivateStatePaths(invocation_root=str(directory),
                                            native_root=str(directory / "native")),
            input_text="the governed turn input", tool_scope="write",
            output_schema=FrozenJson({"type": "object"}),
            budget=RunBudget(timeout_seconds=600))
        worker_module.fsync_json(request_file, json.loads(encode_run_request(request)))
        owned, foreign = self.controller_pair(identity)
        recycling = []
        try:
            owned_socket = Path(owned["descriptor"].socket.path)
            foreign_socket = Path(foreign["descriptor"].socket.path)
            self.assertTrue(owned_socket.is_socket())
            self.assertTrue(foreign_socket.is_socket())
            ready_file = directory / "live-ready.json"
            ready_file.write_text(json.dumps(owned["descriptor"].to_payload()))
            control = {"operation": "worker", "harness": "zcode", "requestFile": str(request_file),
                       "directory": str(directory),
                       "live": {"readyFile": str(ready_file),
                                "instanceId": owned["descriptor"].instance_id,
                                "token": owned["token"]}}
            handle = EndingControllerHandle(control, identity, owned["owned"], client)
            claim = {"attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": 1},
                     "task": {"taskId": task_id,
                              "spec": {"adapter": "command", "cwd": str(self.work),
                                       "task": "controller end wiring", "timeoutSeconds": 30}}}
            self.addCleanup(worker.live.stop)
            with mock.patch.object(worker_module.role_seam, "worker_executor",
                                   return_value=EndingControllerExecutor(handle)):
                receipt = worker.execute(claim)
            self.assertEqual(receipt["report"]["status"], "ok")
            self.assertEqual(len(client.live_attachments), 1,
                             "the renewal thread's real tick registered the controller binding")
            self.assertEqual(client.live_attachments[0].attempt_id, attempt_id)
            self.assertEqual(len(client.live_detachments), 1,
                             "the execute end path released the binding with the board")
            self.assertFalse(owned_socket.exists(),
                             "the reaped owned controller's captured socket file was cleared")
            self.assertTrue(foreign_socket.is_socket(),
                            "another holder's socket file in the shared IPC directory survives")
        finally:
            # After the assertions, in every outcome: recycle exactly the two
            # endpoints this test created. The owned file is cleared through the
            # production primitive once its group is observed gone; the foreign
            # peer ends through its own clean C-Two stop. Nothing is scanned.
            recycling.append(self.reclaim_owned_endpoint(owned))
            recycling.append(self.stop_foreign_peer(foreign))
        self.assertIn(recycling[0]["outcome"], ("already-absent", "deleted"))
        self.assertIs(recycling[0]["groupGone"], True)
        self.assertIsNotNone(recycling[0]["exitCode"])
        self.assertFalse(recycling[0]["socketExistsAfter"],
                         "the fixture leaves no endpoint file of its own behind")
        self.assertIsNotNone(recycling[1]["returnCode"])
        self.assertFalse(recycling[1]["socketExistsAfter"],
                         "the foreign peer's own stop removed its endpoint file")


if __name__ == "__main__":
    unittest.main()
