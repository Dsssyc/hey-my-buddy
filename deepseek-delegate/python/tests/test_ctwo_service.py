"""Real cross-process C-Two calls with an owned mock execution engine; no model."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import c_two as cc

from buddy.contracts import BuddyControl, CompletionInbox
from buddy.transport import CONTROL_NAME, INBOX_NAME, ServiceError, call_service


FAKE_ENGINE = r"""
import readline from 'node:readline';
import fs from 'node:fs';
import path from 'node:path';
const file = path.join(process.env.BUDDY_STATE_DIR, 'fake-runs.json');
let runs = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file,'utf8')) : [];
const save = () => fs.writeFileSync(file, JSON.stringify(runs));
const send = value => process.stdout.write(JSON.stringify(value)+'\n');
const input = readline.createInterface({input:process.stdin});
input.on('line', line => {
  const {id,method,params:p} = JSON.parse(line);
  try {
    let result;
    if(method==='health') result={status:'ready',pid:process.pid};
    else if(method==='wait') {setTimeout(()=>send({id,result:{status:'wait-finished'}}),p.timeoutMs||0);return;}
    else if(method==='list') result={runs:runs.slice(p.offset||0,(p.offset||0)+(p.limit||20)),total:runs.length};
    else if(method==='start') {
      result=runs.find(r=>r.requestId===p.requestId);
      if(!result) {
        result={runId:'run-'+p.requestId,requestId:p.requestId,status:'running',resultAvailable:false,revision:1};
        runs.push(result);save();
        const run=result;
        setTimeout(()=>{run.status='completed';run.resultAvailable=true;run.revision++;save();send({event:'run_completed',run});send({event:'run_completed',run});},250);
      }
    } else if(method==='stop') {send({id,result:{status:'stopped'}});setTimeout(()=>process.exit(0),30);return;}
    else {result=runs.find(r=>r.runId===p.runId);if(!result) throw Object.assign(new Error('not found'),{code:'NOT_FOUND'});}
    send({id,result});
  } catch(e) {send({id,error:{code:e.code||'ERROR',message:e.message}});}
});
input.on('close',()=>process.exit(0));
"""


def eventually(check):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.04)
    raise AssertionError("Timed out waiting for condition")


class Inbox:
    def __init__(self, acknowledge=None):
        self.events = []
        self.lock = threading.Lock()
        self.acknowledge = acknowledge

    def submit(self, event_json: str) -> str:
        with self.lock:
            event = json.loads(event_json)
            self.events.append(event)
        if self.acknowledge:
            self.acknowledge(event)
        return json.dumps({"status": "received"})


class CTwoServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-ctwo-")
        self.directory = Path(self.temp.name)
        self.engine = self.directory / "fake-engine.mjs"
        self.engine.write_text(FAKE_ENGINE)
        self.environment = patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory), "BUDDY_ENGINE_PATH": str(self.engine), "C2_ENV_FILE": "", "C2_RELAY_ANCHOR_ADDRESS": ""})
        self.environment.start()

    def tearDown(self):
        endpoint_file = self.directory / "control.json"
        if endpoint_file.exists():
            try:
                call_service("stop", state_dir=self.directory)
            except ServiceError:
                pass
            eventually(lambda: not endpoint_file.exists())
        cc.shutdown()
        self.environment.stop()
        self.temp.cleanup()

    def test_cross_process_concurrent_clients_idempotency_notification_and_auth(self):
        inbox = Inbox()
        cc.register(CompletionInbox, inbox, name=INBOX_NAME, concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
        binding = {"address": cc.server_address(), "token": "notification-secret", "threadId": "isolated-test-thread"}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            health = list(pool.map(lambda _: call_service("health", state_dir=self.directory), range(3)))
        self.assertEqual(len({item["pid"] for item in health}), 1)
        endpoint = json.loads((self.directory / "control.json").read_text())
        self.assertNotEqual(endpoint["pid"], os.getpid())
        self.assertEqual((self.directory / "control.json").stat().st_mode & 0o777, 0o600)
        with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as service:
            denied = json.loads(service.dispatch(json.dumps({"method": "health", "params": {}})))
        self.assertEqual(denied["error"]["code"], "UNAUTHORIZED")
        params = {"requestId": "one", "cwd": str(self.directory), "task": "mock", "_notify": binding}
        first = call_service("start", params, self.directory)
        repeated = call_service("start", params, self.directory)
        self.assertEqual(first["runId"], repeated["runId"])
        with self.assertRaises(ServiceError) as conflict:
            call_service("start", {**params, "_notify": {**binding, "threadId": "another-thread"}}, self.directory)
        self.assertEqual(conflict.exception.code, "CONFLICT")
        eventually(lambda: len(inbox.events) == 1)
        status = eventually(lambda: (s if (s := call_service("status", {"runId": first["runId"]}, self.directory))["notification"]["status"] == "received" else None))
        self.assertNotIn("token", status["notification"])
        self.assertNotIn("address", status["notification"])
        self.assertEqual(inbox.events[0]["eventId"], first["runId"])
        with self.assertRaises(ServiceError) as denied:
            call_service("notification_ack", {"runId": first["runId"], "token": "wrong", "status": "submitted"}, self.directory)
        self.assertEqual(denied.exception.code, "UNAUTHORIZED")
        accepted = call_service("notification_ack", {"runId": first["runId"], "token": binding["token"], "status": "submitted"}, self.directory)
        self.assertEqual(accepted["notification"]["status"], "submitted")
        self.assertEqual(len(inbox.events), 1)

    def test_client_exit_does_not_stop_run_and_service_restart_preserves_it(self):
        code = "from buddy.transport import call_service; import json; print(json.dumps(call_service('start', {'requestId':'detached','task':'mock','cwd':'.'})))"
        child = subprocess.run([sys.executable, "-c", code], env=dict(os.environ), capture_output=True, text=True, timeout=25)
        self.assertEqual(child.returncode, 0, child.stderr)
        run = json.loads(child.stdout)
        completed = eventually(lambda: (s if (s := call_service("status", {"runId": run["runId"]}, self.directory))["resultAvailable"] else None))
        self.assertEqual(completed["status"], "completed")
        endpoint = self.directory / "control.json"
        call_service("stop", state_dir=self.directory)
        eventually(lambda: not endpoint.exists())
        self.assertEqual(call_service("result", {"runId": run["runId"]}, self.directory)["status"], "completed")

    def test_live_legacy_service_prevents_second_engine(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as legacy:
            legacy.bind(str(self.directory / "service.sock"))
            legacy.listen()
            with self.assertRaises(ServiceError) as failure:
                call_service("health", state_dir=self.directory)
            self.assertEqual(failure.exception.code, "SERVICE_START_FAILED")
            self.assertFalse((self.directory / "fake-runs.json").exists())

    def test_stop_missing_service_does_not_spawn(self):
        self.assertTrue(call_service("stop", state_dir=self.directory)["alreadyStopped"])
        self.assertFalse((self.directory / "control.json").exists())
        self.assertFalse((self.directory / "control.log").exists())

    def test_new_service_rejects_old_client_and_legacy_bind(self):
        call_service("health", state_dir=self.directory)
        address = str(self.directory / "service.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as old_client:
            old_client.settimeout(2)
            old_client.connect(address)
            old_client.sendall(b'{"id":"old","method":"health","params":{}}\n')
            reply = json.loads(old_client.recv(8192))
        self.assertEqual(reply["error"]["code"], "MIGRATED")
        self.assertEqual(reply["id"], "old")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as old_daemon:
            with self.assertRaises(OSError):
                old_daemon.bind(address)

    def test_stale_legacy_socket_is_preserved_and_blocks_start(self):
        address = self.directory / "service.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
            stale.bind(str(address))
        identity = address.stat().st_ino
        with self.assertRaises(ServiceError):
            call_service("health", state_dir=self.directory)
        self.assertEqual(address.stat().st_ino, identity)
        self.assertFalse((self.directory / "fake-runs.json").exists())

    def test_notification_rebind_only_before_first_delivery(self):
        inbox = Inbox()
        cc.register(CompletionInbox, inbox, name=INBOX_NAME)
        params = {"requestId": "rebind", "cwd": str(self.directory), "task": "mock", "_notify": {"address": "ipc://retired-receiver", "token": "old-token", "threadId": "same-thread"}}
        run = call_service("start", params, self.directory)
        params["_notify"] = {"address": cc.server_address(), "token": "new-token", "threadId": "same-thread"}
        self.assertEqual(call_service("start", params, self.directory)["runId"], run["runId"])
        eventually(lambda: len(inbox.events) == 1)
        self.assertEqual(inbox.events[0]["token"], "new-token")
        eventually(lambda: call_service("status", {"runId": run["runId"]}, self.directory)["notification"]["status"] == "received")
        params["_notify"] = {"address": "ipc://another-receiver", "token": "third-token", "threadId": "same-thread"}
        recovered = call_service("start", params, self.directory)
        self.assertEqual(recovered["runId"], run["runId"])
        self.assertEqual(recovered["notification"]["status"], "received")
        self.assertEqual(len(inbox.events), 1)

    def test_recovery_delivers_registered_terminal_run_once_and_never_replays_uncertain(self):
        inbox = Inbox()
        cc.register(CompletionInbox, inbox, name=INBOX_NAME)
        notifications = self.directory / "notifications"
        notifications.mkdir(mode=0o700)
        runs = []
        for request_id, status in [("registered", "registered"), ("uncertain", "dispatching")]:
            run_id = "run-" + request_id
            runs.append({"runId": run_id, "requestId": request_id, "status": "completed", "resultAvailable": True})
            record = {"requestId": request_id, "runId": run_id, "status": status, "address": cc.server_address(), "token": "secret", "threadId": "test-thread"}
            path = notifications / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")
            path.write_text(json.dumps(record))
        (self.directory / "fake-runs.json").write_text(json.dumps(runs))
        call_service("health", state_dir=self.directory)
        eventually(lambda: len(inbox.events) == 1)
        self.assertEqual(inbox.events[0]["requestId"], "registered")
        uncertain = call_service("status", {"runId": "run-uncertain"}, self.directory)
        self.assertEqual(uncertain["notification"]["status"], "unknown")
        endpoint = self.directory / "control.json"
        call_service("stop", state_dir=self.directory)
        eventually(lambda: not endpoint.exists())
        call_service("health", state_dir=self.directory)
        self.assertEqual(len(inbox.events), 1)

    def test_submitted_acknowledgement_before_receipt_is_not_overwritten(self):
        def acknowledge(event):
            call_service("notification_ack", {"runId": event["runId"], "token": event["token"], "status": "submitted"}, self.directory)
        inbox = Inbox(acknowledge)
        cc.register(CompletionInbox, inbox, name=INBOX_NAME, concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
        run = call_service("start", {"requestId": "ack-race", "task": "mock", "cwd": str(self.directory), "_notify": {"address": cc.server_address(), "token": "race-secret", "threadId": "test-thread"}}, self.directory)
        eventually(lambda: (s := call_service("status", {"runId": run["runId"]}, self.directory))["notification"]["status"] == "submitted")
        self.assertEqual(len(inbox.events), 1)


if __name__ == "__main__":
    unittest.main()
