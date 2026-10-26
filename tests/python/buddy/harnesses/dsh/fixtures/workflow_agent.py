"""Offline micro task DSH agent for the real blackboard Worker workflow tests.

A thin extension of the shared fake ACP agent (``../acp/fake_agent.py``): the
wire behavior, the micro task carrier and the private-home tripwire are the shared
agent's own; this module adds only the test-specific facts the two blackboard
workflow modules need from a native harness - the workspace edits, one offline
provider rejection, and the continuation-context capture. The final value still comes
from the real role rules: the finish receipt is minted by
``hey_my_buddy.buddy.roles.worker_services.call_session_tool`` against this
run's own bridge, exactly as the mounted MCP carrier would answer.

The agent derives its session-service binding mechanically, the way the
installed DSH sees it: from the ``session/new`` mount description (the private
MCP server's name and the ``--config`` bridge path of the session-MCP command).
It never reads the controller's control files and never hardcodes a tool name.

Modes: the micro task carrier is always the shared agent's; the workflow behavior
follows the turn itself. Every turn of index 2 or later captures the rebuilt
continuation context and completes through the micro task finish. Turn 1 dies the
offline provider rejection (real edit, one message chunk, no finish call, ``refusal``
stop, machine ``QUOTA`` turn end in this run's private session record) exactly
when the task is the provider rejection fixture's; any other turn 1 completes.
``--workflow-mode`` pins the behavior for a direct launch when needed
(``auto`` resolves by task), and ``--native-error-code`` swaps the provider rejection
record's machine code (fault injection: a code the provider rejection classification does
not recognize must surface as no provider rejection at all, never as a
misattributed one).

The launch the daemon chain composes passes only the public launch face
(``--profile acp --patch <path>``), so ``--log`` is optional: the default log
lives under this run's patch-pinned private session root.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

from hey_my_buddy.paths import native_path

_SPEC = importlib.util.spec_from_file_location(
    "dsh_fake_agent_base", Path(__file__).resolve().parent.parent / "acp" / "fake_agent.py")
fake_agent = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fake_agent)

#: The provider rejection scenario's task; its workflow tests submit exactly this task.
QUOTA_TASK = "Implement the fixture change"
QUOTA_EDIT = "partial work preserved after quota failure\n"
QUOTA_MESSAGE = "The first change is saved; verification remains."
NETWORK_TASK = "Exercise the offline network-error fixture"
NETWORK_EDIT = "partial work preserved after network error\n"
NETWORK_MESSAGE = "The connection dropped after this edit; verification remains."
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
                    self.finish_tool = f"mcp__{server}__hey_my_buddy_finish_turn"
            fake_agent.log(self.log_path, {"event": "workflow-binding",
                                           "mode": self.workflow_mode,
                                           "launcherEnvKeys": sorted(key for key in os.environ if key in (
                                               "HMB_STATE_DIR", "HMB_AGENT_CREDENTIAL_FILE",
                                               "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
                                               "HMB_DEV_SOURCE")),
                                           "bridge": self.bridge_config,
                                           "finishTool": self.finish_tool})
        return payload

    # -- the micro task turn ------------------------------------------------------

    def run_micro_task_prompt(self, message: dict) -> None:
        turn_input = self.turn_input(message)
        index = (turn_input.get("context") or {}).get("turnIndex")
        # The task is the plain section before the final turn-context JSON.
        task_section = self.prompt_text(message.get("params") or {}).rpartition("\n\n")[0]
        quota = self.workflow_mode == "quota" or (
            self.workflow_mode == "auto" and index == 1
            and task_section.endswith("\n\n" + QUOTA_TASK))
        if quota and index == 1:
            self.run_quota_turn(message, turn_input)
            return
        if self.workflow_mode == "auto" and index == 1 and task_section.endswith("\n\n" + NETWORK_TASK):
            self.run_quota_turn(message, turn_input, code="NETWORK_UNAVAILABLE",
                                edit=NETWORK_EDIT, message_text=NETWORK_MESSAGE)
            return
        self.prepare_workspace(message, turn_input)
        super().run_micro_task_prompt(message)

    def prepare_workspace(self, message: dict, turn_input: dict) -> None:
        cwd = self.session_cwd.get((message.get("params") or {}).get("sessionId"))
        if not cwd:
            return
        root = Path(cwd)
        index = (turn_input.get("context") or {}).get("turnIndex")
        if type(index) is int and index >= 2:
            # The continuation captures exactly the context the service rebuilt.
            native_path(root / CONTEXT_FILE).write_text(json.dumps(turn_input.get("context") or {}), encoding="utf-8", newline="\n")
        native_path(root / OUTPUT_FILE).write_text(f"output for turn {turn_input.get('turnId')}\n", encoding="utf-8", newline="\n")

    def run_quota_turn(self, message: dict, turn_input: dict, *, code: str | None = None,
                       edit: str = QUOTA_EDIT, message_text: str = QUOTA_MESSAGE) -> None:
        """The offline provider rejection: real edits, one message, no finish call.

        The native failure lives only in this run's private session record - the
        machine turn-end code the driver's bounded record reader folds into the
        run's failure fact; the prompt itself ends with the native refusal stop,
        so the micro task turn fails for its missing accepted finish.
        """
        request_id = message["id"]
        session_id = (message.get("params") or {}).get("sessionId")
        self.last_session_id = session_id
        cwd = self.session_cwd.get(session_id)
        if cwd:
            native_path(Path(cwd) / "tracked.txt").write_text(edit, encoding="utf-8", newline="\n")
        self.notify_client("session/update", {"sessionId": session_id, "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": message_text}}})
        self.write_quota_record(session_id, code=code, message_text=message_text)
        self.send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "refusal"}})
        fake_agent.log(self.log_path, {"event": "quota-turn", "sessionId": session_id,
                                       "code": code or self.native_error_code})

    def write_quota_record(self, session_id: str, *, code: str | None = None,
                           message_text: str = QUOTA_MESSAGE) -> None:
        """One synthetic ``session.v3`` rollout: partial usage, retained root
        assistant text, and the machine error turn end. A fixture written by the
        fake agent under this run's private session root, never a native record."""
        directory = self.sessions_dir()
        native_path(directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        now_ms = int(time.time() * 1000)
        source = {"kind": "model", "provider": "deepseek-official", "model": "deepseek-flash"}
        events = [
            {"type": "session", "version": 3, "id": session_id, "createdAt": now_ms,
             "cwd": os.getcwd(), "isSeeded": False, "origin": "acp", "delegationDepth": 0},
            {"type": "user/message", "seq": 4, "data": {
                "id": f"user-{session_id}", "role": "user",
                "content": [{"type": "text", "text": "the micro task fixture turn"}],
                "source": {"kind": "user"}}},
            {"type": "turn/start", "seq": 5, "data": {"turn": 1}},
            {"type": "step/start", "seq": 6, "data": {"turn": 1, "step": 1}},
            {"type": "assistant/message", "seq": 7, "data": {
                "turn": 1, "step": 1,
                "message": {"id": f"assistant-{session_id}-7", "role": "assistant",
                            "content": [{"type": "text", "text": message_text}],
                            "source": source},
                "usage": {"inputTokens": 1000, "outputTokens": 100, "totalTokens": 1150,
                          "cacheReadTokens": 50, "reasoningTokens": 10}}},
            {"type": "step/end", "seq": 8, "data": {"turn": 1, "step": 1}},
            {"type": "turn/end", "seq": 9, "data": {
                "turn": 1, "reason": {"kind": "error",
                                      "error": {"code": code or self.native_error_code,
                                                "message": "provider wording never retained"}}}},
        ]
        lines = "".join(json.dumps(event) + "\n" for event in events)
        path = directory / f"{session_id}.v3.jsonl.zstd"
        import zstandard
        native_path(path).write_bytes(zstandard.ZstdCompressor().compress(lines.encode()))
        fake_agent.log(self.log_path, {"event": "quota-record-written", "path": str(path)})

    @staticmethod
    def turn_input(message: dict) -> dict:
        """The prompt's complete final turn-input JSON: identity and context."""
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
    # The shared agent's own construction face, plus this fixture's switches
    # (an omitted ``--log`` defaults under the patch-pinned private root). Every
    # other default - including a switch added to the shared agent later - comes
    # from there, so this fixture cannot fall behind it.
    parser = fake_agent.build_parser(log_required=False)
    parser.add_argument("--workflow-mode", default="auto", choices=["auto", "completed", "quota"])
    parser.add_argument("--native-error-code", default="QUOTA")
    # The micro task carrier with no checkpoint call: the mount binding is
    # derived from session/new instead.
    parser.set_defaults(prompt_mode="micro-task")
    args = parser.parse_args()
    if not args.log:
        args.log = str(Path(args.patch).parent / "fixture-agent.log")
    log_path = Path(args.log)
    native_path(log_path.parent).mkdir(parents=True, exist_ok=True)
    if not fake_agent.check_private_home(log_path, Path(args.fixture_root)):
        return 3
    fake_agent.log(log_path, {"event": "launch-face", "profile": args.profile,
                              "patch": args.patch, "workflowMode": args.workflow_mode})
    WorkflowAgent(args).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
