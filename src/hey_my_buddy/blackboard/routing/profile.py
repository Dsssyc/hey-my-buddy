"""User-triggered public model profiles on the ordinary execution queue."""
from __future__ import annotations

import json
import threading
import uuid

from ...errors import BoardError
from ...json_codec import decode_strict_json
from ...protocol import profile as files, schemas
from ...protocol.owned_process import WorkingDirectoryRejected
from ...json_codec import canonical_json
from . import profile_data, router


class Profile:
    def __init__(self, store):
        self.store = store
        self._file_cleanup_lock = threading.RLock()

    def _run(self, connection, task_id):
        return connection.execute('SELECT * FROM profile_runs WHERE task_id=?', (task_id,)).fetchone()

    def readiness(self, connection):
        buddy_id = connection.execute('SELECT profile_buddy_id FROM router_settings WHERE id=1').fetchone()[0]
        if buddy_id is None:
            return None, {"code": "profile-not-configured", "params": {}}
        buddy, code, params = router.buddy_problem(connection, buddy_id, 'profile', now=self.store.now())
        return buddy, {"code": code, "params": params} if code else None

    def trigger(self, model_id):
        with self.store.db.write() as connection:
            if connection.execute('SELECT 1 FROM models WHERE model_id=?', (model_id,)).fetchone() is None:
                raise BoardError('NOT_FOUND', 'Unknown modelId', modelId=model_id)
            buddy, problem = self.readiness(connection)
            if problem:
                return {"status": "unavailable", "reason": problem}
            active = connection.execute(
                'SELECT p.* FROM profile_runs p WHERE p.model_id=? AND (p.state=\'running\' OR EXISTS('
                'SELECT 1 FROM attempts a WHERE a.task_id=p.task_id AND (' + self.store.UNRESOLVED_SQL + '))) ORDER BY p.rowid DESC LIMIT 1',
                (model_id,)).fetchone()
            if active is not None:
                return {"status": "running", "profileRunId": active['profile_run_id'], "runId": active['task_id'],
                        "buddyId": active['buddy_id']}
            settings = connection.execute('SELECT profile_buddy_id,model_response_wait_seconds FROM router_settings WHERE id=1').fetchone()
            run_id = 'profile-' + str(uuid.uuid4())
            self.store.db.after_rollback(connection, lambda: self._discard_inputs(run_id))
            packet = profile_data.build_packet(connection, model_id)
            reference = files.publish(self.store.directory, run_id, {"packet": packet, "outputSchema": profile_data.answer_schema()})
            document = {"kind": "profile", "profileRunId": run_id, "buddyId": settings['profile_buddy_id'],
                        "buddy": {key: buddy[key] for key in schemas.BUDDY_FIELDS}, "routingMode": "profile",
                        "modelResponseWaitSeconds": settings['model_response_wait_seconds'], "input": reference}
            now = self.store.now()
            task_id = self.store.decisions._create_task(connection, decision_id=run_id, request_id=run_id,
                model_response_wait_seconds=document['modelResponseWaitSeconds'], now=now, kind='profile', document=document)
            connection.execute('INSERT INTO profile_runs(profile_run_id,model_id,buddy_id,task_id,state,started_at) VALUES(?,?,?,?,\'running\',?)',
                               (run_id, model_id, settings['profile_buddy_id'], task_id, now))
            self.store._append_event(connection, 'profile.started', task_id=task_id,
                                     payload={"profileRunId": run_id, "modelId": model_id, "buddyId": settings['profile_buddy_id']})
            head = self.store._head_of(connection)
        self.store._notify(head)
        return {"status": "running", "profileRunId": run_id, "runId": task_id,
                "buddyId": settings['profile_buddy_id'], "buddy": document['buddy'], "usesQuota": True}

    def prepare_claim(self, connection, task, now):
        run = self._run(connection, task['task_id'])
        if run['state'] != 'running':
            return 'decision-closed'
        configured = connection.execute('SELECT profile_buddy_id FROM router_settings WHERE id=1').fetchone()[0]
        if configured == run['buddy_id']:
            buddy, code, params = router.buddy_problem(connection, configured, 'profile', now=now)
            if buddy is not None:
                return None
        else:
            code, params = 'router-input-changed', {"setting": "profileBuddyId"}
        self.finish(connection, task_id=task['task_id'], now=now, code=code, params=params)
        self.store.decisions._cancel_queued_task(connection, task, now)
        return 'decision-closed'

    def answer(self, params):
        schemas.reject_unknown(params, {"attemptId", "generation", "nonce", "round", "answer"}, 'execution.profile')
        round_index = schemas.optional_int(params, 'round', None, 0, 2)
        if round_index is None:
            raise BoardError('INVALID_ARGUMENT', 'round is required')
        with self.store.db.write() as connection:
            attempt = self.store._verify_attempt_actor(connection, params)
            run = self._run(connection, attempt['task_id'])
            if run is None:
                raise BoardError('FORBIDDEN', 'Only a profile execution may submit profile answers')
            if run['state'] != 'running' or attempt['execution_state'] not in ('starting', 'executing'):
                raise BoardError('ATTEMPT_FINISHED', 'Profile execution is no longer running')
            history = [json.loads(row[0]) for row in connection.execute(
                "SELECT payload_json FROM events WHERE attempt_id=? AND kind='profile.answer_checked' ORDER BY seq", (attempt['attempt_id'],))]
            if round_index < len(history):
                previous = history[round_index]
                if previous['answer'] != params.get('answer'):
                    raise BoardError('CONFLICT', 'This profile round already has another answer')
                return {"feedback": previous['feedback']}
            if round_index != len(history):
                raise BoardError('CONFLICT', 'Profile correction rounds must be sequential')
            if history and files.read(self.store.directory, run['profile_run_id'], history[-1]['feedback'])['accepted']:
                raise BoardError('CONFLICT', 'This profile already has an accepted answer')
            material = files.read(self.store.directory, run['profile_run_id'], params.get('answer'))
            if (not isinstance(material, dict) or set(material) != {'answer', 'observations'}
                    or not isinstance(material['observations'], dict)
                    or set(material['observations']) != {'toolCalls', 'toolMarkerFrames', 'deniedInteractions'}
                    or any(type(v) is not int or v < 0 for v in material['observations'].values())):
                raise BoardError('INVALID_ARGUMENT', 'Profile answer requires observed tool facts')
            def retain_reference(reference):
                if not any(reference in (item['answer'], item['feedback']) for item in history):
                    self.store.db.after_rollback(connection, lambda: files.discard_reference(self.store.directory, run['profile_run_id'], reference))
            retain_reference(params['answer'])
            spec = json.loads(connection.execute('SELECT spec_json FROM tasks WHERE task_id=?', (attempt['task_id'],)).fetchone()[0])
            document = spec['decision']['document']
            packet = files.read(self.store.directory, run['profile_run_id'], document['input'])['packet']
            _answer, errors = profile_data.validate_answer(packet, material['answer'])
            feedback = {"accepted": not errors, "errors": errors}
            reference = files.publish(self.store.directory, run['profile_run_id'], feedback,
                before_write=retain_reference)
            connection.execute('UPDATE profile_runs SET correction_rounds=? WHERE profile_run_id=?', (round_index, run['profile_run_id']))
            self.store._append_event(connection, 'profile.answer_checked', task_id=attempt['task_id'], attempt_id=attempt['attempt_id'],
                                     payload={"round": round_index, "answer": params['answer'], "feedback": reference})
            head = self.store._head_of(connection)
        self.store._notify(head)
        return {"feedback": reference}

    def _discard_inputs(self, run_id):
        with self._file_cleanup_lock:
            files.discard(self.store.directory, run_id)

    def recover_files(self):
        with self.store.db.write() as connection, self._file_cleanup_lock:
            active = [row[0] for row in connection.execute("SELECT profile_run_id FROM profile_runs WHERE state='running'")]
            files.discard_orphans(self.store.directory, active)

    def _usage(self, connection, task_id):
        usage = []
        for attempt in connection.execute('SELECT attempt_id,result_json FROM attempts WHERE task_id=? ORDER BY generation', (task_id,)):
            result = (json.loads(attempt['result_json']).get('result') or {}) if attempt['result_json'] else {}
            observed = result.get('usage') or {}
            usage.append({"attemptId": attempt['attempt_id'], "elapsedMs": observed.get('elapsedMs'),
                          "toolCalls": observed.get('toolCalls'), "tokenUsage": result.get('tokenUsage')})
        return {"attempts": usage}

    def finish(self, connection, *, task_id, now, code=None, params=None):
        run = self._run(connection, task_id)
        if run['state'] != 'running':
            return None
        usage = self._usage(connection, task_id)
        state = 'failed' if code else 'completed'
        connection.execute('UPDATE profile_runs SET state=?,reason_code=?,reason_params_json=?,finished_at=?,usage_json=? WHERE task_id=?',
                           (state, code, canonical_json(params or {}) if code else None, now, canonical_json(usage), task_id))
        self.store.db.after_commit(connection, lambda: self._discard_inputs(run['profile_run_id']))
        self.store._append_event(connection, 'profile.finished', task_id=task_id,
                                 payload={"profileRunId": run['profile_run_id'], "status": state, "code": code})
        return {"profileRunId": run['profile_run_id'], "status": state}

    def complete(self, connection, *, task, attempt, status, result, shutdown_confirmed, error, now):
        run = self._run(connection, task['task_id'])
        if run['state'] != 'running':
            # A fenced execution may deliver its usage only when its later
            # receipt proves shutdown; preserve the failure and record that usage.
            connection.execute('UPDATE profile_runs SET usage_json=? WHERE task_id=?',
                               (canonical_json(self._usage(connection, task['task_id'])), task['task_id']))
            return None
        output = result if isinstance(result, dict) else {}
        correction_count = output.get('correctionCount')
        if type(correction_count) is int and 0 <= correction_count <= 2:
            connection.execute('UPDATE profile_runs SET correction_rounds=? WHERE task_id=?', (correction_count, task['task_id']))
        code = output.get('code') or error
        if status != 'ok' or output.get('status') != 'ok':
            code = code or 'router-no-answer'
        if not shutdown_confirmed or attempt['shutdown_confirmed'] != 1:
            code = 'router-stop-unconfirmed'
        params = ({'seconds': json.loads(task['spec_json'])['modelResponseWaitSeconds']}
                  if code == 'router-not-responding' else {})
        checked = None
        if code in (None, 'profile-answer-invalid', 'router-no-answer'):
            event = connection.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND attempt_id=? AND kind='profile.answer_checked' ORDER BY seq DESC LIMIT 1",
                (task['task_id'], attempt['attempt_id'])).fetchone()
            if event is None:
                code = 'router-no-answer'
            else:
                checked = json.loads(event[0])
                feedback = files.read(self.store.directory, run['profile_run_id'], checked['feedback'])
                if not feedback['accepted']:
                    code, params = 'profile-answer-invalid', {"errors": feedback['errors']}
        if code is None:
            document = json.loads(task['spec_json'])['decision']['document']
            problem = self.store.decisions._tool_evidence_problem(
                {"decision_task_id": task['task_id'], "decision_attempt_id": attempt['attempt_id'], "decision_generation": attempt['generation']}, document, output)
            if problem:
                code = problem['code']
        if code is None:
            # The accepted round is immutable input. Publish only after the
            # service has judged the complete attempt's tool and shutdown facts.
            material = files.read(self.store.directory, run['profile_run_id'], checked['answer'])
            answer = material['answer']
            if isinstance(answer, str):
                answer = decode_strict_json(answer)
            profile_data.apply_answer(connection, model_id=run['model_id'], run_id=run['profile_run_id'], answer=answer, now=now)
        if code == WorkingDirectoryRejected.code:
            params = WorkingDirectoryRejected.reason_params(output.get('reason'))
        return self.finish(connection, task_id=task['task_id'], now=now, code=code, params=params)
