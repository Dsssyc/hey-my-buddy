"""Workflow routing carries the prepared checkout's Worker permission."""
import unittest
from unittest.mock import Mock, patch

from support import BoardTestCase
from hey_my_buddy.blackboard.catalog.catalog import permission_capabilities
from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.buddy.harnesses.registry import _WORKER_PERMISSIONS


class WorkflowPermissionRouteTests(unittest.TestCase):
    def test_prepared_access_extends_existing_capability_bounds(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        coordinator = DecisionCoordinator.__new__(DecisionCoordinator)
        spec = {"task": "work", "requiredCapabilities": ["input:text", "permission:full"]}
        with patch.object(DecisionCoordinator, "_create", return_value={}) as create:
            coordinator.route_workflow(connection, run_id="run-1", sequence=1, spec=spec,
                                       manifest={"access": "read"})
        request = create.call_args.args[1]
        self.assertEqual(request["requiredCapabilities"], ["input:text", "permission:full", "permission:read"])
        self.assertEqual(spec["requiredCapabilities"], ["input:text", "permission:full"])

    def test_full_access_requires_declared_permission_capability(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        coordinator = DecisionCoordinator.__new__(DecisionCoordinator)
        with patch.object(DecisionCoordinator, "_create", return_value={}) as create:
            coordinator.route_workflow(connection, run_id="run-1", sequence=1,
                                       spec={"task": "work"}, manifest={"access": "full"})
        self.assertEqual(create.call_args.args[1]["requiredCapabilities"], ["permission:full"])



class PermissionCandidateTests(BoardTestCase):
    def test_full_permission_routes_only_to_a_declaring_harness(self):
        from blackboard.routing.test_decision import DecisionTestCase, BUDDY_ID, SECOND_BUDDY_ID
        self.catalog_fixture()
        board = self.board()
        DecisionTestCase.seed(self, board)
        codex_id = 'codex:fixture:full:high'
        zcode_id = 'zcode:fixture:full:high'
        with board.store.db.write() as connection:
            connection.execute(
                'INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,'
                'available,enabled,capabilities_json)'
                ' VALUES(?,?,?,?,?,?,1,1,?)',
                (codex_id, codex_id, 'codex', 'fixture', 'full', 'high',
                 canonical_json(permission_capabilities('codex'))),
            )
            connection.execute("UPDATE harness_health SET status='ready' WHERE harness='codex'")
            connection.execute(
                'INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,'
                'available,enabled,capabilities_json)'
                ' VALUES(?,?,?,?,?,?,1,1,?)',
                (zcode_id, zcode_id, 'zcode', 'fixture', 'full', 'high',
                 canonical_json(permission_capabilities('zcode'))),
            )
            connection.execute("UPDATE harness_health SET status='ready' WHERE harness='zcode'")
        with board.store.db.read() as connection:
            write, _ = DecisionCoordinator._scan_candidates(connection, ['permission:write'], coding_only=True)
            self.assertTrue(write)
            read, _ = DecisionCoordinator._scan_candidates(connection, ['permission:read'], coding_only=True)
            self.assertEqual([row['buddy_id'] for row in read], [row['buddy_id'] for row in write])
            full, _ = DecisionCoordinator._scan_candidates(connection, ['permission:full'], coding_only=True)
            self.assertCountEqual([row['buddy_id'] for row in full],
                                  [codex_id, zcode_id, BUDDY_ID, SECOND_BUDDY_ID])
        with patch.dict(_WORKER_PERMISSIONS, {'zcode': {
                level: declaration for level, declaration in _WORKER_PERMISSIONS['zcode'].items()
                if level != 'full'}}):
            zcode_capabilities = permission_capabilities('zcode')
        self.assertNotIn('permission:full', zcode_capabilities)
        with board.store.db.write() as connection:
            connection.execute('UPDATE buddies SET capabilities_json=? WHERE buddy_id=?',
                               (canonical_json(zcode_capabilities), zcode_id))
        with board.store.db.read() as connection:
            full, _ = DecisionCoordinator._scan_candidates(connection, ['permission:full'], coding_only=True)
            self.assertCountEqual([row['buddy_id'] for row in full], [codex_id, BUDDY_ID, SECOND_BUDDY_ID])
