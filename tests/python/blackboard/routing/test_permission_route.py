"""Workflow routing carries the prepared checkout's Worker permission."""
import unittest
from unittest.mock import Mock, patch

from support import BoardTestCase
from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator


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

    def test_full_access_requires_undeclared_permission_capability(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        coordinator = DecisionCoordinator.__new__(DecisionCoordinator)
        with patch.object(DecisionCoordinator, "_create", return_value={}) as create:
            coordinator.route_workflow(connection, run_id="run-1", sequence=1,
                                       spec={"task": "work"}, manifest={"access": "full"})
        self.assertEqual(create.call_args.args[1]["requiredCapabilities"], ["permission:full"])



class PermissionCandidateTests(BoardTestCase):
    def test_undeclared_permissions_do_not_enter_the_existing_candidate_scan(self):
        from blackboard.routing.test_decision import DecisionTestCase
        self.catalog_fixture()
        board = self.board()
        DecisionTestCase.seed(self, board)
        with board.store.db.read() as connection:
            write, _ = DecisionCoordinator._scan_candidates(connection, ['permission:write'], coding_only=True)
            self.assertTrue(write)
            read, _ = DecisionCoordinator._scan_candidates(connection, ['permission:read'], coding_only=True)
            self.assertEqual([row['profile_id'] for row in read], [row['profile_id'] for row in write])
            self.assertEqual(DecisionCoordinator._scan_candidates(connection, ['permission:full'], coding_only=True)[0], [])
