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

    def assert_invalid(self, method: str, packet: dict, field: str):
        with self.assertRaises(BoardError) as raised:
            parse_request(method, packet)
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

    def test_service_variant_requires_derived_key_and_dumps_exact_fields(self):
        packet = self.packet()
        with self.assertRaises(BoardError):
            parse_request("workflow_submit", packet, audience="service")
        model = parse_request("workflow_submit", {**packet, "creationKey": "a" * 64}, audience="service")
        self.assertEqual(dump_request(model)["creationKey"], "a" * 64)
        self.assertNotIn("writeScope", dump_request(model))
        lead = {key: value for key, value in packet.items() if key != "macroTaskId"}
        lead["buddy"] = "dsh:provider:model:off"
        with self.assertRaises(BoardError):
            parse_request("workflow_submit", lead, audience="lead")
        self.assertEqual(dump_request(parse_request("workflow_submit", {**lead, "creationKey": "b" * 64},
                                                    audience="lead"))["creationKey"], "b" * 64)

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


class LeadCreationKeyTests(unittest.TestCase):
    def test_standalone_lead_submitting_elsewhere_uses_the_submitted_project(self):
        packet = {"cwd":"/submitted/project","title":"Child","task":"Inspect another project"}
        document = {"taskId":"lead-run"}
        with mock.patch.object(cli,"_call_service",return_value={"macroTaskId":None,"project":{"id":"lead-project"}}), \
             mock.patch.object(cli,"_project_id",return_value="submitted-project") as project:
            key = cli._lead_creation_key(packet,document=document,credential="lead-token",state_dir=None)
        self.assertEqual(key,cli._creation_key(["adr032-submit-v1","project:submitted-project","Child","Inspect another project"]))
        project.assert_called_once_with(packet["cwd"],None)

    def test_standalone_lead_keeps_different_target_projects_in_distinct_namespaces(self):
        packet = {'title': 'Review', 'task': 'Inspect the evidence'}
        with mock.patch.object(cli, '_call_service', return_value={'macroTaskId': None, 'project': {'id': 'lead-project'}}), \
             mock.patch.object(cli, '_project_id', side_effect=lambda cwd, state: cwd):
            keys = [cli._lead_creation_key({**packet, 'cwd': cwd}, document={'taskId': 'lead-run'},
                    credential='lead-token', state_dir=None) for cwd in ('/target/project-a', '/target/project-b')]
        self.assertNotEqual(keys[0], keys[1])

    def test_grouped_lead_uses_its_macro_namespace(self):
        packet = {"cwd":"/submitted/project","title":"Child","task":"Inspect another project"}
        with mock.patch.object(cli,"_call_service",return_value={"macroTaskId":"macro-1"}), \
             mock.patch.object(cli,"_project_id",side_effect=AssertionError("macro binding is authoritative")):
            key = cli._lead_creation_key(packet,document={"taskId":"lead-run"},credential="lead-token",state_dir=None)
        self.assertEqual(key,cli._creation_key(["adr032-submit-v1","macro:macro-1","Child","Inspect another project"]))


if __name__ == "__main__":
    unittest.main()
