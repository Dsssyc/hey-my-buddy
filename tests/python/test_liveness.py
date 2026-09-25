"""Routine client attachment must not turn idle workers into full-database scanners."""
import json
from unittest.mock import patch

from support import BoardTestCase

from buddy.client import BoardClient
from buddy.contracts import CONTRACT_VERSION
from buddy.errors import BoardError
from buddy.service import call_operation


class LivenessTests(BoardTestCase):
    def test_ping_uses_no_storage_or_runtime_inspection(self):
        board = self.board()
        board.control.update(service_id="fixture-service", contract_version=CONTRACT_VERSION)
        with patch.object(board.store.db, "read", side_effect=AssertionError("storage scan")), \
                patch("buddy.service.runtime.resolve_runtime", side_effect=AssertionError("runtime inspection")):
            result = board.call("ping", {})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["serviceId"], "fixture-service")
        self.assertEqual(result["contractVersion"], CONTRACT_VERSION)
        self.assertEqual(result["schemaVersion"], 11)

    def test_each_client_attach_uses_light_ping(self):
        board = self.board()
        operations = []

        def request(endpoint, operation, params, resource="control"):
            operations.append(operation)
            return call_operation(board.service, operation, params)

        client = BoardClient(board.directory, autostart=False)
        with patch("buddy.transport._read_endpoint", return_value={"address": "private-fixture"}), \
                patch("buddy.transport._request", side_effect=request), \
                patch.object(board.store, "integrity", wraps=board.store.integrity) as integrity:
            for _ in range(3):
                client.call("worker_list")
        self.assertEqual(integrity.call_count, 0, "routine reads must never run database integrity checks")
        self.assertEqual(operations, ["ping", "worker_list"] * 3)

    def test_explicit_health_still_checks_current_integrity(self):
        board = self.board()
        with patch.object(board.store, "integrity", wraps=board.store.integrity) as integrity:
            result = board.call("health", {})
        self.assertEqual(integrity.call_count, 1)
        self.assertEqual(result["integrity"]["integrity"], "ok")

    def test_ping_authentication_and_unknown_arguments_are_checked(self):
        board = self.board()
        self.assertEqual(json.loads(board.service.ping('{"token":"wrong"}'))["error"]["code"], "UNAUTHORIZED")
        with self.assertRaises(BoardError) as caught:
            board.call("ping", {"skipAuthentication": True})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_ping_reports_persistence_error_without_claiming_storage_validation(self):
        board = self.board()
        board.store.persistence_error = "fixture unavailable"
        reply = board.call("ping", {})
        self.assertEqual(reply["persistenceError"], "fixture unavailable")
        self.assertNotIn("integrity", reply)
