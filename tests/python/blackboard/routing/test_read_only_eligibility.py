"""Router qualification comes from shared declarations without native calls."""
from unittest.mock import patch
from hey_my_buddy.protocol.capabilities import PERMISSION_LEVELS
from hey_my_buddy.blackboard.service.harness_health import read_health
from support import BoardTestCase


class HealthEligibilityTests(BoardTestCase):
    def test_all_harnesses_publish_declared_levels_without_spawning_or_writing(self):
        board = self.board()
        with board.store.db.read() as db, patch('subprocess.Popen', side_effect=AssertionError('native process')), patch('subprocess.run', side_effect=AssertionError('native process')):
            before = db.total_changes
            for harness, levels in PERMISSION_LEVELS.items():
                result = read_health(db, harness)
                self.assertEqual(result['permissions'], levels)
                self.assertIn('read', levels)
                self.assertNotIn('systemSandbox', result)
                self.assertNotIn('readOnlyStructured', result)
            self.assertEqual(db.total_changes, before)
