"""Workflow routing carries the prepared checkout's Worker permission."""
import unittest
from unittest.mock import Mock, patch

from support import BoardTestCase
from hey_my_buddy.blackboard.catalog.catalog import permission_capabilities
from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
from hey_my_buddy.blackboard.store.db import canonical_json


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
        from blackboard.routing.test_decision import DecisionTestCase
        self.catalog_fixture()
        board = self.board()
        DecisionTestCase.seed(self, board)
        codex_id = 'codex:fixture:full:high'
        zcode_id = 'zcode:fixture:full:high'
        with board.store.db.write() as connection:
            connection.execute(
                'INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,'
                'available,enabled,capabilities_json,created_revision,updated_revision)'
                ' VALUES(?,?,?,?,?,?,1,1,?,0,0)',
                (codex_id, codex_id, 'codex', 'fixture', 'full', 'high',
                 canonical_json(permission_capabilities('codex'))),
            )
            connection.execute("UPDATE harness_health SET status='ready' WHERE adapter='codex'")
            connection.execute(
                'INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,'
                'available,enabled,capabilities_json,created_revision,updated_revision)'
                ' VALUES(?,?,?,?,?,?,1,1,?,0,0)',
                (zcode_id, zcode_id, 'zcode', 'fixture', 'full', 'high',
                 canonical_json(permission_capabilities('zcode'))),
            )
            connection.execute("UPDATE harness_health SET status='ready' WHERE adapter='zcode'")
        with board.store.db.read() as connection:
            write, _ = DecisionCoordinator._scan_candidates(connection, ['permission:write'], coding_only=True)
            self.assertTrue(write)
            read, _ = DecisionCoordinator._scan_candidates(connection, ['permission:read'], coding_only=True)
            self.assertEqual([row['profile_id'] for row in read], [row['profile_id'] for row in write])
            full, _ = DecisionCoordinator._scan_candidates(connection, ['permission:full'], coding_only=True)
            self.assertEqual([row['profile_id'] for row in full], [codex_id, zcode_id])
