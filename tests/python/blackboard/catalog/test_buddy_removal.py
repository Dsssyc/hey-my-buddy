"""Catalog removal through the console authority and current service projections."""
import json

from blackboard.catalog.test_catalog_trust import reading
from blackboard.tasks.test_workflow import WorkflowTestCase
from support import FakeClock
from hey_my_buddy.blackboard.evaluation.models import current_model
from hey_my_buddy.console.reads import console_snapshot_projection
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.reasons import reason


ALPHA_HIGH = 'dsh:fixture:alpha:high'
ALPHA_MAX = 'dsh:fixture:alpha:max'
BETA_HIGH = 'dsh:fixture:beta:high'
BETA_MAX = 'dsh:fixture:beta:max'
ALPHA_SERVICE = {'harness': 'dsh', 'provider': 'fixture', 'model': 'alpha'}


class BuddyRemovalTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.b = self.board(clock=self.clock)
        self.e = self.b.evaluation
        self.e.record_catalog(reading(models=('alpha', 'beta'), efforts=('high', 'max')))

    def remove(self, *buddy_ids, command_id='remove'):
        return self.b.console_call('buddy_remove', {'commandId': command_id, 'buddyIds': list(buddy_ids)})

    def buddy_page(self):
        page = self.b.call('buddy_list', {'includeUnavailable': True})
        return {buddy['buddyId']: buddy for buddy in page['buddies']}

    def assert_removal(self, buddy_id, expected):
        self.assertEqual(self.buddy_page()[buddy_id].get('removal'), expected)

    def assert_refused(self, code, buddy_id, cause=None):
        head = self.b.store.head()
        with self.assertRaises(BoardError) as caught:
            self.remove(BETA_MAX, buddy_id)
        self.assertEqual(caught.exception.code, code)
        expected = {'buddyId': buddy_id}
        if cause:
            expected['reason'] = reason(cause)
        self.assertEqual(caught.exception.details, expected)
        self.assertIn(BETA_MAX, self.buddy_page())
        self.assertEqual(self.b.store.head(), head)
        with self.e.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM commands WHERE command_id='remove'").fetchone())

    def test_retired_effort_cleans_preferences_and_respects_upgrade_fence(self):
        self.b.console_call('user_policy_publish', {
            'commandId': 'configure',
            'buddySettings': [{'buddyId': ALPHA_MAX, 'alias': 'old-alpha', 'enabled': True}],
            'preferenceChanges': [{'buddyId': ALPHA_MAX, 'mode': 'prefer'}],
        })
        self.e.record_catalog(reading(models=('alpha', 'beta'), efforts=('high',)))
        self.assertTrue(self.buddy_page()[ALPHA_MAX]['enabled'])
        self.assert_removal(ALPHA_MAX, {'allowed': True})
        core = self.e.console_core()
        self.assertNotIn(ALPHA_MAX, {buddy['buddyId'] for buddy in core['buddies']})
        self.assertEqual(core['unavailableBuddyCount'], 2)

        request = {'commandId': 'remove', 'buddyIds': [ALPHA_MAX]}
        with self.assertRaises(BoardError) as caught:
            self.b.call('buddy_remove', request)
        self.assertEqual(caught.exception.code, 'FORBIDDEN')

        result = self.remove(ALPHA_MAX)
        self.assertEqual(result, {'removedBuddyIds': [ALPHA_MAX], 'removedModelServices': [], 'duplicate': False})
        self.assertNotIn(ALPHA_MAX, self.buddy_page())
        self.assertIn(ALPHA_HIGH, self.buddy_page())
        with self.e.db.read() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM evaluation_preferences WHERE buddy_id=?', (ALPHA_MAX,)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM buddies WHERE alias='old-alpha'").fetchone())
            self.assertIsNotNone(db.execute("SELECT 1 FROM model_services WHERE model='alpha'").fetchone())

        self.assertEqual(self.remove(ALPHA_MAX), {**result, 'duplicate': True})
        with self.assertRaises(BoardError) as caught:
            self.remove(BETA_MAX)
        self.assertEqual(caught.exception.code, 'CONFLICT')

        fence = self.b.directory / 'upgrade.json'
        fence.write_text('{}')
        for command_id, buddy_id in (('remove', ALPHA_MAX), ('remove', BETA_MAX), ('new-command', BETA_MAX)):
            with self.subTest(command_id=command_id, buddy_id=buddy_id), self.assertRaises(BoardError) as caught:
                self.remove(buddy_id, command_id=command_id)
            self.assertEqual(caught.exception.code, 'UPGRADE_IN_PROGRESS')
        fence.unlink()
        self.assertEqual(self.remove(ALPHA_MAX), {**result, 'duplicate': True})
        self.assertIn(BETA_MAX, self.buddy_page())
        with self.e.db.read() as db:
            events = db.execute("SELECT payload_json FROM events WHERE kind='catalog.buddies_removed'").fetchall()
        self.assertEqual([json.loads(event[0]) for event in events], [{'buddyIds': [ALPHA_MAX], 'modelServices': []}])

    def test_confirmed_absent_service_removal_keeps_history_and_allows_rediscovery(self):
        submitted = self.submit(self.b, provider='fixture', model='alpha', effort='high')
        run_id = submitted['runId']
        self.finish_turn(self.b, self.claim(self.b, run_id=run_id))
        self.accept_run(self.b, self.b.call('workflow_get', {'runId': run_id}))
        historical_run = self.b.call('workflow_get', {'runId': run_id, 'includeAudit': True})

        maintenance_task = self.fixture_task('maintenance-history')
        with self.e.db.write() as db:
            model_id = current_model(db, *ALPHA_SERVICE.values())
            now = self.clock.value
            db.execute("INSERT INTO maintenance_runs VALUES('maint','manual',?,?,'completed',NULL,NULL,?,?)",
                       (BETA_HIGH, maintenance_task, now, now))
            db.execute("INSERT INTO kind_summaries VALUES('dsh',?,'high','Retained assessment','maint')", (model_id,))
            db.execute("INSERT INTO kind_summary_evidence VALUES('dsh',?,'high',?,1)", (model_id, run_id))
            db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                       " rework_rounds,failures) VALUES('dsh',?,'high',1,1,1,0,0)"
                       " ON CONFLICT(harness,model_id,effort) DO NOTHING", (model_id,))

        missing = reading(models=('beta',), efforts=('high', 'max'))
        self.e.record_catalog(missing)
        self.assert_removal(ALPHA_HIGH, None)
        self.assertIn('pendingSince', self.buddy_page()[ALPHA_HIGH])
        self.clock.advance(3601)
        self.e.record_catalog(missing)
        self.assert_removal(ALPHA_HIGH, {'allowed': True})
        self.assert_removal(ALPHA_MAX, {'allowed': True})
        before = self.history_rows()
        kind_before = self.read_evaluation(model_id)
        self.assertEqual(kind_before['summary'], {'text': 'Retained assessment'})
        self.assertGreater(kind_before['statistics']['overall']['samples'], 0)

        result = self.remove(ALPHA_MAX, ALPHA_HIGH)
        self.assertEqual(result, {'removedBuddyIds': [ALPHA_MAX, ALPHA_HIGH],
                                  'removedModelServices': [ALPHA_SERVICE], 'duplicate': False})
        self.assertEqual(set(self.buddy_page()), {BETA_HIGH, BETA_MAX})
        self.assertEqual(self.history_rows(), before)
        self.assertEqual(self.b.call('workflow_get', {'runId': run_id, 'includeAudit': True}), historical_run)
        self.assertEqual(self.read_evaluation(model_id), kind_before)
        with self.e.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM model_services WHERE model='alpha'").fetchone())

        self.e.record_catalog(reading(models=('alpha', 'beta'), efforts=('high', 'max')))
        self.assert_removal(ALPHA_HIGH, None)
        self.assertFalse(self.buddy_page()[ALPHA_HIGH]['enabled'])
        self.assertEqual(self.history_rows(), before)

    def test_projection_and_atomic_rejection_follow_blocker_priority(self):
        self.e.record_catalog(reading(models=('alpha', 'beta'), efforts=('high',)))
        self.assert_removal(ALPHA_HIGH, None)
        self.assert_refused('BUDDY_NOT_REMOVABLE', ALPHA_HIGH, 'buddy-removal.not-retired')
        self.assert_refused('NOT_FOUND', 'missing')

        execution_task = self.fixture_task('execution')
        maintenance_task = self.fixture_task('maintenance')
        profile_task = self.fixture_task('profile')
        with self.e.db.write() as db:
            model_id = current_model(db, *ALPHA_SERVICE.values())
            now = self.clock.value
            db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                       " adapter,model_harness,model_provider,model_model,created_at,updated_at)"
                       " VALUES('active',?,1,'fixture','executing','dsh','dsh','fixture','alpha',?,?)",
                       (execution_task, now, now))
        self.check_blocker('active-execution')
        with self.e.db.write() as db:
            db.execute("UPDATE attempts SET execution_state='finished' WHERE attempt_id='active'")
        self.check_blocker('active-execution')

        blockers = (
            ('has-profile-runs',
             "INSERT INTO profile_runs(profile_run_id,model_id,buddy_id,task_id,state,started_at,finished_at)"
             " VALUES('profile',?,?,?,'completed',?,?)", (model_id, ALPHA_MAX, profile_task, now, now)),
            ('has-maintenance-runs',
             "INSERT INTO maintenance_runs VALUES('maint','manual',?,?,'completed',NULL,NULL,?,?)",
             (ALPHA_MAX, maintenance_task, now, now)),
            ('is-profile-buddy', 'UPDATE router_settings SET profile_buddy_id=?', (ALPHA_MAX,)),
            ('is-maintenance-buddy', 'UPDATE router_settings SET maintenance_buddy_id=?', (ALPHA_MAX,)),
            ('in-router-list', 'INSERT INTO router_buddies(position,buddy_id) VALUES(0,?)', (ALPHA_MAX,)),
        )
        for kind, sql, args in blockers:
            with self.subTest(kind=kind):
                with self.e.db.write() as db:
                    db.execute(sql, args)
                self.check_blocker(kind)
        core = {buddy['buddyId']: buddy for buddy in self.e.console_core()['buddies']}
        self.assertEqual(core[ALPHA_MAX]['removal'],
                         {'allowed': False, 'reason': reason('buddy-removal.in-router-list')})
        self.assertNotIn(BETA_MAX, core)

    def test_harness_unavailability_does_not_enable_removal(self):
        with self.e.db.write() as db:
            db.execute("UPDATE harness_health SET status='unhealthy', record_json=? WHERE harness='dsh'",
                       (json.dumps({'reason': reason('account.login-required')}),))
            db.execute('INSERT INTO router_buddies(position,buddy_id) VALUES(0,?)', (ALPHA_HIGH,))
        self.assertFalse(self.buddy_page()[ALPHA_HIGH]['available'])
        self.assert_removal(ALPHA_HIGH, None)

    def test_invalid_request_shapes(self):
        for params in (
            {'buddyIds': [ALPHA_MAX]},
            {'commandId': 'x' * 129, 'buddyIds': [ALPHA_MAX]},
            {'commandId': 'x', 'buddyIds': ALPHA_MAX},
            {'commandId': 'x', 'buddyIds': []},
            {'commandId': 'x', 'buddyIds': [str(i) for i in range(201)]},
            {'commandId': 'x', 'buddyIds': [ALPHA_MAX, ALPHA_MAX]},
            {'commandId': 'x', 'buddyIds': [42]},
            {'commandId': 'x', 'buddyIds': ['invalid\n']},
            {'commandId': 'x', 'buddyIds': ['x' * 129]},
            {'commandId': 'x', 'buddyIds': [ALPHA_MAX], 'enabled': False},
        ):
            with self.subTest(params=params), self.assertRaises(BoardError) as caught:
                self.b.console_call('buddy_remove', params)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')

    def check_blocker(self, kind):
        cause = f'buddy-removal.{kind}'
        self.assert_removal(ALPHA_MAX, {'allowed': False, 'reason': reason(cause)})
        self.assert_refused('BUDDY_IN_USE', ALPHA_MAX, cause)

    def fixture_task(self, name):
        return self.b.submit_execution(requestId=name, task=name, adapter='command',
                                       argv=['true'], cwd=str(self.workdir(name)),
                                       expectedSeconds=1800)['task']['runId']

    def read_evaluation(self, model_id):
        projection, _deadline = console_snapshot_projection(self.b.service)
        return next(kind for kind in projection['kindEvaluations'] if kind['modelId'] == model_id
                    and kind['effort'] == 'high')

    def history_rows(self):
        tables = ('model_correspondences', 'models', 'kind_statistics', 'kind_cell_statistics',
                  'kind_summaries', 'kind_summary_evidence', 'buddy_infrastructure_failures',
                  'workflow_runs', 'workflow_turns', 'attempts', 'evaluation_decisions',
                  'router_dispatches', 'router_outcomes')
        with self.e.db.read() as db:
            return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in tables}
