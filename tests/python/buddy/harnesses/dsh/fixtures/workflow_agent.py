"""Offline governed DSH agent for the real blackboard Worker workflow tests.

A thin extension of the shared fake ACP agent (``../acp/fake_agent.py``): the
wire behavior, the governed carrier and the private-home tripwire are the shared
agent's own; this module adds only the test-specific facts the two blackboard
workflow modules need from a native harness - the workspace edits, one offline
quota death, and the continuation-context capture. The final value still comes
from the real role rules: the finish receipt is minted by
``hey_my_buddy.buddy.roles.worker_services.call_session_tool`` against this
run's own bridge, exactly as the mounted MCP carrier would answer.

The agent derives its session-service binding mechanically, the way the
installed DSH sees it: from the ``session/new`` mount description (the private
MCP server's name and the ``--config`` bridge path of the session-MCP command).
It never reads the controller's control files and never hardcodes a tool name.

Modes: the governed carrier is always the shared agent's; the workflow behavior
follows the turn itself. Every turn of index 2 or later captures the rebuilt
continuation context and completes through the governed finish. Turn 1 dies the
offline quota death (real edit, one message chunk, no finish call, ``refusal``
stop, machine ``QUOTA`` turn end in this run's private session record) exactly
when the objective is the quota fixture's; any other turn 1 completes.
``--workflow-mode`` pins the behavior for a direct launch when needed
(``auto`` resolves by objective), and ``--native-error-code`` swaps the quota
record's machine code (fault injection: a code the quota classification does
not recognize must surface as no quota failure at all, never as a
misattributed one).

The launch the daemon chain composes passes only the public launch face
(``--profile acp --patch <path>``), so ``--log`` is optional: the default log
lives inside this run's private ``DSH_HOME``.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

_SPEC = importlib.util.spec_from_file_location(
    "dsh_fake_agent_base", Path(__file__).resolve().parent.parent / "acp" / "fake_agent.py")
fake_agent = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fake_agent)

#: The quota scenario's objective; the quota tests submit exactly this task.
QUOTA_OBJECTIVE = "Implement the fixture change"
QUOTA_EDIT = "partial work preserved after quota failure\n"
QUOTA_MESSAGE = "The first change is saved; verification remains."
OUTPUT_FILE = "mock-output.txt"
CONTEXT_FILE = "continued-context.json"


def workflow_options(model_value: str, effort_value: str) -> list:
    """This offline harness's declared selectors: the workflow tests' pinned
    provider/model and the four efforts the sessions can select and read back."""
    return [
        {"id": "model", "name": "Model", "category": "model", "type": "select",
         "currentValue": model_value,
         "options": [{"group": "deepseek-official", "name": "DeepSeek", "options": [
             {"value": '["deepseek-official","deepseek-flash"]', "name": "DeepSeek-V41-Flash"}]}]},
        {"id": "reasoning_effort", "name": "Reasoning effort", "category": "thought_level",
         "type": "select", "currentValue": effort_value,
         "options": [{"value": "off", "name": "off"}, {"value": "low", "name": "low"},
                     {"value": "high", "name": "high"}, {"value": "max", "name": "max"}]},
    ]


class WorkflowAgent(fake_agent.FakeAgent):
    """The shared fake agent bound to this run's mount and the workflow mode."""

    def __init__(self, args) -> None:
        super().__init__(args)
        self.workflow_mode = args.workflow_mode
        self.native_error_code = args.native_error_code
        self.session_cwd: dict = {}

    # -- the session face: declared selectors and the mechanical mount binding --

    def answer(self, method: str, params: dict) -> dict:
        payload = super().answer(method, params)
        result = payload.get("result") if isinstance(payload, dict) else None
        if method in ("session/new", "session/set_config_option") and isinstance(result, dict) \
                and isinstance(result.get("configOptions"), list):
            session = self.sessions.get(params.get("sessionId")) or {}
            result["configOptions"] = workflow_options(session.get("model", ""),
                                                       session.get("effort", "high"))
        if method == "session/new" and isinstance(result, dict):
            session_id = result.get("sessionId")
            self.session_cwd[session_id] = params.get("cwd")
            for entry in params.get("mcpServers") or []:
                arguments = entry.get("args") or []
                if isinstance(arguments, list) and "--config" in arguments:
                    self.bridge_config = arguments[arguments.index("--config") + 1]
                server = entry.get("name")
                if isinstance(server, str) and server:
                    self.finish_tool = f"mcp__{server}__buddy_finish_turn"
            fake_agent.log(self.log_path, {"event": "workflow-binding",
                                           "mode": self.workflow_mode,
                                           "bridge": self.bridge_config,
                                           "finishTool": self.finish_tool})
        return payload

    # -- the governed turn ------------------------------------------------------

    def run_governed_prompt(self, message: dict) -> None:
        turn_input = self.turn_input(message)
        index = (turn_input.get("context") or {}).get("turnIndex")
        quota = self.workflow_mode == "quota" or (
            self.workflow_mode == "auto" and index == 1
            and (turn_input.get("context") or {}).get("objective") == QUOTA_OBJECTIVE)
        if quota and index == 1:
            self.run_quota_turn(message, turn_input)
            return
        self.prepare_workspace(message, turn_input)
        super().run_governed_prompt(message)

    def prepare_workspace(self, message: dict, turn_input: dict) -> None:
        cwd = self.session_cwd.get((message.get("params") or {}).get("sessionId"))
        if not cwd:
            return
        root = Path(cwd)
        index = (turn_input.get("context") or {}).get("turnIndex")
        if type(index) is int and index >= 2:
            # The continuation captures exactly the context the service rebuilt.
            (root / CONTEXT_FILE).write_text(json.dumps(turn_input.get("context") or {}))
        (root / OUTPUT_FILE).write_text(f"output for turn {turn_input.get('turnId')}\n")

    def run_quota_turn(self, message: dict, turn_input: dict) -> None:
        """The offline quota death: real edits, one message, no finish call.

        The native failure lives only in this run's private session record - the
        machine turn-end code the driver's bounded record reader folds into the
        run's failure fact; the prompt itself ends with the native refusal stop,
        so the governed turn fails for its missing accepted finish.
        """
        request_id = message["id"]
        session_id = (message.get("params") or {}).get("sessionId")
        self.last_session_id = session_id
        cwd = self.session_cwd.get(session_id)
        if cwd:
            (Path(cwd) / "tracked.txt").write_text(QUOTA_EDIT)
        self.notify_client("session/update", {"sessionId": session_id, "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": QUOTA_MESSAGE}}})
        self.write_quota_record(session_id)
        self.send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "refusal"}})
        fake_agent.log(self.log_path, {"event": "quota-turn", "sessionId": session_id,
                                       "code": self.native_error_code})

    def write_quota_record(self, session_id: str) -> None:
        """One synthetic ``session.v3`` rollout: partial usage, retained root
        assistant text, and the machine error turn end. A fixture written by the
        fake agent under this run's private DSH_HOME, never a native record."""
        directory = self.sessions_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        now_ms = int(time.time() * 1000)
        source = {"kind": "model", "provider": "deepseek-official", "model": "deepseek-flash"}
        events = [
            {"type": "session", "version": 3, "id": session_id, "createdAt": now_ms,
             "cwd": os.getcwd(), "isSeeded": False, "origin": "acp", "delegationDepth": 0},
            {"type": "user/message", "seq": 4, "data": {
                "id": f"user-{session_id}", "role": "user",
                "content": [{"type": "text", "text": "the governed fixture turn"}],
                "source": {"kind": "user"}}},
            {"type": "turn/start", "seq": 5, "data": {"turn": 1}},
            {"type": "step/start", "seq": 6, "data": {"turn": 1, "step": 1}},
            {"type": "assistant/message", "seq": 7, "data": {
                "turn": 1, "step": 1,
                "message": {"id": f"assistant-{session_id}-7", "role": "assistant",
                            "content": [{"type": "text", "text": QUOTA_MESSAGE}],
                            "source": source},
                "usage": {"inputTokens": 1000, "outputTokens": 100, "totalTokens": 1150,
                          "cacheReadTokens": 50, "reasoningTokens": 10}}},
            {"type": "step/end", "seq": 8, "data": {"turn": 1, "step": 1}},
            {"type": "turn/end", "seq": 9, "data": {
                "turn": 1, "reason": {"kind": "error",
                                      "error": {"code": self.native_error_code,
                                                "message": "provider wording never retained"}}}},
        ]
        lines = "".join(json.dumps(event) + "\n" for event in events)
        path = directory / f"{session_id}.v3.jsonl.zstd"
        import zstandard
        path.write_bytes(zstandard.ZstdCompressor().compress(lines.encode()))
        fake_agent.log(self.log_path, {"event": "quota-record-written", "path": str(path)})

    @staticmethod
    def turn_input(message: dict) -> dict:
        """The frozen turn input the governed prompt carries as its last JSON
        object: the identity and context this fixture's behavior keys on."""
        text = fake_agent.FakeAgent.prompt_text(message.get("params") or {})
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and "turnId" in value and "context" in value:
                return value
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=None)
    # The public launch face the run module's driver always passes.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--patch", default=None)
    parser.add_argument("--workflow-mode", default="auto", choices=["auto", "completed", "quota"])
    parser.add_argument("--native-error-code", default="QUOTA")
    args = parser.parse_args()
    if not args.log:
        args.log = str(Path(os.environ.get("DSH_HOME", os.getcwd())) / "fixture-agent.log")
    # The shared agent's construction face, pinned to the governed carrier with
    # no checkpoint call: the mount binding is derived from session/new instead.
    shared = SimpleNamespace(
        log=args.log, delay=[], garbage_on=[], duplicate_on=[], die_before_answer=[],
        orphan_on=[], respond_then_exit=[], raw_frame=[], freeze_stdin_on=[], fs_path="x.txt",
        spam_notifications=0, option_count=1, malformed_permission=False, error_data_on=[],
        freeze_mid_prompt=False, survive_eof=0.0, allowed_title=None,
        prompt_mode="governed", final_answer=[], emit_unknown_update=False,
        bridge_config=None, finish_tool=None, checkpoint_tool=None,
        governed_permission=False, finish_fail_then_retry=False, no_finish=False,
        forge_receipt=False, stop_reason=None, hang_prompt=False, park_prompt=False,
        emit_tool_update=False, bare_final=False, session_record=None, stderr_note=None,
        emit_foreign_chunk=False, spam_unknown_kinds=0, late_tool_update=False,
        wait_for_inquiry=0.0, answer_inquiry=False, forge_checkpoint=False,
        replay_history=False, resume_echo_foreign_id=False, resume_foreign_root=False)
    namespace = SimpleNamespace(**{**vars(shared), "workflow_mode": args.workflow_mode,
                                   "native_error_code": args.native_error_code,
                                   "profile": args.profile, "patch": args.patch})
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not fake_agent.check_private_home(log_path):
        return 3
    fake_agent.log(log_path, {"event": "launch-face", "profile": args.profile,
                              "patch": args.patch, "workflowMode": args.workflow_mode})
    WorkflowAgent(namespace).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
