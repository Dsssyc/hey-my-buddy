"""Native review verification is explicit, replay-safe and version-bound."""
import json
from unittest.mock import patch

from buddy import harness_review as review
from buddy.errors import BoardError
from support import BoardTestCase, enable_fixture_configuration


class HarnessReviewTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.harness = self.board()
        self.store = self.harness.store
        self.configuration = {"adapter": "codex", "provider": "openai", "model": "gpt-6-sol", "effort": "high"}
        enable_fixture_configuration(self.store, self.configuration)
        self.profile = ":".join(self.configuration.values())
        self.health = {"adapter": "codex", "status": "ready", "version": "0.159.0", "command": ["/fake/codex"], "locationFingerprint": "fixture"}
        with self.store.db.write() as db:
            db.execute("UPDATE harness_health SET record_json=? WHERE adapter='codex'", (json.dumps(self.health),))
        self.params = {"adapter": "codex", "profileId": self.profile, "requestId": "review-1", "execute": True}

    def test_historical_certificate_is_data_and_does_not_cover_a_new_version(self):
        with self.store.db.read() as db:
            original = review.verification_view(db, "codex", {**self.health, "version": "0.157.0"}, platform="darwin")
            current = review.verification_view(db, "codex", self.health, platform="darwin")
            windows = review.verification_view(db, "codex", {**self.health, "version": "0.157.0"}, platform="win32")
        self.assertTrue(original["verified"])
        self.assertEqual(current["status"], "new-version")
        self.assertFalse(current["verified"])
        self.assertFalse(windows["verified"])

    def test_prepare_is_model_free_and_creates_no_work(self):
        plan = review.request(self.store, {"adapter": "codex", "profileId": self.profile})
        self.assertFalse(plan["started"])
        with self.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_execution_admission_and_reply_replay_share_one_task(self):
        first = review.request(self.store, self.params)
        with self.store.db.write() as db:
            db.execute("UPDATE harness_health SET status='unhealthy' WHERE adapter='codex'")
            db.execute("UPDATE evaluation_profiles SET enabled=0")
        replay = review.request(self.store, self.params)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(first["runId"], replay["runId"])
        with self.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)

    def test_changed_request_or_simultaneous_probe_is_refused(self):
        review.request(self.store, self.params)
        for update, code in (({"profileId": "other"}, "CONFLICT"), ({"requestId": "review-2"}, "HARNESS_REVIEW_BUSY")):
            with self.subTest(code=code), self.assertRaises(BoardError) as caught:
                review.request(self.store, {**self.params, **update})
            self.assertEqual(caught.exception.code, code)

    def test_unknown_version_or_stale_health_is_not_probeable(self):
        with self.assertRaises(BoardError) as caught:
            review.request(self.store, {**self.params, "expectedRevision": 999})
        self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
        with self.store.db.write() as db:
            db.execute("UPDATE harness_health SET record_json=? WHERE adapter='codex'", (json.dumps({**self.health, "version": "unknown"}),))
        with self.assertRaises(BoardError):
            review.request(self.store, self.params)

    def test_service_preview_and_cli_help_do_not_start_native_work(self):
        from buddy.cli_help import render
        with patch('subprocess.Popen', side_effect=AssertionError('no native process')):
            plan = self.harness.call('harness_verify', {'adapter': 'codex', 'profileId': self.profile})
            help_text, _ = render('harness-verify', ['harness-verify'])
        self.assertFalse(plan['started'])
        self.assertIn('execute', help_text)
        self.assertIn('profileId', help_text)

    def _settle(self, *, failed_check=None, change_version=False, confirmed=True):
        admitted = self.harness.call('harness_verify', self.params)
        self.harness.call('worker_register', {'workerId': 'verifier', 'adapter': 'dsh', 'capabilities': [review.ADAPTER]})
        claim = self.harness.call('worker_claim', {'workerId': 'verifier', 'taskId': admitted['runId'],
            'claimRequestId': 'claim-review', 'nonce': 'private-review-nonce-1234'})['claim']
        self.assertEqual(claim['attempt']['modelFamily'], {'adapter': 'codex', 'provider': 'openai', 'model': 'gpt-6-sol'})
        if change_version:
            with self.store.db.write() as db:
                db.execute("UPDATE harness_health SET record_json=? WHERE adapter='codex'", (json.dumps({**self.health, 'version': 'next'}),))
        checks = {check: check != failed_check for check in review.CHECKS}
        self.harness.call('worker_result', {'workerId': 'verifier', 'attemptId': claim['attempt']['attemptId'],
            'generation': claim['attempt']['generation'], 'nonce': 'private-review-nonce-1234', 'commandId': 'settle-review',
            'status': 'ok', 'shutdownConfirmed': confirmed, 'result': {'checks': checks, 'version': self.health['version'],
            'platform': review.sys.platform}})
        return self.harness.service.harnesses.get('codex')['reviewVerification']

    def test_complete_receipt_enables_current_version_without_a_catalog_refresh(self):
        verification = self._settle()
        self.assertTrue(verification['verified'])
        from buddy.adapters.codex import CodexAdapter
        from buddy.harness_runtime import bound
        with bound(self.harness.service.harnesses.all()):
            self.assertTrue(CodexAdapter().read_only_structured_verified)
            snapshot = self.harness.call('console_snapshot', {})
        profile = next(p for p in snapshot['profiles'] if p['profileId'] == self.profile)
        self.assertIn('decision', profile['capabilities'])

    def test_missing_boundary_evidence_or_unconfirmed_stop_never_certifies(self):
        verification = self._settle(failed_check='boundaryDenials')
        self.assertFalse(verification['verified'])
        self.assertIn('boundaryDenials', verification['failedChecks'])

    def test_unconfirmed_stop_is_unavailable_and_blocks_another_call(self):
        verification = self._settle(confirmed=False)
        self.assertFalse(verification['verified'])
        self.assertEqual(verification['reasonCode'], 'HARNESS_REVIEW_SHUTDOWN_UNCONFIRMED')
        with self.assertRaises(BoardError) as caught:
            self.harness.call('harness_verify', {**self.params, 'requestId': 'review-2'})
        self.assertEqual(caught.exception.code, 'HARNESS_REVIEW_BUSY')

    def test_version_change_during_the_probe_never_certifies_the_new_version(self):
        verification = self._settle(change_version=True)
        self.assertFalse(verification['verified'])
        self.assertEqual(verification['version'], 'next')

    def test_cancelled_queued_probe_can_be_replaced_but_never_implicitly_retried(self):
        admitted = self.harness.call('harness_verify', self.params)
        self.harness.call('task_cancel', {'runId': admitted['runId']})
        new = self.harness.call('harness_verify', {**self.params, 'requestId': 'review-2'})
        self.assertNotEqual(new['runId'], admitted['runId'])
        with self.assertRaises(BoardError) as caught:
            self.harness.call('task_retry', {'runId': admitted['runId']})
        self.assertEqual(caught.exception.code, 'UNSUPPORTED')


if __name__ == "__main__":
    import unittest
    unittest.main()
