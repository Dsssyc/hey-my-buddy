"""Worker endpoint lifetime and exact cleanup of one reaped execution endpoint."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import c_two as cc
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.c_two_live import (
    ConfirmedProcessGone,
    LiveEndpointDescriptor,
    cleanup_owned_endpoint,
)
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.buddy.runtime.worker import Worker

from buddy.runtime import test_worker_invariants as invariants

PEER = Path(__file__).resolve().parent / "fixtures" / "live_runtime_peer.py"


class RecordingClient(invariants.LiveActivityForwardTests.RecordingClient):
    def __init__(self):
        super().__init__()
        self.live_attachments = []
        self.live_detachments = []

    def live_attach(self, attachment):
        self.live_attachments.append(attachment)
        return {"attached": True}

    def live_detach(self, withdrawal):
        self.live_detachments.append(withdrawal)
        return {"detached": True}


class LoopRecordingClient(RecordingClient):
    """The wiring client plus the worker-loop calls ``Worker.run`` itself makes."""

    def __init__(self):
        super().__init__()
        self.registrations = []
        self.claims = []

    def register_worker(self, worker_id, **kwargs):
        self.registrations.append(worker_id)
        return {"workerId": worker_id}

    def call(self, operation, params):
        if operation == "worker_recover":
            return {"candidates": []}
        raise AssertionError(f"unexpected worker call: {operation}")

    def claim(self, worker_id, claim_request_id, nonce, **kwargs):
        self.claims.append({"workerId": worker_id, "claimRequestId": claim_request_id,
                            "nonce": nonce})
        return {"reason": "no-queued-work", "cursor": 0}




class WorkerEndpointClosureTests(unittest.TestCase):
    """V-C3/V-C4: exit closes the Worker's own endpoint; a reaped owned
    controller's owned endpoint is reaped and foreign endpoints survive."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-worker-endpoint-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.work = self.root / "work"
        self.work.mkdir()
        # Retained private materials (peer journals and stderr); no teardown.
        self.materials = Path(tempfile.mkdtemp(
            prefix="live-wiring-", dir=os.environ.get("HMB_CHECKS_TMPDIR", tempfile.gettempdir())))

    def controller_peer(self, identity, label):
        """Start a session-leading controller peer; read its real endpoint facts.

        The peer leads its own session and process group, so the fixture's stop
        and group-gone observation address exactly the group it created. A peer
        whose start exchange fails is ended and closed here — its handle is
        never left behind for a failing assertion to lose.
        """
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
                       and not key.startswith(("HMB_", "ANTHROPIC_", "C2_"))}
        environment.update(HMB_STATE_DIR=str(self.state),
                           HMB_RUNTIME_ROOT=str(self.materials / "runtime"), TMPDIR=str(self.materials))
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

        The native credential is the one this peer's own descriptor captured at
        creation, and the endpoint is reaped only through the production cleanup
        primitive — never by scanning the shared directory — and only after the
        group this fixture created is observed gone.
        """
        owned = peer["owned"]
        if not owned.finished:
            owned.terminate(grace_seconds=1.0)
        exit_code = owned.process.poll()
        group_gone = owned.shutdown_confirmed()
        cleanup = getattr(peer.get("releaseHandle"), "role_endpoint_cleanup", None)
        if cleanup is None:
            cleanup = cleanup_owned_endpoint(
                peer["descriptor"], ConfirmedProcessGone(pid=owned.pid, exit_code=exit_code,
                                                         group_gone=group_gone),
                context=cc.local_endpoint_context(root=str(self.state / "ipc")))
        self.close_peer_pipes(peer["process"], peer["stderr"])
        record = {"peer": peer["label"], "pid": owned.pid, "pgid": owned.pgid,
                  "exitCode": exit_code, "groupGone": group_gone,
                  "address": peer["descriptor"].address,
                  "endpointStatusAfter": cc.inspect_endpoint(peer["descriptor"].address,
                      root=str(self.state / "ipc"))["status"],
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
                      "address": peer["descriptor"].address,
                      "endpointStatusAfter": cc.inspect_endpoint(peer["descriptor"].address,
                          root=str(self.state / "ipc"))["status"]}
            self.record_disposition(record)
            return record
        # A peer that would not stop cleanly is ended and its captured endpoint
        # cleared exactly like the owned one; this test leaves nothing behind.
        return self.reclaim_owned_endpoint(peer)

    @unittest.skipUnless(os.name == "posix", "native local endpoint maintenance is POSIX")
    def test_worker_exit_closes_its_own_c_two_endpoint(self):
        worker = Worker("w-exit", self.state, client=LoopRecordingClient(), log=invariants.silent)
        descriptor = worker.live.start()

        self.addCleanup(worker.live.stop)
        self.assertEqual(cc.inspect_endpoint(descriptor.address)["status"], "present")
        worker.run(max_iterations=1)
        self.assertEqual(worker.client.registrations, ["w-exit"])
        self.assertEqual(len(worker.client.claims), 1, "the loop really ran one claim round")
        self.assertEqual(cc.inspect_endpoint(descriptor.address)["status"], "absent",
                         "the Worker main loop closes its own C-Two endpoint")

    @unittest.skipUnless(os.name == "posix", "native local endpoint maintenance is POSIX")
    def test_reaped_execution_reaps_only_its_owned_endpoint(self):
        from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity

        Worker("w-end", self.state, client=LoopRecordingClient(), log=invariants.silent)
        task_id, attempt_id = "task-end", "attempt-end"
        identity = RunIdentity(task_id=task_id, attempt_id=attempt_id, generation=1,
                               invocation_id="invocation-end", turn_id="turn-end",
                               input_sha256="a" * 64)
        directory = self.state / "attempts" / task_id / attempt_id
        directory.mkdir(mode=0o700, parents=True)
        owned, foreign = self.controller_pair(identity)
        recycling = []
        try:
            self.assertEqual(cc.inspect_endpoint(owned["descriptor"].address)["status"], "present")
            self.assertEqual(cc.inspect_endpoint(foreign["descriptor"].address)["status"], "present")
            from hey_my_buddy.buddy.roles.live import release_live_binding
            live_file = directory / "execution-live.json"
            live_file.write_text(json.dumps({"identity": identity.model_dump(by_alias=True),
                                             "harness": "zcode", "descriptor": owned["descriptor"].to_payload(),
                                             "token": owned["token"]}))
            handle = owned["owned"]
            handle.role_run_control = {"harness": "zcode"}
            handle.role_run_identity = identity
            handle.execution_live_file = str(live_file)
            owned["releaseHandle"] = handle
            handle.terminate(grace_seconds=1.0)
            self.assertIsNotNone(handle.process.poll())
            self.assertIs(handle.shutdown_confirmed(), True)
            with mock.patch.object(cc, "reap_endpoint", wraps=cc.reap_endpoint) as reap:
                cleanup = release_live_binding(handle)
            reap.assert_called_once()
            self.assertIn(cleanup.outcome, ("already-absent", "reaped"))
            self.assertEqual(cc.inspect_endpoint(owned["descriptor"].address)["status"], "absent",
                             "the reaped controller's own endpoint was reaped")
            self.assertEqual(cc.inspect_endpoint(foreign["descriptor"].address)["status"], "present",
                             "another holder's endpoint in the private IPC domain survives")
        finally:
            # After the assertions, in every outcome: recycle exactly the two
            # endpoints this test created. The owned file is cleared through the
            # production primitive once its group is observed gone; the foreign
            # peer ends through its own clean C-Two stop. Nothing is scanned.
            recycling.append(self.reclaim_owned_endpoint(owned))
            recycling.append(self.stop_foreign_peer(foreign))
        self.assertIn(recycling[0]["outcome"], ("already-absent", "reaped"))
        self.assertIs(recycling[0]["groupGone"], True)
        self.assertIsNotNone(recycling[0]["exitCode"])
        self.assertEqual(recycling[0]["endpointStatusAfter"], "absent",
                         "the fixture leaves no endpoint file of its own behind")
        self.assertIsNotNone(recycling[1]["returnCode"])
        self.assertEqual(recycling[1]["endpointStatusAfter"], "absent",
                         "the foreign peer's own stop removed its endpoint file")


if __name__ == "__main__":
    unittest.main()
