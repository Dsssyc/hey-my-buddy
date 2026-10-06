"""The unified dsh run seam itself: a real native ACP connection, end to end.

These cases drive :func:`hey_my_buddy.buddy.harnesses.dsh.native_run.run`
directly — a real spawned agent (the harness's fake ACP agent through the same
accepted launch wrapper), a real frozen :class:`RunRequest`, the role's real
observation rules and session-service binding, and a :class:`RunResult` read
back as facts. Nothing wraps a prepared dict: the request goes through the
launch patch/permission-preset configuration, session open, the native model
readback, the prompt and its update stream, and the result comes from the same
stream and stop observations the driver owns. The governed cases mint their
receipts through the real ``roles.worker_services`` rules with the run's real
bridge key, so every verified value is one the driver could have received from
the stdio MCP carrier.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract
from hey_my_buddy.buddy.harnesses.run_contract import (
    MAX_SCHEMA_BYTES,
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
    RunIdentity,
    RunRequest,
    SessionService,
)
from hey_my_buddy.buddy.harnesses.dsh import native_run
from hey_my_buddy.buddy.harnesses.dsh.native_run import SessionServices, prepare_session_service, run
from hey_my_buddy.buddy.roles import worker_services
from hey_my_buddy.buddy.roles.run_observers import FastCorrection, worker_observer
from hey_my_buddy.buddy.roles.structured_call import no_tool_prompt
from hey_my_buddy.buddy.roles.turn_io import input_hash, validate_outcome
from hey_my_buddy.errors import BoardError

from buddy.harnesses.dsh.acp.support import FAKE_AGENT

#: The shared stdio MCP session carrier the Host consolidated into the roles
#: package: the mount names this exact module in the child command, and these
#: tests never spawn it — the fake agent answers session tools in-process
#: through the same ``worker_services`` the real carrier frames.
CARRIER_MODULE = "hey_my_buddy.buddy.roles.session_mcp"

#: The Host-confirmed tool-providing rows the none scope disables, as recorded
#: in the acceptance document; the shipped constant must stay this list.
CONFIRMED_NONE_ROWS = (
    "tool-bash", "tool-pwsh", "tool-jobs", "tool-fs", "tool-fs-search",
    "tool-skill", "tool-subagent-control", "tool-subagent-list-agents",
    "tool-subagent", "tool-subagent-fork", "tool-workflow", "tool-todo",
    "tool-goal", "tool-ralph", "tool-web",
)

SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}

FAST_TIMEOUT = 30


class NativeRunCase(unittest.TestCase):
    """One private per-test root; the fake agent launched like the native one."""

    def setUp(self):
        name = self.id().rsplit(".", 1)[-1]
        container = tempfile.TemporaryDirectory(prefix=f"dsh-native-{name}-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(container.cleanup)
        self.base = Path(container.name).resolve()
        self.root = self.base / "native-root"
        self.dsh_home = self.root / "dsh-home"
        self.home = self.base / "home"
        self.logs = self.root / "logs"
        self.dsh_home.mkdir(mode=0o700, parents=True)
        self.home.mkdir(mode=0o700)
        self.logs.mkdir(mode=0o700)
        self.agent_log_path = self.logs / "fake-agent.log"
        self.extra_agent_args: list[str] = []
        (self.base / "cwd").mkdir(mode=0o700)
        # A run inherits HOME by contract; these tests pin it to this test's
        # private directory, which the launch wrapper validates like DSH_HOME.
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        patcher = mock.patch.object(native_run, "command_for",
                                    side_effect=lambda *_args: self.agent_argv())
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent_argv(self) -> list[str]:
        return [sys.executable, str(FAKE_AGENT), "--log", str(self.agent_log_path),
                *self.extra_agent_args]

    def fast_request(self, prompt: str, *, scope: str = "none", timeout: int = FAST_TIMEOUT,
                     schema: dict | None = SCHEMA) -> RunRequest:
        return RunRequest(
            identity=RunIdentity(task_id="task", attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
                                 generation=1, invocation_id=uuid.uuid4().hex),
            harness="dsh",
            configuration=RunConfiguration(provider="fake", model="m1", effort="high"),
            cwd=str(self.base / "cwd"),
            private_state=PrivateStatePaths(
                invocation_root=str(self.base / f"invocation-{uuid.uuid4().hex[:8]}"),
                native_root=str(self.root)),
            input_text=prompt, tool_scope=scope,
            output_schema=run_contract.FrozenJson.from_value(schema or {}, "schema",
                                                             maximum=MAX_SCHEMA_BYTES),
            budget=RunBudget(timeout_seconds=timeout))

    def worker_request(self, *, timeout: int = FAST_TIMEOUT):
        """The governed binding: a real mount, the real role validator and prompt.

        The binding is assembled through exactly the shared role controller's
        parameter set, including the activity directory and the stderr mirror
        path the governed projection consumes.
        """
        identity = {"taskId": "task", "attemptId": f"attempt-{uuid.uuid4().hex[:8]}",
                    "generation": 1, "turnId": "turn-1"}
        turn_input = {"taskId": identity["taskId"], "attemptId": identity["attemptId"],
                      "generation": 1, "turnId": "turn-1", "context": {}, "executionWorkspace": {}}
        attention = self.base / f"attention-{uuid.uuid4().hex[:8]}.json"
        invocation = self.base / f"worker-invocation-{uuid.uuid4().hex[:8]}"
        bound = native_run.prepare_services(
            invocation_root=invocation, identity=identity, input_sha256=input_hash(turn_input),
            attention_path=attention, session_tools=worker_services.session_tools(),
            completion_tool="buddy_finish_turn", validate_outcome=validate_outcome,
            inquiry=None, inquiry_tools=(), activity_dir=str(self.base / f"activity-{uuid.uuid4().hex[:8]}"),
            native_stderr=str(self.base / f"native-stderr-{uuid.uuid4().hex[:8]}.log"))
        mount = bound.services.mount
        prompt = worker_services.governed_prompt("fixture task", turn_input, mount.finish_tool,
                                                 checkpoint_tool=None, answer_tool=None)
        request = RunRequest(
            identity=RunIdentity(task_id=identity["taskId"], attempt_id=identity["attemptId"],
                                 generation=1, invocation_id=uuid.uuid4().hex,
                                 turn_id=identity["turnId"], input_sha256=input_hash(turn_input)),
            harness="dsh",
            configuration=RunConfiguration(provider="fake", model="m1", effort="high"),
            cwd=str(self.base / "cwd"),
            private_state=PrivateStatePaths(invocation_root=str(invocation), native_root=str(self.root)),
            input_text=prompt, tool_scope="write",
            output_schema=run_contract.FrozenJson.from_value(worker_services.OUTCOME_SCHEMA,
                                                             "schema", maximum=MAX_SCHEMA_BYTES),
            budget=RunBudget(timeout_seconds=timeout),
            session_services=(SessionService(
                tool_names=[f"mcp__{mount.server_name}__{name}" for name in mount.bare_tools]),))
        return request, bound, mount

    def governed_agent_args(self, mount, *, permission=False, **flags) -> None:
        # The mount carries all three session tools; the checkpoint's qualified
        # name exists whether or not this run wired an inquiry channel.
        checkpoint = f"mcp__{mount.server_name}__buddy_checkpoint"
        self.extra_agent_args = [
            "--prompt-mode", "governed",
            "--bridge-config", mount.bridge_config_path,
            "--finish-tool", mount.finish_tool,
            "--checkpoint-tool", checkpoint,
            *(["--governed-permission"] if permission else []),
            *[name for flag, value in flags.items() for name in (f"--{flag.replace('_', '-')}",) if value],
        ]

    def agent_log(self) -> list[dict]:
        if not self.agent_log_path.is_file():
            return []
        return [json.loads(line) for line in self.agent_log_path.read_text().splitlines() if line.strip()]

    def launch_records(self) -> list[dict]:
        path = self.root / "logs" / "launches.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def wait_for(self, predicate, timeout: float = 15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        return None


class FastSeamTests(NativeRunCase):
    """The final-message carrier over the none/read/write launch configurations."""

    def test_a_settled_answer_is_a_final_message_fact(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}']
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.mechanism, "final-message")
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.value.schema_status, "unknown")
        self.assertEqual(result.value.correction_count, 0)
        self.assertIsNone(result.unknown_events)
        self.assertEqual(result.model_started, True)
        self.assertEqual(result.model_start_evidence.basis, "native-start")
        self.assertIsNotNone(result.native_identity.session_id)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.stop_evidence.native.exit_code, 0)
        self.assertIsNone(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.completion_evidence.native_outcome, "end_turn")
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertIsNone(result.usage, "no record exists, so the usage stays unknown, never zero")
        package = result.tool_evidence.value
        self.assertTrue(package["streamComplete"])
        self.assertEqual(package["events"], [])
        self.assertEqual(package["nativeIdentity"][0].get("sessionId"),
                         result.native_identity.session_id)

    def test_usage_comes_from_the_private_session_record(self):
        # The optional record source: the fake agent writes one synthetic
        # session.v3 rollout (a fixture, not a native record) and the driver's
        # bounded reader turns it into the shared usage facts — the old Node
        # usage expectations (per-field sums, cache derived once, complete only
        # with a seen completed turn end) on the record path.
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "usage"]
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        usage = result.usage.value
        self.assertEqual(usage["source"], "dsh/session-record")
        # The canonical projection unifies the native cache counters into the
        # input count exactly once, so the reported basis flips to includes.
        self.assertEqual(usage["inputBasis"], "includes-cached")
        self.assertEqual(usage["scope"], "attempt")
        self.assertEqual(usage["completeness"], "complete")
        self.assertEqual(usage["nativeRecords"], 2)
        self.assertEqual(usage["inputTokens"], 3150)
        self.assertEqual(usage["outputTokens"], 300)
        self.assertEqual(usage["cachedInputTokens"], 150)
        self.assertEqual(usage["reasoningOutputTokens"], 30)
        message = result.last_assistant_message.value
        self.assertEqual(message["text"], "the final answer")
        self.assertFalse(message["truncated"])
        ref = next(ref for ref in result.evidence_refs if ref.kind == "dsh-session-record")
        facts = json.loads(Path(ref.location).read_text())
        self.assertEqual(facts["model"], {"provider": "fake", "model": "m1"})
        self.assertEqual(len(facts["steps"]), 2)
        self.assertEqual(facts["sessions"], [result.native_identity.session_id])
        self.assertFalse(facts["truncated"])

    def test_a_quota_turn_end_keeps_its_machine_code_and_partial_usage(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "quota"]
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.usage.value["completeness"], "partial")
        failure = result.native_failure.value
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertEqual(failure["nativeCode"], "QUOTA")
        self.assertEqual(failure["source"], "dsh/session-turn-end")
        retained = json.loads(Path(next(ref for ref in result.evidence_refs
                                        if ref.kind == "dsh-session-record").location).read_text())
        self.assertNotIn("provider wording", json.dumps(retained))

    def test_a_foreign_session_record_is_never_attributed(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "foreign"]
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.usage)
        self.assertIsNone(result.native_failure)
        self.assertEqual([ref for ref in result.evidence_refs
                          if ref.kind == "dsh-session-record"], [])

    def test_an_unreadable_record_keeps_every_other_fact(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "corrupt"]
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.usage)
        self.assertIsNone(result.native_failure)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual([ref for ref in result.evidence_refs
                          if ref.kind == "dsh-session-record"], [])

    def test_an_over_bound_record_is_partial_but_usable(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "flood"]
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        usage = result.usage.value
        self.assertEqual(usage["completeness"], "partial", "a bound stop is never complete")
        self.assertEqual(usage["inputTokens"], 3150)
        facts = json.loads(Path(next(ref for ref in result.evidence_refs
                                     if ref.kind == "dsh-session-record").location).read_text())
        self.assertTrue(facts["truncated"])

    def test_an_uncompressed_record_rolls_out_too(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--session-record", "plain"]
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.usage.value["completeness"], "complete")

    def test_one_format_correction_runs_a_second_session_on_the_same_process(self):
        self.extra_agent_args = ["--prompt-mode", "final",
                                 "--final-answer", "not json", "--final-answer", '{"choice":"a"}']
        prompt = no_tool_prompt("Choose a profile", SCHEMA)
        correction = FastCorrection(SCHEMA, prompt)
        result = run(self.fast_request(prompt), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.correction_count, 1)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(len(result.root_identities), 2)
        self.assertEqual(correction.correction_count, 1)
        started = [entry for entry in self.agent_log() if entry.get("event") == "startup"]
        self.assertEqual(1, len(started), "the correction reuses the one process")

    def test_a_tool_fact_stops_the_run_through_the_observer_feedback(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--emit-tool-update",
                                 "--final-answer", '{"choice":"a"}']
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "observer-request")
        package = result.tool_evidence.value
        self.assertFalse(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 1)
        categories = {event["category"] for event in package["events"]}
        self.assertEqual(categories, {"other"},
                         "an unrecognized native tool name stays other")
        self.assertTrue(all(event["toolName"] == "run-command" for event in package["events"]))

    def test_an_unknown_event_is_retained_then_stopped_by_the_role(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--emit-unknown-update",
                                 "--final-answer", '{"choice":"a"}']
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(correction.stop_reason, "invalid-protocol")
        self.assertEqual(result.unknown_events.total, 1)
        self.assertEqual(dict(result.unknown_events.counts).get("buddy_probe_unknown"), 1)

    def test_a_write_scope_without_a_service_takes_the_final_message_path(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}']
        request = self.fast_request("prompt", scope="write")
        result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.effective_policy.tools.enforcement, "unrestricted")
        self.assertEqual(result.effective_policy.filesystem.enforcement, "unrestricted")
        self.assertEqual(result.effective_policy.tools.basis, "dsh-default-workspace-write-preset")

    def test_a_read_scope_reports_the_preset_and_injects_only_its_key(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}']
        result = run(self.fast_request("prompt", scope="read"),
                     observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        policy = result.effective_policy
        self.assertEqual(policy.tools.enforcement, "native")
        self.assertEqual(policy.tools.requested.value, {"permissionMode": "read-only"})
        self.assertEqual(policy.filesystem.enforcement, "unknown",
                         "the command-write smoke is the Host's post-wiring run")
        record = self.launch_records()[-1]
        self.assertIn(native_run.PERMISSION_MODE_ENV, record["envKeys"])
        self.assertTrue(record["dshHome"].startswith(str(self.root)),
                        "the forced private home lives inside this run's native root")

    def test_the_none_scope_patch_reaches_the_launch_argv_and_policy(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}']
        result = run(self.fast_request("prompt"),
                     observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        record = self.launch_records()[-1]
        self.assertIn("--patch", record["argv"])
        patch_path = Path(record["argv"][record["argv"].index("--patch") + 1])
        rows = json.loads(patch_path.read_text())
        # The uniform order: the private session-record root pin (this test's
        # source home binds nothing), then the disabled tool rows with
        # plan-mode, then the two always-off rows last.
        self.assertEqual([row["id"] for row in rows],
                         ["session-persistence-jsonl", *CONFIRMED_NONE_ROWS,
                          native_run.PLAN_MODE_ROW, *native_run._ALWAYS_DISABLED_ROWS])
        pin = rows[0]
        self.assertTrue(pin["config"]["root"].startswith(str(self.root)))
        self.assertTrue(pin["config"]["root"].endswith("/sessions"))
        policy = result.effective_policy.tools
        self.assertEqual(policy.enforcement, "native")
        self.assertEqual(policy.requested.value["disabledRows"],
                         [row["id"] for row in rows[1:]])

    def test_the_shipped_none_scope_rows_are_the_confirmed_inventory(self):
        self.assertEqual(native_run.NONE_SCOPE_DISABLED_ROWS, CONFIRMED_NONE_ROWS,
                         "the shipped list must stay the Host-confirmed inventory")

    def test_an_unconfirmed_row_list_refuses_to_launch(self):
        # The migration guard's own fault path: a guessed or emptied row list
        # never launches a none-scope run.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        with mock.patch.object(native_run, "NONE_SCOPE_DISABLED_ROWS", ()):
            result = run(self.fast_request("prompt", scope="none"),
                         observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "scope-configuration-unconfirmed")
        self.assertIsNone(result.model_started)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(result.stop_evidence.native.observation_basis, "spawn-never-happened")

    def test_a_spawn_failure_reports_an_unavailable_agent_and_no_started_model(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        with mock.patch.object(native_run, "command_for",
                               return_value=["/no/such/dsh-interpreter"]):
            result = run(self.fast_request("prompt", scope="write"),
                         observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "adapter-unavailable")
        self.assertIsNone(result.model_started)
        self.assertEqual(result.stop_evidence.native.observation_basis, "spawn-never-happened")
        self.assertIsNone(result.harness_version)

    def test_an_agent_lost_before_any_session_never_claims_spawn_never_happened(self):
        # The agent was spawned and held, then died before the handshake: the
        # run's own group observation rules the stop layer, never the missing
        # session. No-session is not no-process.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--die-before-answer", "initialize"]
        result = run(self.fast_request("prompt", scope="write"),
                     observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-disconnected")
        self.assertIsNone(result.model_started)
        stop = result.stop_evidence.native
        self.assertTrue(stop.started, "the process was held, so the run started one")
        self.assertEqual(stop.group_state, "gone")
        self.assertEqual(stop.observation_basis, "owned-acp-process-group")

    def test_an_unobserved_agent_before_any_session_stays_unknown(self):
        # The direct regression for the rejected delivery: a held process whose
        # group cannot be observed stays unknown — never gone, never
        # spawn-never-happened — because a missing session proved nothing.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--die-before-answer", "initialize"]
        unconfirmed = {"shutdownConfirmed": False, "groupObserved": "unknown",
                       "leaderExited": True, "leaderExitCode": 0}
        with mock.patch.object(native_run.AcpClient, "shutdown", return_value=dict(unconfirmed)), \
                mock.patch.object(native_run, "stop_evidence", return_value=dict(unconfirmed)):
            result = run(self.fast_request("prompt", scope="write"),
                         observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-disconnected")
        stop = result.stop_evidence.native
        self.assertEqual(stop.group_state, "unknown", "an unobservable group is never gone")
        self.assertTrue(stop.started)
        self.assertEqual(stop.observation_basis, "owned-acp-process-group")
        self.assertIsNone(result.model_started)

    def test_launch_bookkeeping_failure_keeps_the_wrapper_stop_evidence(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        error = native_run.LaunchOwnershipError(
            "bookkeeping failed after spawn", process=object(), handle=object(),
            evidence={"shutdownConfirmed": False, "groupObserved": "unknown",
                      "leaderExitCode": None})
        with mock.patch.object(native_run.AcpClient, "start", side_effect=error):
            result = run(self.fast_request("prompt", scope="write"),
                         observer=lambda _facts: FEEDBACK_CONTINUE,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "adapter-unavailable")
        stop = result.stop_evidence.native
        self.assertEqual(stop.group_state, "unknown",
                         "the wrapper's own finalize evidence rules, not the session count")
        self.assertTrue(stop.started)
        self.assertEqual(stop.observation_basis, "launch-bookkeeping-finalize")
        self.assertIsNone(result.model_started)

    def test_a_late_tool_fact_reaches_the_role_and_stops_the_run(self):
        # A same-root tool fact emitted while the client drains at EOF still
        # reaches the role observer, whose no-tool rule then refuses the run.
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--late-tool-update"]
        correction = FastCorrection(SCHEMA, "prompt")
        result = run(self.fast_request("prompt"), observer=correction.observer,
                     services=None, cancelled=lambda: False)
        self.assertTrue(self.wait_for(
            lambda: any(entry.get("event") == "late-tool-emitted" for entry in self.agent_log())))
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "observer-interrupt")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1, "the late fact stays a retained fact")
        self.assertFalse(package["streamComplete"], "the role's stop ends the stream")

    def test_a_late_tool_fact_is_retained_when_the_role_continues(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--late-tool-update"]
        result = run(self.fast_request("prompt"), observer=lambda _f: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1)
        # The fact arrived after this root's turn had already ended, so the
        # shared tool evidence honestly marks the stream late; the drain
        # itself completed and the run's verdict is untouched by either.
        self.assertFalse(package["streamComplete"])
        self.assertEqual(package["unsettledToolCalls"], 0)

    def test_a_foreign_root_chunk_never_becomes_the_answer(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--emit-foreign-chunk"]
        result = run(self.fast_request("prompt"), observer=lambda _f: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw, '{"choice":"a"}', "only this run's own root answers")
        self.assertEqual(dict(result.unknown_events.counts).get("foreign-root-text"), 1,
                         "the foreign chunk stays an isolated unknown-origin fact")
        self.assertEqual(result.unknown_events.total, 1)

    def test_sixty_five_unknown_kinds_stay_bounded_and_keep_the_total(self):
        # The shared contract bounds the distinct kinds; the surplus folds into
        # one bucket so an over-wide classification can no longer cost the run
        # its result or its stop facts.
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}',
                                 "--spam-unknown-kinds", "65"]
        result = run(self.fast_request("prompt"), observer=lambda _f: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.unknown_events.total, 65)
        self.assertLessEqual(len(result.unknown_events.counts),
                             run_contract.MAX_UNKNOWN_EVENT_TYPES)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_an_unconfirmed_native_group_stop_reports_unknown_not_gone(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}']
        unconfirmed = {"shutdownConfirmed": False, "groupObserved": "unknown",
                       "leaderExited": True, "leaderExitCode": 0}
        # The synthetic observation stays fully in memory: the shutdown and the
        # follow-up group observation both report unconfirmed, while the real
        # agent (already stopped) is terminated once more without any effect.
        with mock.patch.object(native_run.AcpClient, "shutdown", return_value=dict(unconfirmed)), \
                mock.patch.object(native_run, "stop_evidence", return_value=dict(unconfirmed)):
            result = run(self.fast_request("prompt"), observer=lambda _f: FEEDBACK_CONTINUE,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-shutdown-failed")
        self.assertEqual(result.stop_evidence.native.group_state, "unknown",
                         "an unavailable observation is never a stop proof")
        self.assertTrue(result.stop_evidence.native.leader_exited)
        self.assertTrue(result.tool_evidence.value["streamComplete"],
                        "the drained stream and the ended turn prove the stream itself, "
                        "even while the group stop stays unknown")

    def test_a_parked_agent_is_ended_by_the_owned_group_after_the_deadline(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--park-prompt"]
        result = run(self.fast_request("prompt", timeout=2),
                     observer=lambda _f: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "deadline")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "deadline-budget")
        self.assertEqual(result.stop_evidence.native.group_state, "gone",
                         "the parked agent's group is terminated and confirmed gone")
        self.assertTrue(self.wait_for(
            lambda: any(entry.get("event") == "stdin-eof" for entry in self.agent_log())))


class CancelTests(NativeRunCase):
    def test_a_cancelled_flag_interrupts_the_run_and_reports_the_transport_fact(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        self.extra_agent_args = ["--prompt-mode", "final", "--hang-prompt",
                                 "--final-answer", "never"]
        request = self.fast_request("prompt", scope="write", timeout=60)
        flag = threading.Event()
        result_holder: list = []

        def stop_soon():
            self.assertTrue(self.wait_for(lambda: any(
                entry.get("event") == "hanging-prompt" for entry in self.agent_log())))
            flag.set()

        thread = threading.Thread(target=stop_soon)
        thread.start()
        try:
            result_holder.append(run(request, observer=lambda _f: FEEDBACK_CONTINUE,
                                     services=None, cancelled=flag.is_set))
        finally:
            thread.join()
        result = result_holder[0]
        self.assertEqual(result.end.status, "cancelled")
        self.assertEqual(result.end.reason_code, "native-cancelled")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.interrupt.basis, "session-cancel-notification")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertTrue(self.wait_for(lambda: any(
            entry.get("event") == "cancel-received" for entry in self.agent_log())))


class MountTests(NativeRunCase):
    """The process-free session-service binding and its seam checks."""

    def test_the_mount_describes_qualified_tools_with_an_array_env(self):
        request, bound, mount = self.worker_request()
        services = bound.services
        self.assertEqual(bound.completion_tool, mount.finish_tool)
        self.assertTrue(mount.finish_tool.startswith(f"mcp__{mount.server_name}__"))
        server = mount.mcp_servers[0]
        self.assertEqual(server["name"], mount.server_name)
        self.assertEqual(server["command"], sys.executable)
        self.assertIn("-m", server["args"])
        self.assertIsInstance(server["env"], list)
        bridge = json.loads(Path(mount.bridge_config_path).read_text())
        self.assertEqual(bridge["identity"]["attemptId"], request.identity.attempt_id)
        self.assertEqual(bridge["inputSha256"], request.identity.input_sha256)
        self.assertTrue(bridge["key"])
        self.assertEqual(request.session_services[0].tool_names,
                         tuple(f"mcp__{mount.server_name}__{name}" for name in mount.bare_tools))
        self.assertEqual(bound.completion_tool, mount.finish_tool)

    def test_the_mount_names_the_shared_roles_carrier_in_its_command(self):
        from hey_my_buddy.buddy.roles import turn_io
        mount = prepare_session_service(
            invocation_root=self.base / "m2", identity={"taskId": "t", "attemptId": "a",
                                                        "generation": 1, "turnId": "u"},
            input_sha256="0" * 64, attention_path=self.base / "att2.json",
            session_tools=worker_services.session_tools(),
            completion_tool="buddy_finish_turn")
        command = mount.mcp_servers[0]
        self.assertIn(CARRIER_MODULE, command["args"])
        self.assertIn("--config", command["args"])
        self.assertTrue(turn_io.Path(mount.bridge_config_path).is_file())
        # The named carrier actually exists on this runtime: the mount names a
        # consumable module, never a guessed path.
        from hey_my_buddy.buddy.roles import session_mcp
        self.assertEqual(CARRIER_MODULE, session_mcp.__name__)

    def test_descriptions_and_schema_are_checked_before_any_process(self):
        request, _bound, mount = self.worker_request()
        self.assertIsNone(native_run._check_service_descriptions(request, mount))
        mismatched = self.variant_of(request,
                                     session_services=(SessionService(tool_names=["mcp__other__buddy_finish_turn"]),))
        with self.assertRaises(BoardError):
            native_run._check_service_descriptions(mismatched, mount)
        other_schema = run_contract.FrozenJson.from_value({"type": "object"}, "schema",
                                                          maximum=MAX_SCHEMA_BYTES)
        wrong_schema = self.variant_of(request, output_schema=other_schema)
        with self.assertRaises(BoardError):
            native_run._check_service_descriptions(wrong_schema, mount)

    def test_the_seam_refuses_foreign_harnesses_services_and_continuations(self):
        request, bound, _mount = self.worker_request()
        services = bound.services
        with self.assertRaises(BoardError):
            run(self.variant_of(request, harness="zcode"),
                observer=worker_observer, services=services, cancelled=lambda: False)
        with self.assertRaises(BoardError):
            run(request, observer=worker_observer, services=object(), cancelled=lambda: False)
        continuation = run_contract.RunContinuation(mode="reconstructed-new-session")
        with self.assertRaises(BoardError):
            run(self.variant_of(request, continuation=continuation),
                observer=worker_observer, services=services, cancelled=lambda: False)

    @staticmethod
    def variant_of(request: RunRequest, **overrides) -> RunRequest:
        fields = {name: getattr(request, name) for name in
                  ("identity", "harness", "configuration", "cwd", "private_state", "input_text",
                   "tool_scope", "output_schema", "budget", "session_services")}
        fields.update(overrides)
        return RunRequest(**fields)


class WorkerSeamTests(NativeRunCase):
    """The governed completion-tool carrier over real signed receipts."""

    def test_a_governed_turn_settles_with_a_verified_completion_tool_value(self):
        request, bound, mount = self.worker_request()
        self.governed_agent_args(mount)
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.mechanism, "completion-tool")
        self.assertEqual(result.value.schema_status, "valid")
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        self.assertEqual(result.value.validation_basis, "signed-receipt-role-validator")
        self.assertTrue(result.completion_evidence.receipt_verified)
        self.assertEqual(result.completion_evidence.call_id, "call_finish_1")
        self.assertEqual(result.completion_evidence.native_outcome, "end_turn")
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.native_identity.call_id, "call_finish_1")
        self.assertEqual(result.model_start_evidence.basis, "native-start")
        checked = result.configuration.checked
        self.assertEqual(checked.model.basis, "native-readback")
        self.assertEqual(checked.model.value, "m1")
        self.assertEqual(checked.effort.value, "high")
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        package = result.tool_evidence.value
        self.assertTrue(package["streamComplete"])
        # The checkpoint refusal and the finish call are the verified delivery
        # calls: their events leave the published package, nothing else hides.
        self.assertEqual(package["events"], [])
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(package["nativeIdentity"],
                         [{"sessionId": result.native_identity.session_id}])

    def test_a_worker_turn_reports_the_record_usage_and_mirrors_its_stderr(self):
        # The Worker token-usage capability: a governed turn whose native record
        # exists reports the shared usage facts — the fact must not degrade to
        # None while a real record stands. The bounded stderr tail also reaches
        # the role's own mirror path.
        request, bound, mount = self.worker_request()
        note = "fixture stderr for the governed mirror"
        self.governed_agent_args(mount)
        self.extra_agent_args += ["--session-record", "usage", "--stderr-note", note]
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        usage = result.usage.value
        self.assertEqual(usage["completeness"], "complete")
        self.assertEqual(usage["nativeRecords"], 2)
        self.assertEqual(usage["inputTokens"], 3150)
        self.assertEqual(result.last_assistant_message.value["text"], "the final answer")
        mirror = Path(bound.services.native_stderr)
        self.assertTrue(mirror.is_file())
        self.assertIn(note, mirror.read_text())
        ref = next(ref for ref in result.evidence_refs if ref.kind == "dsh-session-record")
        facts = json.loads(Path(ref.location).read_text())
        self.assertEqual(facts["model"], {"provider": "fake", "model": "m1"})

    def test_a_refused_upgrade_request_becomes_attention_not_completed(self):
        request, bound, mount = self.worker_request()
        self.governed_agent_args(mount, permission=True)
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.parsed.value["disposition"], "attention",
                         "the role's own completion rule refused completed while the upgrade was outstanding")
        attention = json.loads(Path(bound.services.mount.bridge["attentionPath"]).read_text())
        self.assertEqual(len(attention["requests"]), 1)
        self.assertIn("reject_once", attention["requests"][0]["basis"],
                      "the reject-only policy selects the reject option")
        denied = result.denied_interactions
        self.assertTrue(any(item.method == "session/request_permission"
                            and item.action == "permission-refused" for item in denied))
        refs = [ref for ref in result.evidence_refs if ref.kind == "permission-decisions"]
        self.assertEqual(len(refs), 1)
        decisions = json.loads(Path(refs[0].location).read_text())["records"]
        self.assertEqual(decisions[0]["outcome"].get("optionId"), "opt-reject",
                         "the escalation request was answered with the reject option")

    def test_a_failed_finish_result_is_retryable_inside_the_same_turn(self):
        request, bound, mount = self.worker_request()
        self.governed_agent_args(mount, finish_fail_then_retry=True)
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.completion_evidence.call_id, "call_finish_2")
        self.assertEqual(result.value.parsed.value["disposition"], "completed")
        package = result.tool_evidence.value
        self.assertEqual(package["toolCalls"], 1,
                         "the natively failed finish call stays a task-tool fact; "
                         "only its verified retry is the delivery evidence")
        self.assertEqual({event["callId"] for event in package["events"]}, {"call_finish_1"})

    def test_a_completed_turn_without_a_finish_tool_fails(self):
        request, bound, mount = self.worker_request()
        self.governed_agent_args(mount, no_finish=True)
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "missing-finish")
        self.assertIsNone(result.value)

    def test_a_tampered_finish_receipt_is_fatal(self):
        request, bound, mount = self.worker_request()
        self.governed_agent_args(mount, forge_receipt=True)
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "invalid-finish")
        self.assertIsNone(result.value)
        self.assertIsNone(result.completion_evidence)

    def test_the_governed_activity_sidecar_publishes_bounded_phases(self):
        request, bound, mount = self.worker_request()
        activity_dir = self.base / f"activity-{uuid.uuid4().hex[:8]}"
        services = SessionServices(mount=bound.services.mount,
                                   validate_outcome=bound.services.validate_outcome,
                                   activity_dir=str(activity_dir))
        self.governed_agent_args(mount)
        result = run(request, observer=worker_observer, services=services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        sidecar = activity_dir / "activity.json"
        self.assertTrue(sidecar.is_file())
        document = json.loads(sidecar.read_text())
        self.assertEqual(document["taskId"], request.identity.task_id)
        self.assertIn(document["activity"]["phase"],
                     ("streaming-model", "tool-running", "finishing", "waiting-model"))
        self.assertGreaterEqual(document["activity"]["counts"]["toolCalls"], 2)

    def test_an_answer_with_no_session_service_takes_no_governed_path(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}']
        request = self.fast_request("prompt", scope="write")
        described = RunRequest.from_payload(
            request.to_payload() | {"sessionServices": [{"toolNames": ["mcp__x__buddy_finish_turn"]}]})
        with self.assertRaises(BoardError):
            run(described, observer=worker_observer, services=None, cancelled=lambda: False)


class DiscoveryTests(NativeRunCase):
    """The no-prompt discovery: declared selectors only, never a model call."""

    def test_discovery_reads_the_declared_selectors_without_a_prompt(self):
        self.extra_agent_args = ["--prompt-mode", "final"]
        catalog = native_run.run_discovery(
            cwd=str(self.base / "cwd"), invocation_root=self.base / "disc-inv",
            native_root=self.root, timeout_seconds=FAST_TIMEOUT, cancelled=lambda: False)
        self.assertEqual(catalog["adapter"], "dsh")
        self.assertEqual(catalog["source"], "dsh-acp-session-config")
        self.assertEqual(catalog["harnessVersion"], "0.0.1")
        self.assertEqual(catalog["discoveries"], [{"adapter": "dsh", "status": "complete"}])
        provider = next(entry for entry in catalog["providers"] if entry["provider"] == "fake")
        models = {model["id"]: model for model in provider["models"]}
        self.assertEqual(set(models), {"m1", "m2"})
        self.assertEqual(models["m1"]["efforts"], ["off", "low", "high", "max"])
        self.assertIsNone(models["m1"]["contextWindow"])
        prompts = [entry for entry in self.agent_log()
                   if entry.get("dir") == "in" and (entry.get("raw") or {}).get("method") == "session/prompt"]
        self.assertEqual(prompts, [], "discovery never sends a prompt")
        # Discovery carries the uniform launch patch: the private session-record
        # root pin and the two always-off rows, with no tool-scope rows.
        launch = self.launch_records()[-1]
        self.assertIn("--patch", launch["argv"])
        patch_path = Path(launch["argv"][launch["argv"].index("--patch") + 1])
        rows = json.loads(patch_path.read_text())
        self.assertEqual([row["id"] for row in rows],
                         ["session-persistence-jsonl", *native_run._ALWAYS_DISABLED_ROWS])


class SourceBindingTests(NativeRunCase):
    """The owning home's settings/credentials binding and the acp profile."""

    def test_source_rows_point_at_the_owning_home_without_reading_them(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        source = self.base / "owning-dsh-home"
        source.mkdir(mode=0o700)
        settings = source / "settings.yaml"
        credentials = source / ".credentials.yaml"
        settings.write_text("model: fixture\n")
        credentials.write_text("provider:\n  deepseek-official:\n    apiKey: fixture-secret\n")
        # The strongest proof the module never reads the content: the credential
        # file is made unreadable to this user; is_file() still sees it and the
        # run must still launch with the binding row in place.
        credentials.chmod(0o000)
        self.addCleanup(credentials.chmod, 0o600)
        with mock.patch.dict(os.environ, {"DSH_HOME": str(source)}):
            result = run(self.fast_request("prompt", scope="write"),
                         observer=lambda _f: FEEDBACK_CONTINUE,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        record = self.launch_records()[-1]
        patch_path = Path(record["argv"][record["argv"].index("--patch") + 1])
        rows = json.loads(patch_path.read_text())
        self.assertEqual([row["id"] for row in rows[:3]],
                         ["settings", "credentials", "session-persistence-jsonl"])
        self.assertEqual(rows[0]["config"]["path"], str(settings))
        self.assertEqual(rows[1]["config"]["path"], str(credentials))
        self.assertFalse(rows[0]["config"]["watch"])
        self.assertTrue(rows[2]["config"]["root"].startswith(str(self.root)))
        self.assertEqual([row["id"] for row in rows[3:]], list(native_run._ALWAYS_DISABLED_ROWS))

    def test_an_absent_owning_home_binds_nothing(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        with mock.patch.dict(os.environ, {"DSH_HOME": str(self.base / "absent-home")}):
            result = run(self.fast_request("prompt", scope="write"),
                         observer=lambda _f: FEEDBACK_CONTINUE,
                         services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        patch_path = Path(self.launch_records()[-1]["argv"][
            self.launch_records()[-1]["argv"].index("--patch") + 1])
        rows = json.loads(patch_path.read_text())
        self.assertEqual([row["id"] for row in rows],
                         ["session-persistence-jsonl", *native_run._ALWAYS_DISABLED_ROWS])

    def test_the_private_home_carries_the_host_proven_acp_profile(self):
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
        result = run(self.fast_request("prompt", scope="write"),
                     observer=lambda _f: FEEDBACK_CONTINUE,
                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        profile = self.dsh_home / "profiles" / "acp"
        manifest = json.loads((profile / "package.json").read_text())
        self.assertEqual(manifest["dsh"]["profile"]["bundles"],
                         ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"])
        self.assertEqual(manifest["dsh"]["profile"]["patchReload"], "startup")
        self.assertEqual((profile / "cordis.patch.yml").read_text().splitlines()[-1], "[]")
        self.assertEqual((profile / "cordis.yml").read_text().splitlines()[-1], "[]")
        self.assertIn("packages:", (profile / "pnpm-workspace.yaml").read_text())


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
