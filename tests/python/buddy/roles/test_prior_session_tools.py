"""Worker history tools across the session service and native mounts."""
from pathlib import Path
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from hey_my_buddy.buddy.harnesses.claude import native_run as claude
from hey_my_buddy.buddy.harnesses.codex import native_run as codex
from hey_my_buddy.buddy.harnesses.dsh import native_run as dsh
from hey_my_buddy.buddy.harnesses.zcode import native_run as zcode
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.buddy.roles.turn_io import private_json
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.session_content import SESSION_FORMAT
from hey_my_buddy.protocol.session_reader import PRIOR_SESSION_TOOL_NAMES
from hey_my_buddy.protocol.turn_output import validate_outcome


def prior_record(root: Path, attempt_id: str = "earlier", turn_index: int = 1) -> dict:
    path = root / f"{attempt_id}.jsonl"
    blocks = [
        {"sessionUpdate": kind, "content": {"type": "text", "text": value},
         "_meta": {"nativeItemId": "item", "contentSegment": "text"}}
        for kind, value in (("agent_message_chunk", "A"), ("agent_thought_chunk", "hidden"),
                            ("agent_message_chunk", "🧪汉Z"))
    ]
    path.write_text("\n".join(map(canonical_json, [
        SESSION_FORMAT.to_payload(), *({"sessionId": attempt_id, "update": block} for block in blocks),
    ])) + "\n", encoding="utf-8")
    return {"turnIndex": turn_index, "turnId": f"turn-{turn_index}", "attemptId": attempt_id,
            "turnState": "returned", "disposition": "attention", "harness": "codex",
            "provider": "fixture", "model": "fixture", "effort": "low", "startedAt": None,
            "finishedAt": None, "recordAvailable": True, "unavailableReason": None,
            "stopped": True, "recordPath": str(path)}


def mcp_requests(config_path, requests):
    process = subprocess.run(
        [sys.executable, "-m", "hey_my_buddy.buddy.roles.session_mcp", "--config", str(config_path)],
        input="".join(canonical_json({"jsonrpc": "2.0", "id": index, "method": method, "params": params}) + "\n"
                      for index, (method, params) in enumerate(requests)),
        encoding="utf-8", capture_output=True, check=True, timeout=10)
    return [json.loads(line)["result"] for line in process.stdout.splitlines()]


class PriorSessionToolsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.manifest = [prior_record(self.root)]

    def test_mcp_lists_and_reads_coalesced_history_by_code_point(self):
        bridge = self.root / "bridge.json"
        private_json(bridge, {"priorSessions": self.manifest})
        list_sessions, list_entries, read_entry = PRIOR_SESSION_TOOL_NAMES
        listed, sessions, entries, detail, rejected = mcp_requests(bridge, [
            ("tools/list", {}),
            ("tools/call", {"name": list_sessions}),
            ("tools/call", {"name": list_entries, "arguments": {}}),
            ("tools/call", {"name": read_entry, "arguments": {
                "attemptId": "earlier", "entryIndex": 0, "offset": 1, "limit": 2}}),
            ("tools/call", {"name": read_entry, "arguments": {"attemptId": "foreign", "entryIndex": 0}}),
        ])
        self.assertEqual(tuple(tool["name"] for tool in listed["tools"][-3:]), PRIOR_SESSION_TOOL_NAMES)
        sessions, entries, detail = [json.loads(reply["content"][0]["text"]) for reply in (sessions, entries, detail)]
        self.assertEqual(sessions["items"][0]["attemptId"], "earlier")
        self.assertNotIn("recordPath", sessions["items"][0])
        self.assertEqual(entries["totalEntries"], 1)
        self.assertEqual([entry["kind"] for entry in entries["items"]], ["message"])
        self.assertEqual((detail["text"], detail["endOffset"], detail["totalChars"]), ("🧪汉", 3, 4))
        self.assertTrue(rejected["isError"])
        self.assertTrue(rejected["content"][0]["text"].startswith("unknown-attempt:"))

        private_json(bridge, {})
        listed, rejected = mcp_requests(bridge, [("tools/list", {}), ("tools/call", {"name": list_sessions})])
        self.assertFalse(set(PRIOR_SESSION_TOOL_NAMES) & {tool["name"] for tool in listed["tools"]})
        self.assertEqual(rejected, {"isError": True, "content": [
            {"type": "text", "text": "prior session history is not mounted for this turn"}]})

    def test_worker_mount_matrix(self):
        task_file = self.root / "task.txt"
        task_file.write_text("Fixture task", encoding="utf-8")
        for module in (dsh, zcode, claude, codex):
            harness = module.__package__.rsplit(".", 1)[-1]
            cases = [("initial", "write", []), ("reconstructed-new-session", "write", self.manifest),
                     ("reconstructed-new-session", "write", [])]
            if harness == "claude":
                cases.extend([("reconstructed-new-session", level, self.manifest) for level in ("read", "web")])
            else:
                cases.append(("native-session", "write", self.manifest))
            for index, (mode, level, manifest) in enumerate(cases):
                with self.subTest(harness=harness, mode=mode, level=level, history=bool(manifest)):
                    input_file = self.root / f"{harness}-{index}.json"
                    private_json(input_file, {
                        "version": 3, "taskId": "task", "attemptId": "current", "generation": 1,
                        "turnId": "current-turn", "resumeMode": mode, "context": {},
                        "previousSessionId": None if mode == "initial" else "native-previous",
                        "executionWorkspace": {"access": level, "path": str(self.root)},
                    })
                    control = {
                        "harness": harness, "privateRoot": str(input_file.with_suffix("")),
                        "stateDir": str(self.root), "directory": str(self.root),
                        "nativeRoot": str(self.root / "native"), "invocationId": "invocation",
                        "inputFile": str(input_file), "taskFile": str(task_file), "cwd": str(self.root),
                        "sessionRecordPath": None, "priorSessions": manifest,
                        "inquiry": {"resultsPath": str(self.root / "inquiry.jsonl")},
                        "spec": {"provider": "fixture", "model": "fixture", "effort": "low"},
                    }
                    request, services, _, error, parts = run_execution.worker_request(control, module)
                    self.assertIsNone(error)
                    mounted = mode == "reconstructed-new-session" and level != "web" and bool(manifest)
                    if request.session_services:
                        history = services.mount.prior_session_tools
                        configuration = json.loads(Path(services.mount.bridge_config_path).read_text())
                        self.assertEqual(configuration.get("priorSessions"), manifest if mounted else None)
                        self.assertLessEqual(set(history), set(request.session_services[0].tool_names))
                        if mounted:
                            self.check_mcp_mount(module, services.mount)
                    else:
                        history = services.inquiry.prior_session_tools
                        if harness == "codex":
                            dynamic_names = tuple(tool["name"] for tool in services.inquiry.dynamic_tools)
                            self.assertEqual(dynamic_names, (services.inquiry.answer_tool, *history))
                            self.assertEqual(services.inquiry.prior_sessions, tuple(manifest))
                        else:
                            self.assertEqual(services.inquiry.configuration.get("priorSessions"),
                                             manifest if mounted else None)
                    expected = tuple(name if harness == "codex" else f"mcp__{self.server(harness, services)}__{name}"
                                     for name in PRIOR_SESSION_TOOL_NAMES) if mounted else ()
                    self.assertEqual(history, expected)
                    for name in expected:
                        self.assertIn(name, parts.delivery)
                    self.assertEqual("unverified historical evidence" in parts.delivery, mounted)
                    self.assertEqual("omit reasoning" in parts.delivery, mounted)
                    if not mounted:
                        for name in PRIOR_SESSION_TOOL_NAMES:
                            self.assertNotIn(name, request.input_text)
                    self.assertNotIn(self.manifest[0]["recordPath"], request.input_text)

    def server(self, harness, services):
        return services.inquiry.server_name if harness == "claude" else services.mount.server_name

    def check_mcp_mount(self, module, mount):
        outcome = {"disposition": "completed", "summary": "Fixture work", "decisions": [],
                   "artifacts": [], "remaining": [], "request": None}
        listed, history, inquiry, finish = mcp_requests(mount.bridge_config_path, [
            ("tools/list", {}),
            ("tools/call", {"name": PRIOR_SESSION_TOOL_NAMES[0]}),
            ("tools/call", {"name": "hey_my_buddy_answer_inquiry", "arguments": {}}),
            ("tools/call", {"name": "hey_my_buddy_finish_turn", "arguments": outcome}),
        ])
        self.assertEqual(tuple(tool["name"] for tool in listed["tools"]), mount.bare_tools)
        self.assertEqual(json.loads(history["content"][0]["text"])["totalSessions"], 1)
        self.assertTrue(inquiry["isError"])
        calls = (
            ("history", mount.prior_session_tools[0], history["content"]),
            ("inquiry", mount.answer_tool, inquiry["content"]),
            ("finish", mount.finish_tool, finish["content"]),
        )
        kwargs = {"answer_name": mount.answer_tool, "mounted_tools": mount.bare_tools,
                  "validate_outcome": validate_outcome}
        if module is dsh:
            evidence = dsh.RootTurnEvidence("root", mount.finish_tool, mount.bridge,
                                            checkpoint_name=mount.checkpoint_tool, **kwargs)
            for ordinal, (call_id, name, content) in enumerate(calls, start=1):
                evidence.observe({"params": {"sessionId": "root", "update": {
                    "sessionUpdate": "tool_call", "toolCallId": call_id, "title": name,
                }}}, ordinal * 2)
                evidence.observe({"params": {"sessionId": "root", "update": {
                    "sessionUpdate": "tool_call_update", "toolCallId": call_id,
                    "status": "completed", "content": content,
                }}}, ordinal * 2 + 1)
        else:
            evidence = zcode.RootTurnEvidence("root", mount.input_id, mount.finish_tool, mount.bridge, **kwargs)

            def event(seq, kind, payload):
                return {"method": "session/event", "params": {
                    "sessionId": "root", "turnId": "native-turn", "seq": seq, "type": kind, "payload": payload,
                }}

            evidence.observe(event(1, "turn.started", {"inputId": mount.input_id}), 1)
            for ordinal, (call_id, name, content) in enumerate(calls, start=1):
                evidence.observe(event(ordinal * 2, "tool.updated", {
                    "kind": "scheduled", "toolCallId": call_id, "toolName": name,
                }), ordinal * 2)
                evidence.observe(event(ordinal * 2 + 1, "tool.updated", {
                    "kind": "result", "toolCallId": call_id,
                    "result": {"success": True, "truncated": False, "content": content[0]["text"]},
                }), ordinal * 2 + 1)
        self.assertEqual(evidence.verified_delivery_calls, {"inquiry", "finish"})
        self.assertEqual(evidence.receipt["outcome"], outcome)
