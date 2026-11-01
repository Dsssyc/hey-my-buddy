"""The console goal-history read is registered as a named C-Two operation."""

import json

from support import BoardTestCase
from hey_my_buddy.protocol.contracts import BuddyControl, CONTROL_NAME
from hey_my_buddy.protocol.transport import _read_endpoint


class MacroTaskGoalHistoryContractTests(BoardTestCase):
    def test_named_control_read_validates_the_goal_history_request(self):
        with self.daemon():
            endpoint = _read_endpoint(self.directory)
            self.assertIsNotNone(endpoint)
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as control:
                missing = json.loads(control.macro_task_goal_history(json.dumps({
                    "token": endpoint["token"], "macroTaskId": "macro-absent",
                })))
                self.assertEqual(missing["error"]["code"], "NOT_FOUND")
                invalid = json.loads(control.macro_task_goal_history(json.dumps({
                    "token": endpoint["token"], "macroTaskId": "run:standalone",
                })))
                self.assertEqual(invalid["error"]["code"], "INVALID_ARGUMENT")
