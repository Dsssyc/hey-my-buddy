"""Public request denial and model-sourced help for ADR-032."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.cli import main as cli
from hey_my_buddy.cli import cli_help
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.requests import dump_request, parse_request


def help_text(*args: str) -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), mock.patch.object(cli, "_call_service", side_effect=AssertionError("help started service")):
        assert cli.main(["help", *args]) == 0
    return output.getvalue()


class HelpTests(unittest.TestCase):
    def test_host_help_lists_only_the_delegation_loop(self):
        general = help_text()
        for name in ("create", "amend", "submit", "await", "get", "inquire", "continue", "accept", "conclude", "cancel", "console"):
            self.assertIn(f"  {name}:", general)
        for retired in ("decide", "takeover", "scope-amend", "goal-amend", "workspace-resolve", "status", "result", "events", "wait-capacity", "controlFile", "submissionToken"):
            self.assertNotIn(retired, general)
        self.assertNotIn("  health:", general)
        self.assertIn("  health:", help_text("operations"))
        self.assertIn("  suggest:", help_text("role"))

    def test_detailed_help_reflects_pydantic_fields_and_combinations(self):
        submit = help_text("submit")
        for field in ("cwd", "macroTaskId", "title", "task", "buddy", "checkout", "access", "expectedSeconds", "inputs", "writeScope"):
            self.assertIn(field, submit)
        for label, detail, count in (("submit", submit, 1),
                                     ("continue and its helper packets", help_text("continue"), 2)):
            self.assertEqual(detail.count("'harness / provider / model / effort'"), count,
                             f"{label} help must state the buddy name writing")
        self.assertIn("console's Buddy page", submit)
        self.assertIn("checkout none forbids inputs and writeScope", submit)
        self.assertNotIn("creationKey", submit)
        self.assertNotIn("controlFile", submit)
        self.assertNotIn("requestId", submit)
        self.assertIn("waitSeconds", help_text("await"))
        self.assertIn("question and inquiryId cannot be combined", help_text("inquire"))
        self.assertIn("Nested HelperRequest fields:", help_text("continue"))
        self.assertIn("Nested IntegrationTarget fields:", help_text("accept"))
        self.assertIn("Nested RoutingHistoryRequest fields:", help_text("get"))
        self.assertIn("maxUtf8Bytes", help_text("role", "suggest"))
        self.assertNotIn("creationKey", help_text("role", "submit"))
        self.assertNotIn("import ast", Path(cli_help.__file__).read_text())
        self.assertIn("current stopped turn's fixed result",help_text("conclude"))

    def test_help_argument_grammar_comes_from_local_request_model(self):
        for arguments in (["operations", "submit"], ["submit", "extra"], ["unknown"]):
            with self.subTest(arguments=arguments), self.assertRaises(BoardError):
                parse_request("help", {"arguments": arguments}, audience="local")

    def test_relocate_help_belongs_to_operations_and_has_only_cwd(self):
        self.assertNotIn("  relocate:", help_text())
        self.assertNotIn("  relocate:", help_text("role"))
        self.assertIn("  relocate:", help_text("operations"))
        detail = help_text("operations", "relocate")
        self.assertIn("cwd (required)", detail)
        self.assertIn("existing marker", detail)
        self.assertNotIn("projectId", detail)
        model = parse_request("relocate", {"cwd": str(Path.cwd())}, audience="operations")
        self.assertEqual(set(dump_request(model)), {"cwd"})


class RequestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cwd = directory.name

    def packet(self, **changes):
        packet = {"cwd": self.cwd, "macroTaskId": "standalone", "title": "Read",
                  "task": "Review results", "buddy": "route", "checkout": "existing",
                  "access": "read", "inputs": [], "expectedSeconds": 600}
        packet.update(changes)
        return packet

    def assert_invalid(self, method: str, packet: dict, field: str, *, audience="host"):
        with self.assertRaises(BoardError) as raised:
            parse_request(method, packet, audience=audience)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn(field, str(raised.exception))

    def test_host_cannot_supply_internal_identity_or_retired_controls(self):
        for field, value in (("creationKey", "a" * 64), ("requestId", "old"),
                             ("controlFile", "/old"), ("hostId", "host"),
                             ("executionWorkspace", {"kind": "existing"}),
                             ("submissionToken", "old")):
            with self.subTest(field=field):
                self.assert_invalid("submit", self.packet(**{field: value}), field)
        self.assert_invalid("create", {"cwd": self.cwd, "title": "X", "goal": "Do X", "creationKey": "a" * 64}, "creationKey")

    def test_authorization_and_checkout_combinations_reject_implicit_choices(self):
        self.assert_invalid("submit", {key: value for key, value in self.packet().items() if key != "access"}, "access")
        self.assert_invalid("submit", {key: value for key, value in self.packet().items() if key != "macroTaskId"}, "macroTaskId")
        self.assert_invalid("submit", self.packet(access="write"), "writeScope")
        self.assert_invalid("submit", self.packet(writeScope=["."]), "writeScope")
        self.assert_invalid("submit", self.packet(checkout="none"), "inputs")
        self.assert_invalid("submit", self.packet(access="full", writeScope=["src"]), "accessNote")
        self.assert_invalid("submit", self.packet(delegationAuthority=True), "delegationAuthority")
        self.assert_invalid("submit", self.packet(expectedSeconds=0), "expectedSeconds")
        self.assert_invalid("submit", self.packet(fixes=None), "fixes")
        self.assert_invalid("continue", {"runId": "run-1", "requestId": "req-1"}, "input")

    def test_continue_and_accept_bind_exact_response_facts(self):
        self.assert_invalid("continue", {"runId": "run-1", "input": "again"}, "requestId")
        self.assert_invalid("continue", {"runId": "run-1", "artifactId": "art-1"}, "input")
        self.assert_invalid("continue", {"runId": "run-1", "requestId": "req-1", "decision": "decline", "helpers": []}, "helpers")
        self.assert_invalid("accept", {"runId": "run-1", "artifactId": "art-1", "note": "checked"}, "target")
        self.assert_invalid("accept", {"runId": "run-1", "artifactId": "art-1", "note": "checked",
                                      "notRequired": "no files", "target": {"path": self.cwd, "ref": "main"}}, "target")

    def test_service_and_lead_requests_refuse_public_creation_key(self):
        packet = self.packet()
        model = parse_request("workflow_submit", packet, audience="service")
        self.assertEqual(dump_request(model), packet)
        self.assertNotIn("writeScope", dump_request(model))
        self.assert_invalid("workflow_submit", {**packet, "creationKey": "a" * 64}, "creationKey",
                            audience="service")
        create = {"cwd": self.cwd, "title": "X", "goal": "Do X"}
        self.assertEqual(dump_request(parse_request("workflow_create", create, audience="service")), create)
        self.assert_invalid("workflow_create", {**create, "creationKey": "a" * 64}, "creationKey",
                            audience="service")
        lead = {key: value for key, value in packet.items() if key != "macroTaskId"}
        lead["buddy"] = "dsh / provider / model / off"
        self.assertEqual(dump_request(parse_request("workflow_submit", lead, audience="lead")), lead)
        self.assert_invalid("workflow_submit", {**lead, "creationKey": "b" * 64}, "creationKey",
                            audience="lead")

    def test_get_selector_and_inquiry_identity(self):
        self.assert_invalid("get", {"runId": "run-1", "cwd": self.cwd}, "exactly one")
        self.assert_invalid("get", {"macroTaskId": "mt-1", "includeAudit": True}, "includeAudit")
        self.assertEqual(dump_request(parse_request("get", {"macroTaskId": "mt-1", "before": "opaque-page-token"}))["before"],
                         "opaque-page-token")
        self.assertEqual(dump_request(parse_request("get", {"runId": "run-1",
                                                    "routingHistory": {"before": 12}}))["routingHistory"], {"before": 12})
        self.assert_invalid("inquire", {"runId": "run-1", "question": "status?", "inquiryId": "q-1"}, "question")
        with self.assertRaises(BoardError) as raised:
            parse_request("get", {"cwd": self.cwd}, audience="role")
        self.assertIn("cwd", str(raised.exception))
        self.assertEqual(dump_request(parse_request("get", {}, audience="role")), {})

    def test_local_console_choices_do_not_cross_service_model(self):
        self.assertEqual(dump_request(parse_request("console", {"browser": False}))["browser"], False)
        with self.assertRaises(BoardError) as raised:
            parse_request("console", {"action": "open", "browser": False}, audience="service")
        self.assertIn("browser", str(raised.exception))
        self.assertEqual(dump_request(parse_request("harness-set", {"harness": "codex", "path": None},
                                                    audience="operations")), {"harness": "codex", "path": None})


if __name__ == "__main__":
    unittest.main()
