"""Unit tests for the private requestUserInput probe against a scripted fake server.

The fake speaks the verified wire shapes (reverse ``interaction/requestUserInput``
frames with reannounce, runtime preferences, session lifecycle) so the probe's
accept path, its strict reply schema, wrong-identity refusals and the honest
no-question limit are all exercised with no model network, no real bundle and no
shared board. Live-bundle evidence lives in the acceptance record, not here.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROBE_PATH = Path(__file__).resolve().parent.parent / "probes" / "zcode_request_input.py"

QUESTION_TEXT = "Which release codename should the probe echo?"

FAKE_SERVER = r'''#!/usr/bin/env python3
"""Scripted fake ZCode app-server: one deterministic probe turn, no model."""
import json, os, select, sys, time

case = os.environ.get("PROBE_FAKE_CASE", "ok")
report_path = os.environ["PROBE_FAKE_REPORT"]
if "--version" in sys.argv:
    print("fixture-0.16.9")
    raise SystemExit(0)

session_id, turn_id = "sess-probe-fixture", "turn-probe-fixture"
input_id, tool_call_id, request_id = None, "call-fixture-1", "perm-fixture-1"
workspace_cwd = "."
question_text = "Which release codename should the probe echo?"
nonce = os.environ["PROBE_FAKE_NONCE"]
selection = {"providerId": "fixture-api", "modelId": "fixture-model", "options": {"reasoningLevel": "low"}}
state = {"preferences": None, "frames": [], "replies": [], "methods": [], "turns": 0, "closed": False,
         "nativePins": {k: v for k, v in os.environ.items() if k.startswith(("BUDDY_", "ZCODE_"))}}


def send(value):
    print(json.dumps(value), flush=True)


def snapshot():
    return {"session": {"sessionId": session_id, "sessionKind": "interactive",
                        "workspace": {"workspacePath": workspace_cwd}},
            "settings": {"model": {"current": selection, "available": [
                {"ref": {"providerId": "fixture-api", "modelId": "fixture-model"}, "label": "Fixture model",
                 "providerLabel": "Fixture API", "contextWindow": 200000,
                 "reasoning": {"levels": [{"value": "low"}, {"value": "high"}], "defaultLevel": "low"}}]},
                         "thoughtLevel": {"current": selection["options"]["reasoningLevel"]}}}


def write_report():
    with open(report_path, "w") as stream:
        json.dump(state, stream)


def read_reply(frame_ids, wait):
    """Read controller frames until one answers a listed reverse-request id."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not ready:
            continue
        line = sys.stdin.readline()
        if not line:
            return None
        message = json.loads(line)
        if message.get("id") in frame_ids:
            state["replies"].append(message)
            return message
        if "method" in message:
            handle(message)
    return None


def ask_runtime_preferences():
    send({"id": "server-prefs", "method": "session/requestRuntimePreferences",
          "params": {"sessionId": session_id, "scope": "runtime-materialization"}})
    reply = read_reply({"server-prefs"}, 5.0)
    state["preferences"] = (reply or {}).get("result")


def run_turn():
    global input_id, state
    state["turns"] += 1
    seq = 0

    def event(kind, payload):
        nonlocal seq
        seq += 1
        send({"method": "session/event", "params": {"type": kind, "sessionId": session_id,
              "turnId": turn_id, "seq": seq, "payload": payload}})

    event("turn.started", {"inputId": input_id})
    if case == "no-question":
        event("turn.completed", {"inputId": input_id, "resultType": "success",
                                 "response": "fixture finished without asking anything"})
        send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_completed"}})
        return
    questions = [{"question": question_text, "header": "Codename", "multiSelect": False,
                  "options": [{"label": "amber", "description": "first option"},
                              {"label": "birch", "description": "second option"}]}]
    params = {"input": {"questions": questions},
              "prompt": "AskUserQuestion pauses execution to collect answers from the user",
              "questions": [{**q, "options": [{**o, "value": o["label"]} for o in q["options"]]} for q in questions],
              "requestId": request_id, "schema": {"toolName": "AskUserQuestion"},
              "sessionId": "sess-other" if case == "wrong-session" else session_id,
              "toolCallId": tool_call_id, "toolName": "AskUserQuestion",
              "turnId": "turn-other" if case == "wrong-turn" else turn_id}
    if case == "malformed":
        params = {k: v for k, v in params.items() if k != "questions"}
    event("userInput.requested", {"toolCallId": tool_call_id, "toolName": "AskUserQuestion"})
    # Two reverse frames back to back: the native client re-announces with a new
    # id, and every frame must be answerable.
    send({"id": "server-1", "method": "interaction/requestUserInput", "params": params})
    send({"id": "server-2", "method": "interaction/requestUserInput", "params": params})
    state["frames"] = ["server-1", "server-2"]
    first = read_reply({"server-1", "server-2"}, 5.0)
    second = None
    if first is not None:
        remaining = {"server-1", "server-2"} - {first.get("id")}
        second = read_reply(remaining, 5.0)
    result = (first or {}).get("result") or {}
    answers = ((result.get("content") or {}).get("answers") or {}) if isinstance(result, dict) else {}
    accepted = isinstance(result, dict) and result.get("action") == "accept"
    if accepted:
        echo = answers.get(question_text) or sorted(answers.values())[0]
        event("tool.updated", {"kind": "scheduled", "toolName": "AskUserQuestion", "toolCallId": tool_call_id})
        event("tool.updated", {"kind": "result", "toolCallId": tool_call_id,
                               "result": {"success": True, "truncated": False,
                                          "content": f"User has answered your questions: \"{question_text}\"=\"{echo}\""}})
        event("userInput.resolved", {"toolCallId": tool_call_id, "resolution": "accept"})
        event("turn.completed", {"inputId": input_id, "resultType": "success",
                                 "response": f"PROBE-ECHO: {echo}"})
    else:
        event("tool.updated", {"kind": "error", "toolCallId": tool_call_id, "error": "declined by Host"})
        event("turn.completed", {"inputId": input_id, "resultType": "success",
                                 "response": "the question was declined; the fixture turn ends anyway"})
    send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_completed"}})
    write_report()


def handle(message):
    global input_id, workspace_cwd
    method, params = message.get("method"), message.get("params", {})
    request_id = message.get("id")
    state["methods"].append(method)
    if method == "runtime/capabilities":
        send({"id": request_id, "result": {"independentPlanState": True}})
    elif method == "session/create":
        workspace_cwd = params["workspace"]["workspacePath"]
        ask_runtime_preferences()
        send({"id": request_id, "result": snapshot()})
    elif method in ("session/setModel", "session/setThoughtLevel"):
        if method == "session/setModel":
            selection.update(params["model"])
        else:
            selection["options"]["reasoningLevel"] = params["thoughtLevel"]
        send({"id": request_id, "result": snapshot()})
    elif method == "session/subscribe":
        send({"id": request_id, "result": {"sessionId": session_id, "events": [], "eventSeq": 0}})
    elif method == "session/send":
        input_id = params["inputId"]
        send({"id": request_id, "result": {"sessionId": session_id, "accepted": True, "stateRevision": 1}})
        run_turn()
    elif method == "session/close":
        state["closed"] = True
        send({"id": request_id, "result": {"closed": True}})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})


for line in sys.stdin:
    handle(json.loads(line))
write_report()
'''


def load_probe():
    import importlib.util
    spec = importlib.util.spec_from_file_location("zcode_request_input_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeFixtureCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-input-probe-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.probe = load_probe()
        self.fake = self.root / "fake-app-server.py"
        self.fake.write_text(FAKE_SERVER)
        self.fake.chmod(0o755)
        self.builtin = self.root / "builtin.json"
        self.personal = self.root / "personal.json"
        self.builtin.write_text(json.dumps({"config": {"providerConfigRules": {"templateRules": [], "providerRules": []}}}))
        self.personal.write_text(json.dumps({"config": {"providerConfigRules": {"providerRules": [
            {"providerId": "fixture-api", "config": {"access": {"type": "api-key", "apiKey": "fixture-secret"}}}]}}}))
        self.nonce = "probe-nonce-4f0c2a"
        self.fake_report = self.root / "fake-report.json"

    def run_fake_probe(self, case: str) -> tuple[dict, dict]:
        # The fake's control variables ride the environment; the provider files are
        # passed explicitly because the probe strips every inherited ZCODE_*/BUDDY_*
        # pin before building its private child environment.
        environment = {k: v for k, v in os.environ.items() if not k.startswith("BUDDY_")
                       and not k.startswith("ZCODE_") and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        environment.update({"PROBE_FAKE_CASE": case, "PROBE_FAKE_REPORT": str(self.fake_report),
                            "PROBE_FAKE_NONCE": self.nonce, "PROBE_FAKE_CWD": str(self.root / "scratch")})
        with mock.patch.dict(os.environ, environment):
            report = self.probe.run_probe(self.root / "probe", cwd=self.root / "scratch",
                                          provider="fixture-api", model="fixture-model", effort="low",
                                          nonce=self.nonce, wall_seconds=30.0, zcode_cli=str(self.fake),
                                          builtin_provider=str(self.builtin), personal_provider=str(self.personal))
        fake = json.loads(self.fake_report.read_text()) if self.fake_report.exists() else {}
        return report, fake


class ProbeAcceptFlowTests(ProbeFixtureCase):
    def test_accept_resumes_the_same_turn_with_the_nonce_in_output(self):
        report, fake = self.run_fake_probe("ok")
        self.assertEqual(report["status"], "ok", report.get("error"))
        checks = report["checks"]
        for key in ("questionInduced", "sameSession", "singleTurnResumed", "nonceInOutput",
                    "turnCompleted", "turnCompletedSameTurn", "sessionClosed", "processExitedZero"):
            self.assertTrue(checks[key], (key, checks))
        captured = report["capturedUserInput"][0]
        self.assertEqual(captured["requestId"], "perm-fixture-1")
        self.assertEqual(captured["sessionId"], "sess-probe-fixture")
        self.assertEqual(captured["turnId"], "turn-probe-fixture")
        self.assertEqual(captured["toolCallId"], "call-fixture-1")
        self.assertEqual(captured["toolName"], "AskUserQuestion")
        self.assertEqual(captured["questions"][0]["question"], QUESTION_TEXT)
        self.assertEqual([o["label"] for o in captured["questions"][0]["options"]], ["amber", "birch"])
        # Only one native turn ever started and completed; the question tool result
        # for the captured call id arrived between those two events.
        self.assertEqual(len(report["turnStarted"]), 1)
        scheduled = [e for e in report["toolEvents"] if e["toolKind"] == "scheduled"]
        results = [e for e in report["toolEvents"] if e["toolKind"] == "result"]
        self.assertEqual(scheduled[0]["toolCallId"], captured["toolCallId"])
        self.assertLess(report["turnStarted"][0]["seq"], scheduled[0]["seq"])
        self.assertLess(results[0]["seq"], report["turnCompleted"][0]["seq"])
        kinds = [e["kind"] for e in report["userInputEvents"]]
        self.assertIn("userInput.requested", kinds)
        self.assertIn("userInput.resolved", kinds)

    def test_the_probe_answers_the_preferences_and_every_reannounced_frame(self):
        _report, fake = self.run_fake_probe("ok")
        self.assertEqual(fake["frames"], ["server-1", "server-2"])
        answered = {reply["id"] for reply in fake["replies"]}
        self.assertTrue({"server-1", "server-2"} <= answered, fake["replies"])
        accepts = [reply for reply in fake["replies"]
                   if isinstance(reply.get("result"), dict) and reply["result"].get("action") == "accept"]
        self.assertEqual(len(accepts), 2)
        for reply in accepts:
            result = reply["result"]
            self.assertEqual(set(result), {"action", "content"}, "the accept result must stay schema-strict")
            self.assertEqual(result["content"]["answers"], {QUESTION_TEXT: self.nonce})
        self.assertEqual(fake["preferences"]["askUserQuestionAutoResolutionEnabled"], False)
        self.assertEqual(fake["preferences"]["modelContextBudgetStrategy"], "preflight-v1")

    def test_inherited_buddy_and_native_pins_never_reach_the_probe_child(self):
        _report, fake = self.run_fake_probe("ok")
        pins = fake["nativePins"]
        self.assertEqual(sorted(pins), ["BUDDY_ZCODE_CLI", "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE",
                                        "ZCODE_LOG_CONSOLE", "ZCODE_LOG_DIR", "ZCODE_MODEL_TELEMETRY_ENABLED",
                                        "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", "ZCODE_SESSION_DB_PATH",
                                        "ZCODE_STORAGE_DIR"], pins)
        self.assertEqual(pins["ZCODE_LOG_CONSOLE"], "0")
        for key in ("ZCODE_LOG_DIR", "ZCODE_SESSION_DB_PATH", "ZCODE_STORAGE_DIR"):
            self.assertIn(str(self.root / "probe"), pins[key], (key, pins[key]))

    def test_runtime_preferences_reply_shape_is_strict(self):
        _report, fake = self.run_fake_probe("ok")
        self.assertEqual(set(fake["preferences"]),
                         {"nativeSearchEnhancementsEnabled", "memoryEnabled",
                          "askUserQuestionAutoResolutionEnabled", "modelContextBudgetStrategy"})


class ProbeRefusalTests(ProbeFixtureCase):
    def test_a_reverse_request_for_another_session_is_declined_and_never_accepted(self):
        report, fake = self.run_fake_probe("wrong-session")
        self.assertEqual(report["status"], "ok", report.get("error"))
        self.assertEqual(report["capturedUserInput"], [])
        self.assertEqual(len(report["refusedFrames"]), 2)
        self.assertIn("different session", report["refusedFrames"][0]["reason"])
        declines = [reply for reply in fake["replies"] if reply.get("id") in ("server-1", "server-2")
                    and isinstance(reply.get("result"), dict) and reply["result"].get("action") == "decline"]
        self.assertEqual(len(declines), 2)
        self.assertFalse(report["checks"]["questionInduced"])
        self.assertTrue(report["checks"]["turnCompleted"])

    def test_a_reverse_request_for_another_turn_is_declined(self):
        report, _fake = self.run_fake_probe("wrong-turn")
        self.assertEqual(report["status"], "ok", report.get("error"))
        self.assertEqual(report["capturedUserInput"], [])
        self.assertIn("different turn", report["refusedFrames"][0]["reason"])

    def test_a_malformed_reverse_request_is_declined_not_crashed(self):
        report, fake = self.run_fake_probe("malformed")
        self.assertEqual(report["status"], "ok", report.get("error"))
        self.assertEqual(report["capturedUserInput"], [])
        self.assertIn("questions", report["refusedFrames"][0]["reason"])
        frame_replies = [reply for reply in fake["replies"] if reply.get("id") in ("server-1", "server-2")]
        self.assertEqual(len(frame_replies), 2)
        self.assertTrue(all(isinstance(reply.get("result"), dict)
                            and reply["result"].get("action") == "decline" for reply in frame_replies))

    def test_a_turn_without_any_question_reports_the_limit_honestly(self):
        report, fake = self.run_fake_probe("no-question")
        self.assertEqual(report["status"], "ok", report.get("error"))
        self.assertEqual(report["capturedUserInput"], [])
        self.assertEqual(report["refusedFrames"], [])
        self.assertEqual(fake["frames"], [])
        self.assertFalse(report["checks"]["questionInduced"])
        self.assertFalse(report["checks"]["nonceInOutput"])


class ProbeUnitTests(unittest.TestCase):
    def setUp(self):
        self.probe = load_probe()

    def test_accept_answer_is_keyed_by_exact_question_text(self):
        questions = [{"question": "Q one?", "options": [{"label": "a"}, {"label": "b"}]},
                     {"question": "Q two?", "options": [{"label": "c"}, {"label": "d"}]}]
        result = self.probe.accept_answer(questions, "nonce-x")
        self.assertEqual(result, {"action": "accept", "content": {"answers": {"Q one?": "nonce-x", "Q two?": "nonce-x"}}})

    def test_accept_answer_declines_when_no_question_text_survives(self):
        result = self.probe.accept_answer([{"options": [{"label": "a"}, {"label": "b"}]}], "nonce-x")
        self.assertEqual(result["action"], "decline")

    def test_validate_identity_rejects_each_documented_malformation(self):
        good = {"questions": [{"question": "Q?", "options": [{"label": "a"}, {"label": "b"}]}],
                "sessionId": "sess-1", "turnId": "turn-1", "toolCallId": "call-1", "requestId": "perm-1"}
        questions, reason = self.probe.validate_identity(good, "sess-1", "turn-1")
        self.assertIsNone(reason)
        self.assertEqual(questions[0]["question"], "Q?")
        bad_cases = [
            ({**good, "questions": []}, "questions"),
            ({**good, "questions": "nope"}, "questions"),
            ({**good, "toolCallId": ""}, "tool call"),
            ({**good, "requestId": 7}, "request identity"),
            ({**good, "sessionId": "sess-2"}, "different session"),
            ({**good, "turnId": "turn-2"}, "different turn"),
            ({**good, "questions": [{"question": "Q?", "options": [{"label": "only one"}]}]}, "option list"),
            ("not an object", "not an object"),
        ]
        for params, expected in bad_cases:
            _questions, reason = self.probe.validate_identity(params, "sess-1", "turn-1")
            self.assertIsNotNone(reason, params)
            self.assertIn(expected, reason)

    def test_validate_identity_accepts_a_frame_without_a_live_turn_binding(self):
        params = {"questions": [{"question": "Q?", "options": [{"label": "a"}, {"label": "b"}]}],
                  "sessionId": "sess-1", "toolCallId": "call-1", "requestId": "perm-1"}
        questions, reason = self.probe.validate_identity(params, "sess-1", None)
        self.assertIsNone(reason)
        self.assertEqual(len(questions), 1)


if __name__ == "__main__":
    unittest.main()
