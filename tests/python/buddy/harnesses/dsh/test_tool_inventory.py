"""Synthetic YAML inventory: new rows narrow launch policy, never widen it."""
import unittest
import time
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.dsh import tool_inventory
from hey_my_buddy.buddy.harnesses.dsh.acp.launch import LaunchOwnershipError
from hey_my_buddy.buddy.harnesses.dsh.protocol import NativeError

from hey_my_buddy.buddy.harnesses.dsh.tool_inventory import tool_rows


class ToolInventoryParsingTests(unittest.TestCase):
    def test_custom_tags_are_inert_and_only_group_children_are_rows(self):
        self.assertEqual(tool_rows(b'''
- id: settings
  config:
    id: tool-not-a-row
    expression: !!js process.exit(1)
    other: !future {private: secret}
- id: group
  group: true
  config:
  - id: tool-new-plugin
    name: plugin
    config: !!js never_execute_this()
- id: tool-web
  disabled: true
'''), ("tool-new-plugin", "tool-web"))

    def test_unknown_or_ambiguous_shapes_are_not_an_empty_inventory(self):
        for raw in (b'', b'[]', b'rows: []', b'- tool-web', b'- id: [tool-web]',
                    b'- id: tool-web\n  id: tool-hidden', b'- id: tool-web\n  group: !!js true',
                    b'- id: group\n  group: true\n  config: {}',
                    b'- id: tool-web\n- config: {}', b'- id: tool-web\n  config: [',
                    b'&loop [{id: group, group: true, config: *loop}]', b'\xff'):
            with self.subTest(raw=raw):
                self.assertIsNone(tool_rows(raw))

    def test_group_aliases_and_disabled_rows_still_supply_all_row_names(self):
        self.assertEqual(tool_rows(b'''
- id: one
  group: true
  config: &rows
  - id: tool-future
    disabled: true
- id: two
  group: true
  config: *rows
'''), ('tool-future',))


class ToolInventoryLaunchTests(unittest.TestCase):
    def read(self):
        return tool_inventory.read_tool_rows(["fake-dsh"], environment={}, launch_environment={},
                                            cwd=".", private_root=Path("."), deadline=time.monotonic() + 1)

    def test_pre_spawn_failure_discards_exception_text(self):
        with mock.patch.object(tool_inventory, "launch", side_effect=OSError("private-secret")):
            self.assertEqual(self.read(), (None, tool_inventory.UNREADABLE))

    def test_confirmed_startup_failure_is_unreadable_and_allows_none_scope_to_continue(self):
        error = LaunchOwnershipError("private-secret", process=mock.Mock(), handle=mock.Mock(),
                                     evidence={"shutdownConfirmed": True})
        with mock.patch.object(tool_inventory, "launch", side_effect=error):
            self.assertEqual(self.read(), (None, tool_inventory.UNREADABLE))

    def test_unconfirmed_probe_stop_never_allows_a_second_launch(self):
        error = LaunchOwnershipError("private-secret", process=mock.Mock(), handle=mock.Mock(),
                                     evidence={"shutdownConfirmed": False})
        with mock.patch.object(tool_inventory, "launch", side_effect=error):
            with self.assertRaises(NativeError) as caught:
                self.read()
        self.assertEqual(caught.exception.code, "native-shutdown-failed")
        self.assertNotIn("private-secret", str(caught.exception))
        self.assertFalse(caught.exception.spawn_evidence["shutdownConfirmed"])
