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
