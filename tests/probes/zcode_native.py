#!/usr/bin/env python3
"""Explicit installed-ZCode checks against a localhost model fixture.

Run from the checkout root:
    PYTHONPATH=src:tests/python python tests/probes/zcode_native.py -v
A single check can be selected with InstalledZcodeTests.<test_name>.

This starts the installed macOS ZCode bundle through Node and sandbox-exec,
a loopback HTTP model stub, and the source role controller/MCP processes.
The native CLI uses a private HOME, provider files, session roots and logs;
the sandbox permits only loopback network and blocks shared configuration
writes. No user credentials, real provider requests or paid model calls are
used. Missing ZCode, Node or sandbox-exec is a failed explicit probe, not a
skipped check. These native session, tool and catalog facts are outside the
regular check suite and require Host verification after ZCode changes.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase

CLI = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")
BUILTIN = CLI.parent.parent / "config/provider/zcode-builtin.json"


class InstalledZcodeTests(ZcodeFixtureCase):
    def setUp(self):
        super().setUp()
        self.requests = []
        captured = self.requests
        scenario = self
        self.omit_first_request = False
        self.correct_failed_finish = False
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                tools = [x["function"]["name"] for x in data.get("tools", [])]
                last_user = max((i for i, m in enumerate(data["messages"]) if m["role"] == "user"), default=-1)
                tool_results = [m for m in data["messages"][last_user + 1:] if m["role"] == "tool"]
                called = bool(tool_results)
                rejected = called and 'The required parameter `request` is missing' in json.dumps(tool_results[-1])
                captured.append({"model": data["model"], "tools": tools, "missingRequestRejected": rejected})
                finish = next((name for name in tools if name.endswith("__hey_my_buddy_finish_turn")), None)
                outcome = {"disposition": "completed", "summary": "localhost native fixture completed",
                           "remaining": [], "decisions": [], "artifacts": [], "request": None}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                def chunk(delta, reason=None):
                    body = {"id": "chatcmpl-local-only", "object": "chat.completion.chunk", "created": 1,
                            "model": data["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                    self.wfile.write(("data: " + json.dumps(body) + "\n\n").encode())
                    self.wfile.flush()
                if finish and (not called or scenario.correct_failed_finish and rejected):
                    if scenario.omit_first_request and not called:
                        outcome.pop("request")
                    chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": f"call_native_finish_{len(tool_results) + 1}",
                           "type": "function", "function": {"name": finish, "arguments": json.dumps(outcome)}}]})
                    chunk({}, "tool_calls")
                else:
                    chunk({"role": "assistant", "content": "Native fixture finished."})
                    chunk({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        shutil.copyfile(BUILTIN, self.builtin)
        self.personal.write_text(json.dumps({"schemaVersion": 1, "config": {
            "providerOrder": ["fixture-api"], "providerConfigRules": {"providerRules": [{
                "providerId": "fixture-api", "templateId": "zai-standard-api", "providerName": "Local test fixture",
                "enabled": True, "config": {"group": "standard-personal", "access": {"type": "api-key", "apiKey": "local-non-secret"},
                    "api": {"type": "openai-chat-completions", "baseUrl": f"http://127.0.0.1:{self.http.server_port}"},
                    "personalModelIds": ["GLM-5.3-Flash"]}}]},
            "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
            "defaultModelSelection": {"providerId": "fixture-api", "modelId": "GLM-5.3-Flash", "options": {"reasoningLevel": "low"}},
        }}))
        (self.cwd / ".zcode").mkdir()
        (self.cwd / ".zcode/config.json").write_text(json.dumps({"plugins": {"enabled": False}, "features": {"skill": False, "memory": False}}))
        self.environment.update(HMB_ZCODE_CLI=str(CLI), ZCODE_DATA_BASE_DIR=str(self.root / "account-empty"))
        # Native code is real. The fixture permits only loopback network and
        # prevents writes to shared ZCode settings or agent configuration.
        wrapper = self.root / "native-cli"
        shared_paths = " ".join(f"(subpath {json.dumps(str(Path.home() / name))})" for name in (".zcode", ".agents"))
        profile = ('(version 1) (allow default) (deny network-outbound (remote ip "*:*")) '
                   '(allow network-outbound (remote ip "localhost:*")) '
                   f'(deny file-write* {shared_paths})')
        command = [shutil.which("sandbox-exec"), "-p", profile, shutil.which("node"), str(CLI)]
        wrapper.write_text("#!/bin/sh\nexport " + shlex.join([
            "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=" + str(self.builtin),
            "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=" + str(self.personal),
            "ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE=" + str(BUILTIN),
        ]) + "\nexec " + shlex.join(command) + ' "$@"\n')
        wrapper.chmod(0o700)
        self.environment["HMB_ZCODE_CLI"] = str(wrapper)

    def test_native_root_finish_and_exact_resume_keep_coding_tools(self):
        context = self.context(timeout=30)
        context.spec["model"] = "GLM-5.3-Flash"
        _, first = self.execute(context)
        self.assertEqual(first.status, "ok", first.to_report())
        first_turn = first.result["turn"]
        self.assertEqual(first_turn["outcome"]["summary"], "localhost native fixture completed")
        self.assertEqual(first_turn["provenance"]["settlement"], "session-closed")
        self.assertIn("Bash", self.requests[0]["tools"])
        self.assertTrue(any(name in self.requests[0]["tools"] for name in ("Agent", "Task")), self.requests[0]["tools"])
        resumed = self.context(index=2, previous=first_turn["sessionId"], timeout=30)
        resumed.spec["model"] = "GLM-5.3-Flash"
        second_handle, second = self.execute(resumed)
        self.assertEqual(second.status, "ok", second.to_report())
        second_turn = second.result["turn"]
        self.assertEqual(second_turn["sessionId"], first_turn["sessionId"])
        self.assertNotEqual(second_turn["provenance"]["nativeTurnId"], first_turn["provenance"]["nativeTurnId"])
        self.assertEqual(second_handle.result.buddy.checked.effort.value, "low")
        self.assertEqual(second.result["requested"], {
            key: value.value for key in ("provider", "model", "effort")
            if (value := getattr(second_handle.result.buddy.checked, key)) is not None})
        self.assertNotIn("observed", second.result)
        self.assertEqual(second.result["resolved"], {
            key: value.value for key in ("provider", "model", "effort")
            if (value := getattr(second_handle.result.buddy.checked, key)) is not None})
        finish_names = {name for request in self.requests for name in request["tools"] if name.endswith("__hey_my_buddy_finish_turn")}
        self.assertEqual(len(finish_names), 2, "continuation did not receive its own private finish bridge")

    def test_native_missing_request_can_be_corrected_in_the_same_root_turn(self):
        self.omit_first_request = self.correct_failed_finish = True
        context = self.context(timeout=30)
        context.spec["model"] = "GLM-5.3-Flash"
        _, result = self.execute(context)
        self.assertEqual(result.status, "ok", result.to_report())
        self.assertTrue(result.shutdown_confirmed)
        self.assertTrue(any(request["missingRequestRejected"] for request in self.requests))
        self.assertEqual(result.result["turn"]["provenance"]["toolCallId"], "call_native_finish_2")
        self.assertEqual(result.result["turn"]["outcome"]["request"], None)

    def test_native_failed_turn_can_reconstruct_with_no_verified_previous_session(self):
        self.omit_first_request = True
        failed = self.context(timeout=30)
        failed.spec["model"] = "GLM-5.3-Flash"
        failed_handle, first = self.execute(failed)
        self.assertEqual(first.status, "failed", first.to_report())
        self.assertEqual(first.result.get("code"), "finish-tool-failed")
        self.assertTrue(first.shutdown_confirmed)
        self.assertNotIn("turn", first.result)
        self.assertFalse(failed.turn_output_file().exists())
        self.assertTrue(any(request["missingRequestRejected"] for request in self.requests))
        root = Path(failed_handle.role_run_control["nativeRoot"])
        previous = next(json.loads(path.read_text())["sessionId"] for path in root.glob("*.json"))
        self.omit_first_request = False
        recovered = self.context(index=2, mode="reconstructed-new-session", effort="high", timeout=30)
        recovered.spec["model"] = "GLM-5.3-Flash"
        second_handle, second = self.execute(recovered)
        self.assertEqual(second.status, "ok", second.to_report())
        turn = second.result["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertIsNone(turn["previousSessionId"])
        self.assertNotEqual(turn["sessionId"], previous)
        self.assertEqual(second_handle.result.buddy.checked.effort.value, "high")
        self.assertEqual(turn["provenance"]["settlement"], "session-closed")

    def test_native_catalog_has_per_model_efforts_without_model_requests(self):
        with mock.patch.dict(os.environ, self.environment, clear=True):
            discovered = self.description.discover_models()
        provider = next(x for x in discovered["providers"] if x["provider"] == "fixture-api")
        model = next(x for x in provider["models"] if x["id"] == "GLM-5.3-Flash")
        self.assertEqual(model["efforts"], ["low", "high", "max"])
        self.assertIn("text", model["inputModalities"])
        self.assertTrue(model["available"])
        self.assertEqual(provider["harness"], "zcode")
        self.assertEqual(provider["packageVersion"], discovered["harnessVersion"])
        self.assertEqual(self.requests, [])

    def test_native_all_provider_disabled_catalog_is_a_complete_empty_observation(self):
        value = json.loads(self.personal.read_text())
        value["config"]["providerConfigRules"]["providerRules"][0]["enabled"] = False
        self.personal.write_text(json.dumps(value))
        with mock.patch.dict(os.environ, self.environment, clear=True):
            discovered = self.description.discover_models()
        self.assertEqual(discovered["providers"], [])
        self.assertEqual(discovered["discoveries"],
                         [{"harness": "zcode", "status": "complete", "accountStatus": "not-applicable"}])
        self.assertTrue(any("empty" in warning for warning in discovered["warnings"]))
        self.assertEqual(self.requests, [], "discovery must never call the model")


if __name__ == "__main__":
    unittest.main()
