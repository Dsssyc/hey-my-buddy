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

    def _run(self, connection, task_id):
        return connection.execute("SELECT * FROM maintenance_runs WHERE task_id=?", (task_id,)).fetchone()

    def trigger(self, *, manual=False):
        with self.store.db.write() as connection:
            response, _delay = self._schedule(connection, manual=manual)
            head = self.store._head_of(connection)
        self.store._notify(head)
        return response

    def collect(self):
        with self.store.db.write() as connection:
            _response, delay = self._schedule(connection, manual=False)
            head = self.store._head_of(connection)
        self.store._notify(head)
        return delay

    def _schedule(self, connection, *, manual):
        now = self.store.now()
        settings = connection.execute("SELECT * FROM router_settings WHERE id=1").fetchone()
        interval = settings["maintenance_interval_seconds"]
        active = connection.execute("SELECT m.* FROM maintenance_runs m WHERE m.state='running' OR EXISTS"
            " (SELECT 1 FROM (SELECT * FROM attempts WHERE " + self.store.UNRESOLVED_SQL + ") a"
            " WHERE a.task_id=m.task_id) ORDER BY m.started_at LIMIT 1").fetchone()
        if active is not None:
            return {"status": "running", "maintenanceRunId": active["maintenance_run_id"]}, None
        # An explicit empty-board request reports that fact regardless of setup.
        has_pending = maintenance_data.pending(connection)
        if not has_pending:
            return {"status": "no-new-evidence"}, interval
        buddy_id = settings["maintenance_buddy_id"]
        if buddy_id is None:
            return {"status": "not-configured"}, None
        latest = connection.execute("SELECT * FROM maintenance_runs ORDER BY started_at DESC,rowid DESC LIMIT 1").fetchone()
        if latest is not None and not manual:
            # Successful batches drain immediately. An invalid batch waits from
            # its actual finish; missed intervals are never replayed.
            finish = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='maintenance.finished' ORDER BY seq DESC LIMIT 1",
                                        (latest["task_id"],)).fetchone()
            drain = (latest["state"] == "completed" and has_pending and finish is not None
                     and json.loads(finish[0])["continuePending"])
            if not drain:
                remaining = interval - (datetime.fromisoformat(now) - datetime.fromisoformat(latest["finished_at"])).total_seconds()
                if remaining > 0:
                    return {"status": "waiting"}, remaining
        buddy, code, facts = router.buddy_problem(connection, buddy_id, "fast", now=now)
        if buddy is None:
            return {"status": "unavailable", "reason": {"code": code, "params": facts}}, interval
        packet = maintenance_data.build_packet(connection)
        run_id = "maint-" + str(uuid.uuid4())
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
                           " VALUES(?,?,?,?,'running',?)", (run_id, "manual" if manual else "interval", buddy_id, task_id, now))
        self.store._append_event(connection, "maintenance.started", task_id=task_id,
                                 payload={"maintenanceRunId": run_id, "trigger": "manual" if manual else "interval"})
        return {"status": "running", "maintenanceRunId": run_id, "runId": task_id}, None

    def prepare_claim(self, connection, task, now):
        run = self._run(connection, task["task_id"])
        if run["state"] != "running":
            return "decision-closed"
        buddy, code, facts = router.buddy_problem(connection, run["buddy_id"], "fast", now=now)
        if buddy is not None:
            return None
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
            task = connection.execute("SELECT spec_json FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            document = json.loads(task[0])["decision"]["document"]
            packet = files.read(self.store.directory, run["maintenance_run_id"], document["input"])["packet"]
            feedback = maintenance_data.apply_answer(connection, packet=packet, answer=material["answer"],
                                                     maintenance_run_id=run["maintenance_run_id"])
            reference = files.publish(self.store.directory, run["maintenance_run_id"], feedback)
            self.store._append_event(connection, "maintenance.answer_checked", task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"], payload={"round": round_index, "answer": params["answer"], "feedback": reference})
            head = self.store._head_of(connection)
        self.store._notify(head)
        return {"feedback": reference}

    def finish(self, connection, *, task_id, now, code=None, params=None):
        run = self._run(connection, task_id)
        if run is None or run["state"] != "running":
            return None
        connection.execute("UPDATE maintenance_runs SET state=?,reason_code=?,reason_params_json=?,finished_at=? WHERE task_id=?",
                           ("failed" if code else "completed", code, canonical_json(params or {}) if code else None, now, task_id))
        self.store._append_event(connection, "maintenance.finished", task_id=task_id,
            payload={"maintenanceRunId": run["maintenance_run_id"], "status": "failed" if code else "completed", "code": code,
                     "continuePending": code is None and maintenance_data.pending(connection)})
        return {"maintenanceRunId": run["maintenance_run_id"], "status": "failed" if code else "completed"}

    def complete(self, connection, *, task, attempt, status, result, shutdown_confirmed, error, now):
        run = self._run(connection, task["task_id"])
        if run is None or run["state"] != "running":
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
        event = connection.execute("SELECT payload_json FROM events WHERE attempt_id=? AND kind='maintenance.answer_checked' ORDER BY seq DESC LIMIT 1",
                                   (attempt["attempt_id"],)).fetchone()
        params = {}
        if event is not None:
            feedback_ref = json.loads(event[0])["feedback"]
            feedback = files.read(self.store.directory, run["maintenance_run_id"], feedback_ref)
            if feedback["errors"]:
                params["feedback"] = feedback_ref
                code = code or "answer-shape"
        elif code is None:
            code = "router-no-answer"
        return self.finish(connection, task_id=task["task_id"], now=now, code=code, params=params)


class Scheduler:
    def __init__(self, store):
        self.store = store
        self.thread = threading.Thread(target=self.run, name="hey-my-buddy-maintenance", daemon=True)

    def run(self):
        try:
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
