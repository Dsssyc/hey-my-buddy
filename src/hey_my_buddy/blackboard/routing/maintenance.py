"""One maintenance task at a time on the ordinary execution queue."""
from __future__ import annotations

from datetime import datetime
import json
import threading
import uuid

from ...errors import BoardError
from ...protocol import maintenance as files, schemas
from ..store.db import canonical_json
from . import maintenance_data, router


class Maintenance:
    def __init__(self, store):
        self.store = store
        self._check_signature = None
        self._check_after = None
        self._file_cleanup_lock = threading.RLock()

    def _run(self, connection, task_id):
        return connection.execute("SELECT * FROM maintenance_runs WHERE task_id=?", (task_id,)).fetchone()

    def trigger(self, *, manual=False):
        response, _delay = self._request(manual=manual)
        return response

    def collect(self):
        _response, delay = self._request(manual=False)
        return delay

    @staticmethod
    def _latest(connection):
        return connection.execute("SELECT * FROM maintenance_runs ORDER BY rowid DESC LIMIT 1").fetchone()

    def _active(self, connection, latest):
        if latest is None:
            return None
        if latest["state"] == "running":
            return latest
        unresolved = connection.execute(
            "SELECT 1 FROM attempts WHERE task_id=? AND (" + self.store.UNRESOLVED_SQL + ") LIMIT 1",
            (latest["task_id"],)).fetchone()
        return latest if unresolved is not None else None

    def _readiness(self, connection, *, manual):
        settings = connection.execute("SELECT * FROM router_settings WHERE id=1").fetchone()
        interval = settings["maintenance_interval_seconds"]
        if settings["maintenance_buddy_id"] is None:
            return {"status": "not-configured"}, None, None
        latest = self._latest(connection)
        active = self._active(connection, latest)
        if active is not None:
            return {"status": "running", "maintenanceRunId": active["maintenance_run_id"]}, None, None
        signature = (settings["maintenance_buddy_id"], interval,
                     latest["maintenance_run_id"] if latest else None,
                     latest["finished_at"] if latest else None)
        if signature != self._check_signature:
            self._check_signature, self._check_after = signature, None
        now = self.store.now()
        instant = datetime.fromisoformat(now).timestamp()
        if not manual:
            deadline = self._check_after
            if latest is not None:
                event = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='maintenance.finished' ORDER BY seq DESC LIMIT 1",
                                           (latest["task_id"],)).fetchone()
                drain = latest["state"] == "completed" and event is not None and json.loads(event[0])["continuePending"]
                if not drain:
                    interval_deadline = datetime.fromisoformat(latest["finished_at"]).timestamp() + interval
                    deadline = interval_deadline if deadline is None else max(deadline, interval_deadline)
            if deadline is not None and instant < deadline:
                return {"status": "waiting"}, deadline - instant, None
        buddy, code, facts = router.buddy_problem(connection, settings["maintenance_buddy_id"], "fast", now=now)
        if buddy is None:
            if not manual:
                self._check_after = instant + interval
            return {"status": "unavailable", "reason": {"code": code, "params": facts}}, interval, None
        return None, None, (dict(settings), buddy)

    def _request(self, *, manual):
        # Only the cheap settings/deadline read runs on event wakeups. The
        # evidence scan and packet construction run once when this check is due,
        # outside the writer lock.
        with self.store.db.read() as connection:
            response, delay, readiness = self._readiness(connection, manual=manual)
            if response is not None:
                return response, delay
            settings, buddy = readiness
            packet = maintenance_data.build_packet(connection)
        if packet is None:
            interval = settings["maintenance_interval_seconds"]
            if not manual:
                self._check_after = datetime.fromisoformat(self.store.now()).timestamp() + interval
            return {"status": "no-new-evidence"}, interval
        with self.store.db.write() as connection:
            response, delay = self._admit(connection, settings=settings, buddy=buddy, packet=packet,
                                          trigger_type="manual" if manual else "interval")
            head = self.store._head_of(connection)
        self.store._notify(head)
        return response, delay

    def _admit(self, connection, *, settings, buddy, packet, trigger_type):
        current = connection.execute("SELECT maintenance_buddy_id,maintenance_interval_seconds FROM router_settings WHERE id=1").fetchone()
        if tuple(current) != (settings["maintenance_buddy_id"], settings["maintenance_interval_seconds"]):
            return {"status": "settings-changed"}, 0
        active = self._active(connection, self._latest(connection))
        if active is not None:
            return {"status": "running", "maintenanceRunId": active["maintenance_run_id"]}, None
        buddy_id = settings["maintenance_buddy_id"]
        now = self.store.now()
        run_id = "maint-" + str(uuid.uuid4())
        self.store.db.after_rollback(connection, lambda: self._discard_inputs(run_id))
        reference = files.publish(self.store.directory, run_id, {
            "packet": packet, "outputSchema": maintenance_data.answer_schema(packet)})
        identity = {key: buddy[key] for key in schemas.BUDDY_FIELDS}
        budget = router.configured_budget(connection)
        document = {"kind": "maintain", "maintenanceRunId": run_id, "buddyId": buddy_id,
                    "buddy": identity, "routingMode": "fast", "budget": budget, "input": reference}
        task_id = self.store.decisions._create_task(
            connection, decision_id=run_id, request_id=run_id,
            router_budget_seconds=budget["timeoutSeconds"], now=now,
            kind="maintain", document=document)
        connection.execute("INSERT INTO maintenance_runs(maintenance_run_id,trigger_type,buddy_id,task_id,state,started_at)"
                           " VALUES(?,?,?,?,'running',?)", (run_id, trigger_type, buddy_id, task_id, now))
        self.store._append_event(connection, "maintenance.started", task_id=task_id,
                                 payload={"maintenanceRunId": run_id, "trigger": trigger_type})
        return {"status": "running", "maintenanceRunId": run_id, "runId": task_id}, None

    def prepare_claim(self, connection, task, now):
        run = self._run(connection, task["task_id"])
        if run["state"] != "running":
            return "decision-closed"
        configured = connection.execute("SELECT maintenance_buddy_id FROM router_settings WHERE id=1").fetchone()[0]
        if configured == run["buddy_id"]:
            buddy, code, facts = router.buddy_problem(connection, run["buddy_id"], "fast", now=now)
            if buddy is not None:
                return None
        else:
            code, facts = "router-input-changed", {"setting": "maintenanceBuddyId"}
        self.finish(connection, task_id=task["task_id"], now=now, code=code, params=facts)
        self.store.decisions._cancel_queued_task(connection, task, now)
        return "decision-closed"

    def answer(self, params):
        schemas.reject_unknown(params, {"attemptId", "generation", "nonce", "round", "answer"}, "execution.maintenance")
        round_index = schemas.optional_int(params, "round", None, 0, 2)
        if round_index is None:
            raise BoardError("INVALID_ARGUMENT", "round is required")
        with self.store.db.write() as connection:
            attempt = self.store._verify_attempt_actor(connection, params)
            run = self._run(connection, attempt["task_id"])
            if run is None:
                raise BoardError("FORBIDDEN", "Only a maintenance execution may submit maintenance answers")
            if run["state"] != "running" or attempt["execution_state"] not in ("starting", "executing"):
                raise BoardError("ATTEMPT_FINISHED", "Maintenance execution is no longer running")
            history = connection.execute("SELECT payload_json FROM events WHERE attempt_id=? AND kind='maintenance.answer_checked' ORDER BY seq",
                                         (attempt["attempt_id"],)).fetchall()
            if round_index < len(history):
                previous = json.loads(history[round_index][0])
                if previous["answer"] != params.get("answer"):
                    raise BoardError("CONFLICT", "This maintenance round already has another answer")
                return {"feedback": previous["feedback"]}
            if round_index != len(history):
                raise BoardError("CONFLICT", "Maintenance correction rounds must be sequential")
            material = files.read(self.store.directory, run["maintenance_run_id"], params.get("answer"))
            if (not isinstance(material, dict) or set(material) != {"answer", "observations"}
                    or material["observations"] != {"toolCalls": 0, "toolMarkerFrames": 0, "deniedInteractions": 0}
                    or any(type(value) is not int for value in material["observations"].values())):
                raise BoardError("INVALID_ARGUMENT", "Maintenance answer requires observed zero-tool facts")
            bound = [json.loads(row[0]) for row in history]
            if not any(params["answer"] in (item["answer"], item["feedback"]) for item in bound):
                self.store.db.after_rollback(connection, lambda: files.discard_reference(
                    self.store.directory, run["maintenance_run_id"], params["answer"]))
            task = connection.execute("SELECT spec_json FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            document = json.loads(task[0])["decision"]["document"]
            packet = files.read(self.store.directory, run["maintenance_run_id"], document["input"])["packet"]
            feedback = maintenance_data.apply_answer(connection, packet=packet, answer=material["answer"],
                                                     maintenance_run_id=run["maintenance_run_id"])
            def retain_feedback(reference):
                if not any(reference in (item["answer"], item["feedback"]) for item in bound):
                    self.store.db.after_rollback(connection, lambda: files.discard_reference(
                        self.store.directory, run["maintenance_run_id"], reference))
            reference = files.publish(self.store.directory, run["maintenance_run_id"], feedback, before_write=retain_feedback)
            self.store._append_event(connection, "maintenance.answer_checked", task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"], payload={"round": round_index, "answer": params["answer"], "feedback": reference})
            head = self.store._head_of(connection)
        self.store._notify(head)
        return {"feedback": reference}

    def _discard_inputs(self, run_id):
        with self._file_cleanup_lock:
            files.discard(self.store.directory, run_id)

    def _discard_run_files(self, run_id, harness, task_id, attempt_ids):
        with self._file_cleanup_lock:
            self._discard_inputs(run_id)
            self._discard_native_files(harness, task_id, attempt_ids)

    def _discard_native_files(self, harness, task_id, attempt_ids):
        from ...private_dirs import attempt_root, remove_tree
        from ...paths import native_path

        for attempt_id in attempt_ids:
            # Keep the execution receipt, live endpoint and tool proof. Native
            # no-tool sessions contain the packet and answers and are transient.
            root = native_path(attempt_root(self.store.directory, harness, task_id, attempt_id))
            for invocation in root.glob("no-tool-*"):
                remove_tree(invocation)

    def recover_files(self):
        # The writer excludes admissions whose input file is published before
        # its run row commits. Active executions' directories are preserved.
        with self.store.db.write() as connection, self._file_cleanup_lock:
            runs = connection.execute("SELECT m.*,json_extract(t.spec_json,'$.decision.document.buddy.harness') AS harness"
                                      " FROM maintenance_runs m JOIN tasks t ON t.task_id=m.task_id").fetchall()
            files.discard_orphans(self.store.directory, [row["maintenance_run_id"] for row in runs if row["state"] == "running"])
            for run in runs:
                if run["state"] != "running":
                    stopped = [row[0] for row in connection.execute("SELECT attempt_id FROM attempts WHERE task_id=? AND shutdown_confirmed=1", (run["task_id"],))]
                    self._discard_native_files(run["harness"], run["task_id"], stopped)

    def finish(self, connection, *, task_id, now, code=None, params=None):
        run = self._run(connection, task_id)
        if run["state"] != "running":
            return None
        params = dict(params or {})
        event = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='maintenance.answer_checked' ORDER BY seq DESC LIMIT 1",
                                   (task_id,)).fetchone()
        if event is not None:
            feedback_ref = json.loads(event[0])["feedback"]
            feedback = files.read(self.store.directory, run["maintenance_run_id"], feedback_ref)
            if code is None and feedback["errors"]:
                code = "maintenance-answer-invalid"
                params = {"errors": feedback["errors"]}
        elif code is None:
            code = "router-no-answer"
        connection.execute("UPDATE maintenance_runs SET state=?,reason_code=?,reason_params_json=?,finished_at=? WHERE task_id=?",
                           ("failed" if code else "completed", code, canonical_json(params) if code else None, now, task_id))
        harness = connection.execute("SELECT json_extract(spec_json,'$.decision.document.buddy.harness') FROM tasks WHERE task_id=?",
                                     (task_id,)).fetchone()[0]
        stopped = [row[0] for row in connection.execute("SELECT attempt_id FROM attempts WHERE task_id=? AND shutdown_confirmed=1", (task_id,))]
        self.store.db.after_commit(connection, lambda: self._discard_run_files(run["maintenance_run_id"], harness, task_id, stopped))
        self.store._append_event(connection, "maintenance.finished", task_id=task_id,
            payload={"maintenanceRunId": run["maintenance_run_id"], "status": "failed" if code else "completed", "code": code,
                     "continuePending": code is None and maintenance_data.pending(connection)})
        return {"maintenanceRunId": run["maintenance_run_id"], "status": "failed" if code else "completed"}

    def complete(self, connection, *, task, attempt, status, result, shutdown_confirmed, error, now):
        run = self._run(connection, task["task_id"])
        if run["state"] != "running":
            return None
        output = result if isinstance(result, dict) else {}
        code = output.get("code") or error
        if status != "ok" or output.get("status") != "ok":
            code = code or "router-no-answer"
        if not shutdown_confirmed or attempt["shutdown_confirmed"] != 1:
            code = "router-stop-unconfirmed"
        if code is None:
            document = json.loads(task["spec_json"])["decision"]["document"]
            problem = self.store.decisions._tool_evidence_problem(
                {"decision_task_id": task["task_id"], "decision_attempt_id": attempt["attempt_id"],
                 "decision_generation": attempt["generation"]}, document, output)
            if problem:
                code = problem["code"]
        return self.finish(connection, task_id=task["task_id"], now=now, code=code)


class Scheduler:
    def __init__(self, store):
        self.store = store
        self.thread = threading.Thread(target=self.run, name="hey-my-buddy-maintenance", daemon=True)

    def run(self):
        try:
            self.store.maintenance.recover_files()
            while True:
                with self.store._condition:
                    if self.store._wait_stopped:
                        return
                    head = self.store._head
                delay = self.store.maintenance.collect()
                with self.store._condition:
                    self.store._condition.wait_for(
                        lambda: self.store._wait_stopped or self.store._head != head, timeout=delay)
        except Exception as error:
            self.store.persistence_error = f"Maintenance scheduler: {type(error).__name__}: {error}"
