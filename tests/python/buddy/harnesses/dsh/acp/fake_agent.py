"""Synthetic ACP agent for exercising the standalone client without DSH.

Speaks the wire shapes observed from the installed agent and confirmed against
the public schema: initialize, session/new (the ``mcpServers`` key is required;
a stdio ``env`` that is not an array is recorded and the server is not mounted,
mirroring the installed agent's silent no-mount), set_config_option echo,
session/list, session/resume, session/close, prompt with interleaved permission
requests and notifications, and the cancel notification. Fault switches let a
test inject delayed responses, duplicate responses, garbage frames, a
silently-exiting leader with a surviving group member, and a slow EOF. At
startup it verifies it runs under a forced private HOME and DSH_HOME and exits
when it has not.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def log(log_path: Path, event: dict) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **event}
    with log_path.open("a") as handle:
        handle.write(json.dumps(entry, default=str, sort_keys=True) + "\n")


def check_private_home(log_path: Path) -> bool:
    """Refuse to run outside a forced private HOME/DSH_HOME; the check result is
    the first log line so a test can assert the launch contract held.

    A private root anywhere under the user's home tree is legitimate - the
    product's own state lives there - so this tripwire checks the exact
    identities: the real home itself and the default DSH home.
    """
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    home = os.environ.get("HOME")
    dsh_home = os.environ.get("DSH_HOME")
    ok = bool(home and dsh_home
              and Path(home).resolve() != Path(real_home).resolve()
              and Path(dsh_home).resolve() != (Path(real_home) / ".dsh").resolve())
    log(log_path, {"event": "home-check", "ok": ok,
                   "homeSet": home is not None, "dshHomeSet": dsh_home is not None})
    return ok


def config_options(model_value: str, effort_value: str) -> list:
    return [
        {"id": "model", "name": "Model", "category": "model", "type": "select",
         "currentValue": model_value,
         "options": [{"group": "fake", "name": "Fake", "options": [
             {"value": '["fake","m1"]', "name": "Fake M1"},
             {"value": '["fake","m2"]', "name": "Fake M2"}]}]},
        {"id": "reasoning_effort", "name": "Reasoning effort", "category": "thought_level",
         "type": "select", "currentValue": effort_value,
         "options": [{"value": "off", "name": "off"}, {"value": "low", "name": "low"},
                     {"value": "high", "name": "high"}, {"value": "max", "name": "max"}]},
    ]


class FakeAgent:
    #: Hostile JSON-RPC shapes a test can inject before a real answer: a wrong
    #: version, a missing version, dict and bool ids (a bool id must never alias
    #: this client's integer ids), a response with neither result nor error, one
    #: with both, and a well-formed response for an id this client never sent.
    HOSTILE_FRAMES = [
        '{"jsonrpc":"1.0","id":1,"result":{"evil":true}}',
        '{"id":1,"result":{"evil":true}}',
        '{"jsonrpc":"2.0","id":{"x":1},"result":{}}',
        '{"jsonrpc":"2.0","id":true,"result":{}}',
        '{"jsonrpc":"2.0","id":1}',
        '{"jsonrpc":"2.0","id":1,"result":{},"error":{"code":1,"message":"both"}}',
        '{"jsonrpc":"2.0","id":999,"result":{"evil":"unrelated"}}',
    ]

    def __init__(self, args) -> None:
        self.log_path = Path(args.log)
        self.delays = dict(item.rsplit(":", 1) for item in (args.delay or []))
        self.garbage_on = set(args.garbage_on or [])
        self.duplicate_on = set(args.duplicate_on or [])
        self.die_before_answer = set(args.die_before_answer or [])
        self.orphan_on = set(args.orphan_on or [])
        self.respond_then_exit = set(args.respond_then_exit or [])
        self.raw_frame_on = set(args.raw_frame or [])
        self.freeze_on = set(args.freeze_stdin_on or [])
        self.survive_eof = args.survive_eof
        self.allowed_title = args.allowed_title
        self.fs_path = args.fs_path
        self.option_count = args.option_count
        self.spam_notifications = args.spam_notifications
        self.malformed_permission = args.malformed_permission
        self.error_data_on = set(args.error_data_on or [])
        self.freeze_mid_prompt = args.freeze_mid_prompt
        # -- the native-run driver modes (additive; the legacy path above is unchanged)
        self.prompt_mode = args.prompt_mode
        self.final_answers = list(args.final_answer or [])
        self.emit_unknown_update = args.emit_unknown_update
        self.bridge_config = args.bridge_config
        self.finish_tool = args.finish_tool
        self.checkpoint_tool = args.checkpoint_tool
        self.governed_permission = args.governed_permission
        self.finish_fail_then_retry = args.finish_fail_then_retry
        self.no_finish = args.no_finish
        self.forge_receipt = args.forge_receipt
        self.stop_reason = args.stop_reason
        self.hang_prompt = args.hang_prompt
        self.park_prompt = args.park_prompt
        self.emit_tool_update = args.emit_tool_update
        self.bare_final = args.bare_final
        self.session_record = args.session_record
        self.stderr_note = args.stderr_note
        self.emit_foreign_chunk = args.emit_foreign_chunk
        self.spam_unknown_kinds = args.spam_unknown_kinds
        self.late_tool_update = args.late_tool_update
        # The cooperative-checkpoint inquiry script (additive): wait for a queued
        # Host question, checkpoint to deliver it, answer it through the real
        # session-tool rules, or forge the checkpoint receipt when told to.
        self.wait_for_inquiry = args.wait_for_inquiry
        self.answer_inquiry = args.answer_inquiry
        self.forge_checkpoint = args.forge_checkpoint
        self.last_session_id = None
        self.cancel_event = threading.Event()
        self.prompt_count = 0
        self._stop_reading = threading.Event()
        # Handler threads run concurrently; their stdout writes must not
        # interleave, or the client would see corrupted frames.
        self._send_lock = threading.Lock()
        self.lock = threading.Lock()
        self.next_id = 0
        self.client_pending: dict = {}
        self.sessions: dict = {}
        self.session_seq = 0
        self.stubs: list = []

    def send(self, payload: dict) -> None:
        line = json.dumps(payload) + "\n"
        with self._send_lock:
            sys.stdout.write(line)
            sys.stdout.flush()
        log(self.log_path, {"dir": "out", "raw": payload})

    # -- MCP stub mounting -------------------------------------------------

    def mount_stub(self, entry: dict) -> dict:
        name = entry.get("name")
        env = entry.get("env")
        if env is not None and not isinstance(env, list):
            # The installed agent accepts a mapping env and silently fails to
            # mount; mirror that observable behavior exactly.
            log(self.log_path, {"event": "stub-not-mounted", "name": name,
                                "envShape": type(env).__name__})
            return {"name": name, "mounted": False, "envShape": type(env).__name__}
        argv = [entry["command"], *entry.get("args", [])]
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, start_new_session=False,
                                       env={"PATH": os.environ.get("PATH", os.defpath),
                                            "HOME": os.environ["HOME"]})
        except (OSError, KeyError) as error:
            return {"name": name, "mounted": False, "error": str(error)}
        self.stubs.append(process)

        def stub_round(payload: dict) -> dict:
            process.stdin.write((json.dumps(payload) + "\n").encode())
            process.stdin.flush()
            return json.loads(process.stdout.readline())

        try:
            init = stub_round({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})
            process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
            tools = stub_round({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = [tool.get("name") for tool in tools.get("result", {}).get("tools", [])]
        except (OSError, ValueError) as error:
            return {"name": name, "mounted": False, "error": str(error)}
        log(self.log_path, {"event": "stub-mounted", "name": name, "tools": names,
                            "pid": process.pid})
        return {"name": name, "mounted": True, "tools": names}

    def close_stubs(self) -> None:
        for process in self.stubs:
            try:
                process.stdin.close()
            except (OSError, AttributeError):
                pass
        for process in self.stubs:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.stubs = []

    # -- client requests -----------------------------------------------------

    def request_client(self, method: str, params: dict, timeout: float = 15.0) -> dict:
        with self.lock:
            self.next_id += 1
            request_id = self.next_id
            done = threading.Event()
            self.client_pending[request_id] = [done, None]
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if not done.wait(timeout):
            with self.lock:
                self.client_pending.pop(request_id, None)
            return {"timeout": True}
        with self.lock:
            entry = self.client_pending.pop(request_id, None)
        return (entry[1] if entry else None) or {}

    def notify_client(self, method: str, params: dict) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- agent-side request dispatch -------------------------------------------

    def handle(self, message: dict) -> None:
        log(self.log_path, {"dir": "in", "raw": message})
        method = message.get("method")
        if method is None:
            return
        if "id" not in message:
            if method == "session/cancel":
                log(self.log_path, {"event": "cancel-received",
                                    "sessionId": (message.get("params") or {}).get("sessionId")})
                self.cancel_event.set()
            return
        request_id = message["id"]
        params = message.get("params") or {}
        if method in self.die_before_answer:
            log(self.log_path, {"event": "dying-before-answer", "method": method})
            self.close_stubs()
            os._exit(0)
        if method in self.orphan_on:
            # Spawn a same-group child that outlives this leader, then exit
            # without answering: the leader is gone but the group is not.
            sleeper = subprocess.Popen(["/bin/sleep", "20"], start_new_session=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            log(self.log_path, {"event": "orphan-spawned", "pid": sleeper.pid})
            self.close_stubs()
            os._exit(0)
        if method in self.delays:
            time.sleep(float(self.delays[method]))
        if method in self.garbage_on:
            sys.stdout.write("this line is not json\n")
            sys.stdout.write('{"half": "open"}\n')  # valid JSON object, but no jsonrpc member
            sys.stdout.write('[1, 2, 3]\n')  # valid JSON, but not an object
            sys.stdout.flush()
        if method in self.raw_frame_on:
            for raw in self.HOSTILE_FRAMES:
                sys.stdout.write(raw + "\n")
            sys.stdout.flush()
        if method in self.freeze_on:
            # Keep running but stop reading the client's stdin on the next loop
            # tick; the serve loop stays alive without consuming any input.
            self._stop_reading.set()
            log(self.log_path, {"event": "stdin-frozen", "method": method})
        if method == "session/prompt":
            if self.park_prompt:
                log(self.log_path, {"event": "parked-prompt"})
                threading.Event().wait()  # never returns: only the group stop ends this
            if self.hang_prompt:
                log(self.log_path, {"event": "hanging-prompt", "until": "session/cancel"})
                self.cancel_event.wait(timeout=120)
                self.send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "cancelled"}})
                return
            if self.prompt_mode == "final":
                self.run_final_prompt(message)
                if method in self.respond_then_exit:
                    os._exit(0)
                return
            if self.prompt_mode == "governed":
                self.run_governed_prompt(message)
                if method in self.respond_then_exit:
                    os._exit(0)
                return
            self.run_prompt(message)
            if method in self.respond_then_exit:
                os._exit(0)
            return
        payload = self.answer(method, params)
        self.send({"jsonrpc": "2.0", "id": request_id, **payload})
        if method in self.duplicate_on:
            self.send({"jsonrpc": "2.0", "id": request_id, **payload})
            log(self.log_path, {"event": "duplicate-response", "method": method})
        if method in self.respond_then_exit:
            log(self.log_path, {"event": "respond-then-exit", "method": method})
            self.close_stubs()
            os._exit(0)

    def answer(self, method: str, params: dict) -> dict:
        if method == "initialize":
            return {"result": {
                "protocolVersion": 1, "agentInfo": {"name": "fake-harness-acp", "version": "0.0.1"},
                "agentCapabilities": {"mcpCapabilities": {"http": True},
                                      "promptCapabilities": {"image": False, "audio": False,
                                                             "embeddedContext": False},
                                      "sessionCapabilities": {"close": {}, "list": {}, "resume": {}}},
                "authMethods": []}}
        if method == "session/new":
            if "mcpServers" not in params:
                return {"error": {"code": -32602, "message": "mcpServers is required"}}
            mounted = [self.mount_stub(entry) for entry in params.get("mcpServers") or []]
            self.session_seq += 1
            session_id = f"fake-session-{self.session_seq}"
            self.sessions[session_id] = {"cwd": params.get("cwd"),
                                         "model": '["fake","m1"]', "effort": "high"}
            return {"result": {"sessionId": session_id, "modes": None,
                               "configOptions": config_options(self.sessions[session_id]["model"],
                                                               self.sessions[session_id]["effort"]),
                               "mountedMcp": mounted}}
        if method == "session/set_config_option":
            session = self.sessions.get(params.get("sessionId"))
            if session is None:
                return {"error": {"code": -32602, "message": "unknown session"}}
            if params.get("configId") == "model":
                session["model"] = params.get("value")
            elif params.get("configId") == "reasoning_effort":
                session["effort"] = params.get("value")
            return {"result": {"configOptions": config_options(session["model"], session["effort"])}}
        if method == "session/list" and "session/list" in self.error_data_on:
            return {"error": {"code": -32000, "message": "SAFE-TEST-MARKER-in-message",
                              "data": {"marker": "SAFE-TEST-MARKER-in-data"}}}
        if method == "session/list":
            return {"result": {"sessions": [
                {"sessionId": session_id, "cwd": session["cwd"], "title": None,
                 "updatedAt": None} for session_id, session in sorted(self.sessions.items())]}}
        if method == "session/resume":
            session = self.sessions.get(params.get("sessionId"))
            if session is None:
                return {"error": {"code": -32602, "message": "no such session"}}
            return {"result": {"modes": None,
                               "configOptions": config_options(session["model"], session["effort"])}}
        if method == "session/close":
            self.sessions.pop(params.get("sessionId"), None)
            self.close_stubs()
            return {"result": {}}
        return {"error": {"code": -32601, "message": f"Method not found: {method}"}}

    def run_prompt(self, message: dict) -> None:
        request_id = message["id"]
        params = message.get("params") or {}
        session_id = params.get("sessionId")
        if self.freeze_mid_prompt:
            # Park before asking: the client's permission answer can then never
            # be consumed, so its non-delivery becomes a deterministic fact.
            self._stop_reading.set()
            log(self.log_path, {"event": "stdin-frozen", "method": "mid-prompt"})
        if self.malformed_permission:
            # Field shapes the client must survive: toolCall is a list, options
            # is an int, and the answer must still go out and be recorded.
            first = self.request_client("session/request_permission", {
                "sessionId": session_id,
                "toolCall": ["bad"],
                "options": 3})
            log(self.log_path, {"event": "permission-answer-malformed", "answer": first})
        else:
            first = self.request_client("session/request_permission", {
                "sessionId": session_id,
                "toolCall": {"toolCallId": "call_fake_1", "kind": "execute", "title": "run-command"},
                "options": [{"optionId": "opt-allow", "kind": "allow_once", "name": "Allow"},
                            {"optionId": "opt-reject", "kind": "reject_once", "name": "Reject"}]})
            log(self.log_path, {"event": "permission-answer-1", "answer": first})
        if self.freeze_mid_prompt:
            return
        self.notify_client("session/update", {"sessionId": session_id,
                                              "update": {"sessionUpdate": "agent_message_chunk",
                                                         "content": {"type": "text", "text": "working"}}})
        second = self.request_client("session/request_permission", {
            "sessionId": session_id,
            "toolCall": {"toolCallId": "call_fake_2", "kind": "edit",
                         "title": self.allowed_title or "write-file"},
            "options": [{"optionId": "opt-allow-only", "kind": "allow_once", "name": "Allow"}]
            + [{"optionId": f"opt-filler-{index}", "kind": f"kind-{index}",
                "name": f"option {index}"}
               for index in range(max(1, self.option_count) - 1)]})
        log(self.log_path, {"event": "permission-answer-2", "answer": second})
        fs = self.request_client("fs/read_text_file", {"sessionId": session_id, "path": self.fs_path})
        log(self.log_path, {"event": "fs-answer", "answer": fs})
        for update in (
            {"sessionUpdate": "usage_update", "used": 900, "size": 1000000},
            {"sessionUpdate": "tool_call", "toolCallId": "call_fake_1", "kind": "other",
             "title": "run-command", "status": "in_progress", "rawInput": {"command": "echo"}},
            {"sessionUpdate": "tool_call_update", "toolCallId": "call_fake_1",
             "status": "completed"},
        ):
            self.notify_client("session/update", {"sessionId": session_id, "update": update})
        self.send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})

    def run_final_prompt(self, message: dict) -> None:
        """The final-message carrier: usage snapshot, unknown fact, then the answer."""
        request_id = message["id"]
        params = message.get("params") or {}
        session_id = params.get("sessionId")
        self.last_session_id = session_id
        self.write_session_record(session_id, self.prompt_text(params))
        if self.emit_foreign_chunk:
            # A message chunk framed under a session this run never opened.
            self.notify_client("session/update", {"sessionId": "foreign-root-1",
                                                  "update": {"sessionUpdate": "agent_message_chunk",
                                                             "content": {"type": "text",
                                                                         "text": "foreign"}}})
        for index in range(self.spam_unknown_kinds):
            self.notify_client("session/update", {"sessionId": session_id,
                                                  "update": {"sessionUpdate": f"future-{index}"}})
        if self.emit_unknown_update:
            self.notify_client("session/update", {"sessionId": session_id,
                                                  "update": {"sessionUpdate": "buddy_probe_unknown"}})
        if self.emit_tool_update:
            self.notify_client("session/update", {"sessionId": session_id,
                                                  "update": {"sessionUpdate": "tool_call",
                                                             "toolCallId": "call_probe_1", "kind": "other",
                                                             "title": "run-command", "status": "in_progress"}})
            self.notify_client("session/update", {"sessionId": session_id,
                                                  "update": {"sessionUpdate": "tool_call_update",
                                                             "toolCallId": "call_probe_1",
                                                             "status": "completed"}})
        if not self.bare_final:
            self.notify_client("session/update", {"sessionId": session_id,
                                                  "update": {"sessionUpdate": "usage_update",
                                                             "used": 100, "size": 1000000}})
        index = min(self.prompt_count, len(self.final_answers) - 1) if self.final_answers else 0
        self.prompt_count += 1
        answer = self.final_answers[index] if self.final_answers else '{"choice":"a"}'
        self.notify_client("session/update", {"sessionId": session_id,
                                              "update": {"sessionUpdate": "agent_message_chunk",
                                                         "content": {"type": "text", "text": answer}}})
        self.send({"jsonrpc": "2.0", "id": request_id,
                   "result": {"stopReason": self.stop_reason or "end_turn"}})

    def run_governed_prompt(self, message: dict) -> None:
        """The governed carrier: real session tools through the role's own rules.

        The tool results are minted by ``hey_my_buddy.buddy.roles.worker_services``
        with this run's real bridge configuration — exactly what the stdio MCP
        carrier would answer — so the driver verifies genuine signed receipts and
        refusal envelopes, never fixture prose. The script: one checkpoint call
        (an inquiry-channel refusal when no journal is mounted), one ``completed``
        finish (refused while an attention request is outstanding), and, when that
        refusal came back, one corrected finish with the attention disposition.
        """
        request_id = message["id"]
        params = message.get("params") or {}
        session_id = params.get("sessionId")
        self.last_session_id = session_id
        self.write_session_record(session_id, self.prompt_text(params))
        sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "src"))
        from hey_my_buddy.buddy.roles import worker_services
        configuration = json.loads(Path(self.bridge_config).read_text())
        if self.governed_permission:
            answer = self.request_client("session/request_permission", {
                "sessionId": session_id,
                "toolCall": {"toolCallId": "call_upgrade_1", "kind": "edit",
                             "title": "escalate-write"},
                "options": [{"optionId": "opt-allow", "kind": "allow_once", "name": "Allow"},
                            {"optionId": "opt-reject", "kind": "reject_once", "name": "Reject"}]})
            log(self.log_path, {"event": "governed-permission-answer", "answer": answer})

        def tool_frame(call_id: str, title: str, status: str, content=None) -> None:
            update = {"sessionUpdate": "tool_call", "toolCallId": call_id, "kind": "other",
                      "title": title, "status": status}
            if content is not None:
                update["content"] = content
            self.notify_client("session/update", {"sessionId": session_id, "update": update})

        def tool_result(call_id: str, status: str, text: str) -> None:
            self.notify_client("session/update", {"sessionId": session_id, "update": {
                "sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": status,
                "content": [{"type": "content", "content": {"type": "text", "text": text}}]}})

        checkpoint_id = "call_checkpoint_1"
        if self.checkpoint_tool:
            if self.wait_for_inquiry > 0:
                # Deterministic cooperative delivery: hold the turn open until a
                # Host question is actually queued (or the bound expires), so a
                # test's ask can never race the checkpoint.
                bounded = time.monotonic() + self.wait_for_inquiry
                while time.monotonic() < bounded:
                    if worker_services.pending_inquiries(configuration):
                        break
                    time.sleep(0.05)
                log(self.log_path, {"event": "inquiry-wait-done",
                                    "pending": bool(worker_services.pending_inquiries(configuration))})
            # The MCP protocol passes the bare tool name; the mcp__server__tool
            # composite is the ACP-side presentation only.
            bare = self.checkpoint_tool.rsplit("__", 1)[-1]
            tool_frame(checkpoint_id, self.checkpoint_tool, "in_progress")
            checkpoint_result = worker_services.call_session_tool(bare, {}, configuration)
            checkpoint_text = checkpoint_result["content"][0]["text"]
            if self.forge_checkpoint and "tool-refusal" not in checkpoint_text:
                # A tampered checkpoint receipt fails its signature verification
                # controller-side: the id changes under a stale signature.
                receipt = json.loads(checkpoint_text)
                if receipt.get("inquiries"):
                    receipt["inquiries"][0]["inquiryId"] = "forged-inquiry-id"
                checkpoint_text = json.dumps(receipt)
            tool_result(checkpoint_id, "completed", checkpoint_text)
            log(self.log_path, {"event": "checkpoint-result",
                                "refusal": "tool-refusal" in checkpoint_text})
            if self.answer_inquiry and "tool-refusal" not in checkpoint_text:
                receipt = json.loads(checkpoint_text)
                questions = receipt.get("inquiries") or []
                if questions:
                    answer_tool = self.checkpoint_tool.rsplit("__", 1)[0] + "__buddy_answer_inquiry"
                    tool_frame("call_answer_1", answer_tool, "in_progress")
                    answer_result = worker_services.call_session_tool(
                        "buddy_answer_inquiry",
                        {"inquiryId": questions[0]["inquiryId"],
                         "answer": "the root's bounded answer to the Host question"},
                        configuration)
                    answer_text = answer_result["content"][0]["text"]
                    tool_result("call_answer_1", "completed", answer_text)
                    log(self.log_path, {"event": "answer-result",
                                        "refusal": "tool-refusal" in answer_text})

        def finish(outcome: dict, call_id: str, *, fail_first: bool = False) -> str:
            tool_frame(call_id, self.finish_tool, "in_progress")
            if fail_first:
                # A native tool failure keeps the turn alive: the root corrects
                # its call and retries inside the same native turn.
                self.notify_client("session/update", {"sessionId": session_id, "update": {
                    "sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "failed"}})
                log(self.log_path, {"event": "finish-failed", "callId": call_id})
                return ""
            result = worker_services.call_session_tool(self.finish_tool.rsplit("__", 1)[-1], outcome, configuration)
            text = result["content"][0]["text"]
            if self.forge_receipt and "tool-refusal" not in text:
                receipt = json.loads(text)
                receipt["receiptId"] = "f" * 32  # a tampered receipt fails its signature
                text = json.dumps(receipt)
            tool_result(call_id, "completed", text)
            log(self.log_path, {"event": "finish-result",
                                "refusal": "tool-refusal" in text,
                                "callId": call_id})
            return text

        if self.no_finish:
            self.send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
            return
        completed_outcome = {"disposition": "completed", "summary": "the fixture turn is done",
                             "remaining": [], "decisions": [], "artifacts": [], "request": None}
        text = finish(completed_outcome, "call_finish_1", fail_first=self.finish_fail_then_retry)
        if self.finish_fail_then_retry:
            text = finish(completed_outcome, "call_finish_2")
        elif "tool-refusal" in text:
            envelope = json.loads(text)
            if envelope.get("reason") == "inquiry-pending":
                # The real root's correction: the answer the driver just verified
                # reaches the journal moments later; wait for it, then retry the
                # completed finish inside this same native turn.
                bounded = time.monotonic() + 5
                while time.monotonic() < bounded and worker_services.pending_inquiries(configuration):
                    time.sleep(0.05)
                text = finish(completed_outcome, "call_finish_2")
            if "tool-refusal" in text:
                attention_outcome = {"disposition": "attention",
                                     "summary": "the fixture turn needs a Host decision",
                                     "remaining": [], "decisions": [], "artifacts": [],
                                     "request": {"summary": "the refused native upgrade request",
                                                 "attempted": "the bounded fixture work",
                                                 "neededWork": "a Host decision on the refused request",
                                                 "expectedArtifacts": [],
                                                 "acceptance": "the Host accepts the attention receipt"}}
                finish(attention_outcome, "call_finish_3")
        self.notify_client("session/update", {"sessionId": session_id,
                                              "update": {"sessionUpdate": "usage_update",
                                                         "used": 900, "size": 1000000}})
        self.send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})

    # -- the synthetic private session record ---------------------------------

    def write_session_record(self, session_id: str, prompt_text: str) -> None:
        """Write one synthetic ``session.v3`` rollout under this fake's DSH_HOME.

        The shapes mirror the desensitized native fixture: a version-3 session
        header, a user message, turn/step markers, assistant messages whose
        ``source`` names the model and whose ``usage`` carries the native
        counters, and a terminal turn end. This is a fixture written by the
        fake agent, never a native record; it only exercises the driver's
        optional record reader end to end.
        """
        mode = self.session_record
        if not mode:
            return
        directory = Path(os.environ["DSH_HOME"]) / "sessions"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        now_ms = int(time.time() * 1000)
        source = {"kind": "model", "provider": "fake", "model": "m1"}

        def assistant(seq: int, step: int, text: str, usage=None) -> dict:
            data = {"turn": 1, "step": step,
                    "message": {"id": f"assistant-{session_id}-{seq}", "role": "assistant",
                                "content": [{"type": "text", "text": text}], "source": source}}
            if usage is not None:
                data["usage"] = usage
            return {"type": "assistant/message", "seq": seq, "data": data}

        events = [
            {"type": "session", "version": 3,
             "id": "fake-session-foreign" if mode == "foreign" else session_id,
             "createdAt": now_ms, "cwd": os.getcwd(), "isSeeded": False,
             "origin": "acp", "delegationDepth": 0},
            {"type": "user/message", "seq": 4, "data": {
                "id": f"user-{session_id}", "role": "user",
                "content": [{"type": "text", "text": prompt_text}],
                "source": {"kind": "user"}}},
            {"type": "turn/start", "seq": 5, "data": {"turn": 1}},
            {"type": "step/start", "seq": 6, "data": {"turn": 1, "step": 1}},
            assistant(7, 1, "Reading the fixture task first.", {
                "inputTokens": 1000, "outputTokens": 100, "totalTokens": 1150,
                "cacheReadTokens": 50, "reasoningTokens": 10}),
            {"type": "step/end", "seq": 8, "data": {"turn": 1, "step": 1}},
            {"type": "step/start", "seq": 9, "data": {"turn": 1, "step": 2}},
        ]
        if mode == "missing":
            events.append(assistant(10, 2, "the final answer", None))
        else:
            events.append(assistant(10, 2, "the final answer", {
                "inputTokens": 2000, "outputTokens": 200, "totalTokens": 2300,
                "cacheReadTokens": 100, "reasoningTokens": 20}))
        events.append({"type": "step/end", "seq": 11, "data": {"turn": 1, "step": 2}})
        if mode == "quota":
            events.append({"type": "turn/end", "seq": 12, "data": {
                "turn": 1, "reason": {"kind": "error",
                                      "error": {"code": "QUOTA",
                                                "message": "provider wording never retained"}}}})
        else:
            events.append({"type": "turn/end", "seq": 12,
                           "data": {"turn": 1, "reason": {"kind": "completed"}}})
        if mode == "flood":
            events.insert(len(events) - 1, {"type": "fixture/oversized",
                                            "seq": 11.5, "data": {"blob": "x" * (4 * 1024 * 1024)}})
        lines = "".join(json.dumps(event) + "\n" for event in events)
        if mode == "corrupt":
            path = directory / f"{session_id}.v3.jsonl.zstd"
            path.write_bytes(b"this is not a zstd stream\n")
        elif mode == "plain":
            path = directory / f"{session_id}.v3.jsonl"
            path.write_text(lines)
        else:
            import zstandard
            path = directory / f"{session_id}.v3.jsonl.zstd"
            path.write_bytes(zstandard.ZstdCompressor().compress(lines.encode()))
        log(self.log_path, {"event": "session-record-written", "mode": mode,
                            "path": str(path), "sessionId": session_id})

    @staticmethod
    def prompt_text(params: dict) -> str:
        """The prompt's own text blocks, without interpreting anything."""
        blocks = params.get("prompt") if isinstance(params.get("prompt"), list) else []
        return "".join(block.get("text", "") for block in blocks
                       if isinstance(block, dict) and isinstance(block.get("text"), str))

    def serve(self) -> None:
        import select

        log(self.log_path, {"event": "startup"})
        if self.stderr_note:
            sys.stderr.write(self.stderr_note + "\n")
            sys.stderr.flush()
        if self.spam_notifications:
            for index in range(self.spam_notifications):
                self.notify_client(f"session/spam-{index}", {"index": index})
        # Read the descriptor directly and parse lines from our own buffer: a
        # buffered readline would prefetch coalesced frames that select on the
        # fd can no longer see, starving them until new data arrives.
        pending = b""
        while not self._stop_reading.is_set():
            while True:
                index = pending.find(b"\n")
                if index < 0:
                    break
                line = pending[:index]
                pending = pending[index + 1:]
                self.handle_client_line(line)
            if self._stop_reading.is_set():
                break
            ready, _, _ = select.select([0], [], [], 0.05)
            if not ready:
                continue
            if self._stop_reading.is_set():
                break  # re-check after the wake: never consume a post-freeze frame
            chunk = os.read(0, 65536)
            if not chunk:
                break
            pending += chunk
        if self._stop_reading.is_set():
            # A frozen agent stays alive holding its unread stdin: the test's
            # group termination is the only way out, never a silent exit.
            log(self.log_path, {"event": "parked-frozen"})
            threading.Event().wait()
        if self.late_tool_update:
            # Same-root frames emitted as the client drains at EOF: they arrive
            # after the prompt answered and must still reach the role observer.
            session_id = self.last_session_id or "fake-session-drain"
            self.notify_client("session/update", {"sessionId": session_id,
                                                  "update": {"sessionUpdate": "tool_call",
                                                             "toolCallId": "call_late_1",
                                                             "kind": "other",
                                                             "title": "run-command",
                                                             "status": "in_progress"}})
            self.notify_client("session/update", {"sessionId": session_id,
                                                  "update": {"sessionUpdate": "tool_call_update",
                                                             "toolCallId": "call_late_1",
                                                             "status": "completed"}})
            log(self.log_path, {"event": "late-tool-emitted"})
        self.close_stubs()
        if self.survive_eof:
            log(self.log_path, {"event": "surviving-eof", "seconds": self.survive_eof})
            time.sleep(self.survive_eof)
        log(self.log_path, {"event": "stdin-eof", "exit": 0})

    def handle_client_line(self, line: bytes) -> None:
        text = line.strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError:
            log(self.log_path, {"event": "malformed-client-frame"})
            return
        if "id" in message and "method" not in message:
            with self.lock:
                pending = self.client_pending.get(message.get("id"))
                if pending is not None:
                    pending[1] = message
                    pending[0].set()
            return
        threading.Thread(target=self.handle, args=(message,), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    # The public launch face the run module's driver always passes, accepted and
    # recorded like the installed agent's: the profile name and the patch file.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--patch", default=None)
    parser.add_argument("--delay", action="append", default=[],
                        help="METHOD:SECONDS delay before answering that method")
    parser.add_argument("--garbage-on", action="append", default=[],
                        help="emit invalid frames before answering this method")
    parser.add_argument("--duplicate-on", action="append", default=[],
                        help="send a second (duplicate) response for this method")
    parser.add_argument("--die-before-answer", action="append", default=[],
                        help="exit without answering this method")
    parser.add_argument("--orphan-on", action="append", default=[],
                        help="spawn a same-group sleeper and exit without answering this method")
    parser.add_argument("--respond-then-exit", action="append", default=[],
                        help="answer this method and exit immediately (reply then EOF)")
    parser.add_argument("--raw-frame", action="append", default=[],
                        help="emit hostile JSON-RPC shapes before answering this method")
    parser.add_argument("--freeze-stdin-on", action="append", default=[],
                        help="stop reading stdin when this method arrives, staying alive")
    parser.add_argument("--fs-path", default="x.txt",
                        help="path used in the fs/* request during prompt")
    parser.add_argument("--spam-notifications", type=int, default=0,
                        help="emit this many distinct notification methods at startup")
    parser.add_argument("--option-count", type=int, default=1,
                        help="total options offered in the second permission request")
    parser.add_argument("--malformed-permission", action="store_true",
                        help="the first permission request carries toolCall as a list and options as an int")
    parser.add_argument("--error-data-on", action="append", default=[],
                        help="answer this method with a JSON-RPC error whose message and data carry markers")
    parser.add_argument("--freeze-mid-prompt", action="store_true",
                        help="park after sending the first permission request, mid-prompt")
    parser.add_argument("--survive-eof", type=float, default=0.0)
    parser.add_argument("--allowed-title", default=None)
    # -- native-run driver modes
    parser.add_argument("--prompt-mode", default="legacy", choices=["legacy", "final", "governed"])
    parser.add_argument("--final-answer", action="append", default=[],
                        help="the Nth prompt's message text; the last one repeats")
    parser.add_argument("--emit-unknown-update", action="store_true",
                        help="emit one unknown sessionUpdate kind before the answer")
    parser.add_argument("--bridge-config", default=None,
                        help="the finish-bridge.json the governed session tools read")
    parser.add_argument("--finish-tool", default=None, help="the governed completion tool's qualified name")
    parser.add_argument("--checkpoint-tool", default=None, help="the governed checkpoint tool's qualified name")
    parser.add_argument("--governed-permission", action="store_true",
                        help="request one upgrade permission before the governed tool calls")
    parser.add_argument("--hang-prompt", action="store_true",
                        help="park the prompt until a session/cancel arrives, then stop cancelled")
    parser.add_argument("--park-prompt", action="store_true",
                        help="park the prompt forever; only the owned group stop ends it")
    parser.add_argument("--emit-tool-update", action="store_true",
                        help="emit one completed tool_call/update pair before the answer")
    parser.add_argument("--bare-final", action="store_true",
                        help="emit no usage snapshot: only the message chunk and the stop")
    parser.add_argument("--finish-fail-then-retry", action="store_true",
                        help="the first finish call fails natively and the root retries")
    parser.add_argument("--no-finish", action="store_true",
                        help="end the governed prompt without ever calling the finish tool")
    parser.add_argument("--forge-receipt", action="store_true",
                        help="tamper with the finish receipt so its signature fails")
    parser.add_argument("--session-record", default=None,
                        choices=["usage", "quota", "missing", "foreign", "corrupt",
                                 "flood", "plain"],
                        help="write one synthetic session.v3 rollout under this fake's DSH_HOME")
    parser.add_argument("--stderr-note", default=None,
                        help="write one line to stderr at startup, for the tail mirror")
    parser.add_argument("--emit-foreign-chunk", action="store_true",
                        help="emit one agent_message_chunk framed under a foreign session id")
    parser.add_argument("--spam-unknown-kinds", type=int, default=0,
                        help="emit this many distinct unknown sessionUpdate kinds before the answer")
    parser.add_argument("--late-tool-update", action="store_true",
                        help="emit one same-root completed tool pair while the client drains at EOF")
    parser.add_argument("--wait-for-inquiry", type=float, default=0.0,
                        help="hold the governed turn until a Host question is queued (bounded seconds)")
    parser.add_argument("--answer-inquiry", action="store_true",
                        help="answer the first delivered question through the real session-tool rules")
    parser.add_argument("--forge-checkpoint", action="store_true",
                        help="mutate the checkpoint receipt under its stale signature")
    parser.add_argument("--stop-reason", default=None,
                        help="end the prompt with this stop reason instead of end_turn")
    args = parser.parse_args()
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not check_private_home(log_path):
        return 3
    log(log_path, {"event": "launch-face", "profile": args.profile, "patch": args.patch})
    FakeAgent(args).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
