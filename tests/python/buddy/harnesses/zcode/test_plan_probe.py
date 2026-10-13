"""The deliberate probe's local model sequence, without installed ZCode."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


class PlanProbeModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probes = Path(__file__).resolve().parents[4] / "probes"
        spec = importlib.util.spec_from_file_location("plan_mode_probe", probes / "zcode_plan_mode.py")
        cls.module = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "path", [str(probes), *sys.path]):
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.probe = self.module.ModeProbe()
        self.probe.next_operation = 0
        self.probe.observations = []
        self.probe.native_events = []
        self.probe.call_prefix = "call_probe_fixture"
        self.probe.read_target = Path("/tmp/read.txt")
        self.probe.operations = [("write", "Write", {"file_path": "/tmp/probe", "content": "marker"}),
                                 ("command", "Bash", {"command": "printf marker"}),
                                 ("read", "Read", {"file_path": "/tmp/probe"}),
                                 ("finish", "mcp__probe__buddy_finish_turn", {"disposition": "completed"})]

    def request(self, tools, results=()):
        data = {"model": "local-fixture", "messages": [{"role": "user", "content": "probe"}, *results]}
        if tools is not None:
            data["tools"] = [{"function": {"name": name}} for name in tools]
        handler_class = self.probe.model_handler()
        handler = handler_class.__new__(handler_class)
        body = json.dumps(data).encode()
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.do_POST()
        frames = [json.loads(line[6:]) for line in handler.wfile.getvalue().decode().splitlines()
                  if line.startswith("data: ") and line != "data: [DONE]"]
        return frames[0]["choices"][0]["delta"]

    def test_native_title_calls_do_not_consume_operations(self):
        self.assertEqual(self.request(None)["content"], "Local mode probe finished.")
        self.assertEqual(self.probe.next_operation, 0)
        self.assertEqual(self.probe.observations, [])

    def test_a_root_request_with_an_empty_tool_list_still_attempts_the_write(self):
        self.assertEqual(self.request([])["tool_calls"][0]["function"]["name"], "Write")
        self.assertFalse(self.probe.observations[0]["advertised"])

    def test_four_operations_are_requested_even_when_plan_omits_write_and_command(self):
        results = []
        for operation, name, arguments in self.probe.operations:
            call = self.request(["Read", "mcp__probe__buddy_finish_turn"], results)["tool_calls"][0]
            self.assertEqual(call["function"]["name"], name)
            self.assertEqual(json.loads(call["function"]["arguments"]), arguments)
            results.append({"role": "tool", "tool_call_id": call["id"],
                            "content": "Permission denied in plan mode" if operation in ("write", "command") else "ok"})
        self.assertEqual(self.request(["Read"], results)["content"], "Local mode probe finished.")
        self.assertEqual([item["operation"] for item in self.probe.observations], ["write", "command", "read", "finish"])
        self.assertEqual([item["advertised"] for item in self.probe.observations], [False, False, True, True])
        self.assertEqual([item["modelToolResults"] for item in self.probe.observations], [[result] for result in results])

    def test_hosts_native_plan_denial_is_classified_as_denied(self):
        self.probe.observations = [
            {"operation": operation, "callId": "call_" + operation, "modelToolResults": [
                {"role": "tool", "content": "Plan mode only allows read-only, non-destructive tools"}]}
            for operation in ("write", "command")]
        reports = self.probe.operation_reports({"write": None, "command": None}, False)
        self.assertEqual([item["handling"] for item in reports[:2]], ["denied", "denied"])

    def test_read_only_command_uses_its_native_result_instead_of_file_effects(self):
        self.probe.operations = [("read_command", "Bash", {"command": "ls /tmp"})]
        self.probe.observations = [{"operation": "read_command", "callId": "call_read_command",
                                    "modelToolResults": [{"role": "tool", "content": "read.txt\n"}]}]
        report = self.probe.operation_reports({"write": None, "command": None}, False)[0]
        self.assertEqual(report["handling"], "allowed")

    def test_touch_requires_an_existing_empty_file_to_count_as_allowed(self):
        self.probe.operations = [("touch_command", "Bash", {"command": "touch /tmp/probe"})]
        self.probe.observations = [{"operation": "touch_command", "callId": "call_touch", "modelToolResults": []}]
        missing = self.probe.operation_reports({"touch_command": None}, False)[0]
        empty = self.probe.operation_reports({"touch_command": ""}, False)[0]
        self.assertEqual(missing["handling"], "other")
        self.assertEqual(empty["handling"], "allowed")

    def test_resume_sequence_ignores_the_previous_turns_tool_results(self):
        self.probe.operations = [self.probe.operations[0], self.probe.operations[-1]]
        self.probe.call_prefix = "call_probe_resumed"
        old = {"role": "tool", "tool_call_id": "call_probe_previous_write", "content": "old result"}
        first = self.request(["Write", "mcp__probe__buddy_finish_turn"], [old])["tool_calls"][0]
        current = {"role": "tool", "tool_call_id": first["id"], "content": "new result"}
        second = self.request(["Write", "mcp__probe__buddy_finish_turn"], [old, current])["tool_calls"][0]
        self.assertEqual(second["function"]["name"], "mcp__probe__buddy_finish_turn")
        self.assertEqual(self.probe.observations[0]["modelToolResults"], [current])
        self.assertEqual(len(self.probe.observations), 2)

    def offline_probe(self):
        from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
        probe = self.module.ModeProbe()
        self.addCleanup(probe.doCleanups)
        ZcodeFixtureCase.setUp(probe)
        probe.base = probe.root
        probe.write_target = probe.cwd / "written.txt"
        probe.command_target = probe.cwd / "command.txt"
        probe.read_target = probe.cwd / "read.txt"
        probe.read_target.write_text(self.module.MARKERS["read"])
        return probe

    def offline_run(self, request, **kwargs):
        # The model ID differs between installed and offline fixtures.
        request = request.model_copy(update={"buddy":
            request.buddy.model_copy(update={"model": "fixture-model"})})
        return self.original_run(request, **kwargs)

    def test_probe_can_resume_the_same_plan_session_through_the_offline_native_path(self):
        probe = self.offline_probe()
        self.original_run = self.module.native_run.run
        with mock.patch.object(self.module.native_run, "run", side_effect=self.offline_run):
            first = probe.measure("plan", 10)
            second = probe.measure("yolo", 10, previous=probe.last_result.native_identity.session_id)
        self.assertTrue(first["turnCompleted"], first["end"])
        self.assertTrue(second["turnCompleted"], second["end"])
        self.assertTrue(second["sameSession"])
        self.assertEqual(second["previousSessionId"], first["nativeIdentity"]["sessionId"])
        switched = [call for call in second["modeCalls"] if call["method"] == "session/setMode"]
        self.assertEqual(switched[0]["params"], {"sessionId": second["previousSessionId"], "mode": "yolo"})
        self.assertEqual(switched[0]["readback"], {"current": "yolo"})
        self.assertEqual([item["operation"] for item in first["operations"]],
                         ["write", "command", "python_command", "tee_command", "touch_command",
                          "copy_command", "read", "read_command", "finish"])
        self.assertEqual([item["operation"] for item in second["operations"]], ["write", "finish"])
        self.assertEqual(first["modeCalls"][0]["readback"], {"current": "build"})

    def test_probe_plan_continuations_finish_with_underlying_build_or_yolo_readback(self):
        self.original_run = self.module.native_run.run
        for initial_mode, expected in (("plan", "build"), ("yolo", "yolo")):
            with self.subTest(initial=initial_mode):
                probe = self.offline_probe()
                with mock.patch.object(self.module.native_run, "run", side_effect=self.offline_run):
                    first = probe.measure(initial_mode, 10)
                    resumed = probe.measure("plan", 10, previous=probe.last_result.native_identity.session_id)
                    resumed_target = probe.write_target
                    if initial_mode == "plan":
                        third = probe.measure("yolo", 10, previous=probe.last_result.native_identity.session_id)
                self.assertTrue(first["turnCompleted"], first["end"])
                self.assertTrue(resumed["turnCompleted"], resumed["end"])
                self.assertTrue(resumed["sameSession"])
                switched = [call for call in resumed["modeCalls"] if call["method"] == "session/setMode"]
                self.assertEqual(switched[0]["params"]["mode"], "plan")
                self.assertEqual(switched[0]["readback"], {"current": expected})
                self.assertEqual(len(resumed["operations"]), 9)
                if initial_mode == "plan":
                    self.assertTrue(third["turnCompleted"], third["end"])
                    self.assertTrue(third["sameSession"])
                    self.assertEqual(probe.turn_index, 3)
                    self.assertNotEqual(resumed_target, probe.write_target)
