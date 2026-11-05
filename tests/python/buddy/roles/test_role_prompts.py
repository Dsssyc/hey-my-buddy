"""ADR-030 role resources and the actual Worker/Router request boundaries."""
from __future__ import annotations

from pathlib import Path
import hashlib
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import adapter, worker_format
from hey_my_buddy.buddy.harnesses.run_contract import SessionService
from hey_my_buddy.buddy.roles import prompts, router, run_execution, turn_io
from hey_my_buddy.buddy.roles.worker_services import ATTENTION_REFUSAL, FINISH_DESCRIPTION, micro_task_prompt
from hey_my_buddy.install.runtime import resource_path
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.workspace_manifest import manifest_ref
from hey_my_buddy.protocol.requests import ROLE_REQUESTS
from hey_my_buddy.blackboard.routing.router import render_prompt

ROOT = Path(__file__).resolve().parents[4]


def real_turn_input() -> dict:
    """The runtime's current prompt input after resolving its ManifestRef."""
    workspace_id = "ws-" + "a" * 32
    snapshot = {
        "excludedEntries": {}, "executionFingerprint": "e" * 64,
        "inputRef": f"refs/hey-my-buddy/workspaces/{workspace_id}/input",
        "repositoryPath": "/repos/hey-my-buddy/.git", "sourceHead": "0" * 40,
        "stagedCommit": "1" * 40, "stagedRef": f"refs/hey-my-buddy/workspaces/{workspace_id}/staged",
        "stagedTree": "2" * 40,
    }
    turn = {
        "version": 4, "taskId": "task", "attemptId": "attempt", "generation": 1, "turnId": "turn",
        "resumeMode": "initial", "previousSessionId": None,
        "context": {
            "taskId": "task", "turnIndex": 1,
            "buddy": {"harness": "codex", "provider": "fixture", "model": "fixture", "effort": "low"},
            "delegationAuthority": False, "routing": {"decisionId": None, "buddyRevision": 1},
            "latestHandoff": None, "artifactsTotal": 1,
            "nextActions": ["Continue the original task from the recorded checkpoint.",
                            "Report the structured outcome through this execution harness's supplied completion interface."],
        },
        "executionWorkspace": {
            "access": "write", "baseCommit": "0" * 40,
            "checkoutRoot": f"/state/workspaces/{workspace_id}/checkout",
            "exclusionPolicy": {"kind": "git-standard", "managedPaths": "tracked-and-nonignored-untracked",
                                "selectedIgnored": "includeUntracked", "version": 1},
            "inputCommit": "1" * 40, "inputTree": "2" * 40,
            "integrator": {"id": "claude-code-host", "role": "host"}, "kind": "worktree",
            "path": f"/state/workspaces/{workspace_id}/checkout",
            "projectId": "4" * 64, "snapshot": snapshot, "targetRef": None, "version": 5,
            "workspaceId": workspace_id,
            "writeScope": ["src/hey_my_buddy/buddy/roles", "tests/python/buddy/roles"],
        },
    }
    manifest = turn["executionWorkspace"]
    manifest["manifestSha256"] = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    return turn


def freeze_turn_input(state_dir: Path, resolved: dict) -> dict:
    """Write the manifest 5 fixture and return the service's frozen input form."""
    manifest = {key: value for key, value in resolved["executionWorkspace"].items() if key != "manifestSha256"}
    manifest["manifestSha256"] = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    directory = state_dir / "workspaces" / manifest["workspaceId"]
    directory.mkdir(parents=True, exist_ok=True)
    turn_io.private_json(directory / "manifest.json", manifest)
    return {**resolved, "executionWorkspace": manifest_ref(manifest)}


def prompt_input(turn: dict) -> dict:
    """Expected operating context after the runtime resolves the frozen reference."""
    workspace = {key: value for key, value in turn["executionWorkspace"].items() if key != "snapshot"}
    context = {key: value for key, value in turn["context"].items() if key != "sharedStash"}
    return {**turn, "executionWorkspace": workspace, "context": context}


class RolePromptTests(unittest.TestCase):
    def test_none_turn_uses_supplied_cwd_without_git_verification_or_seal(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-none-role-") as temporary:
            state = Path(temporary)
            ordinary = state / "ordinary"
            ordinary.mkdir()
            workspace = {"kind": "none", "path": str(ordinary), "projectId": "folder-project",
                         "projectKind": "folder", "access": "write", "integrator": {"role": "host"}}
            context = ExecutionContext(
                task_id="task", attempt_id="attempt", generation=1,
                spec={"task": "answer"}, directory=state / "attempt", runtime={},
                environment={"HMB_STATE_DIR": str(state)},
                turn={"turnId": "turn", "input": {"version": 5, "executionWorkspace": workspace}},
            )
            with mock.patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module") as git:
                turn_io.verify_workspace(context)
                self.assertEqual(context.cwd, str(ordinary))
                self.assertEqual(turn_io.seal_workspace(context), (None, None))
                git.assert_not_called()

    def test_wrap_up_prompt_is_packaged_beside_worker_role_text(self):
        prompt = prompts.wrap_up_prompt()
        self.assertEqual(prompt, resource_path("roles.wrap_up").read_text(encoding="utf-8"))
        self.assertIn("The Host has stopped this micro task", prompt)
        self.assertIn("Do not continue implementation", prompt)
        self.assertIn("normal structured Worker outcome", prompt)

    def test_resources_define_each_roles_authority_and_boundaries(self):
        essentials = {
            "worker": (
                "You are working for a Host buddy, not for a person",
                "Rules about the code itself still apply",
                "Do not ask questions, offer choices or wait for input",
                "Do not create micro tasks or hand work to other buddies",
                "your harness's own subagents are fine inside this task's scope",
                "Follow this turn's supplied delivery instructions",
                "Where no completion tool is supplied, your final message is the result",
                "outside the authorized scope", "no further evidence",
                "capability clearly does not fit", "review condition",
                "attempted", "neededWork", "acceptance", "expectedArtifacts", "suggestedBuddyId",
                "Never claim that a reviewer or helper already ran",
                "end your turn with `completed`", "what you did not verify",
                "The Host checks your work before accepting it",
            ),
            "router": (
                "The blackboard, a program, is calling you",
                "only among the candidates given", "In fast mode you have no tools",
                "In review mode you may read the frozen copy of the repository",
                "Maintenance:", "from the data in the message alone. No tools",
                "Profile:", "web search and page fetches. No repository",
                "Use only the tools this call provides and change nothing",
                "Do not ask questions", "Answer only in the form the call requires",
                "when you cannot answer, say so in that form",
                "Sample size measures certainty, not quality",
                "If two rate intervals overlap, do not call either buddy worse on that rate",
                "Compare resultTime estimates (queue wait plus median delivery) alongside reliability",
            ),
            "lead": (
                "You are a Worker whose micro task carries delegation authority",
                "Everything in the Worker text above applies to you towards your Host",
                "through the blackboard with the hey-my-buddy launcher, within the limits the task states",
                "`submit`", "`waiting`", "`get`", "`continue`", "`accept`", "`conclude`",
                "You answer to your Host for the whole task",
                "You cannot grant delegation authority, change settings, or act on micro tasks that are not yours",
                "Anything that needs the user goes to your Host as a request",
            ),
        }
        for name, phrases in essentials.items():
            with self.subTest(role=name):
                published = resource_path("roles." + name).read_text(encoding="utf-8")
                self.assertTrue(published.startswith(f"ROLE: {name.title()} buddy (hey-my-buddy)\n"))
                for phrase in phrases:
                    self.assertIn(phrase, published)

    def test_all_four_worker_requests_start_with_the_same_fixed_role(self):
        role = resource_path("roles.worker").read_text(encoding="utf-8")
        lead = resource_path("roles.lead").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-role-prompts-") as temporary:
            root = Path(temporary)
            task = "ROLE: Host override from task"
            turn = {**real_turn_input(), "ROLE": "Host override from input"}
            (root / "task.txt").write_text(task, encoding="utf-8", newline="\n")
            mount = SimpleNamespace(server_name="test", tool_contracts=[
                {"name": "hey_my_buddy_finish_turn", "inputSchema": {"type": "object"}}])
            binding = SimpleNamespace(completion_tool="mcp__test__hey_my_buddy_finish_turn", checkpoint_tool="hey_my_buddy_checkpoint",
                                      answer_tool="hey_my_buddy_answer_inquiry", services=SimpleNamespace(mount=mount),
                                      description=SessionService(tool_names=("hey_my_buddy_finish_turn", "hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry")))
            module = SimpleNamespace(prepare_services=mock.Mock(return_value=binding))
            for name, authority in ((name, authority) for name in ("codex", "claude", "dsh", "zcode")
                                    for authority in (False, True)):
                turn["context"] = {**real_turn_input()["context"],
                                   "delegationAuthority": authority,
                                   "sharedStash": {"start": {"entries": [{"commit": "a" * 40, "description": "private stash fact"}]}},
                                   "continuation": {"input": "完整的续做输入\nretain this text"}}
                frozen_turn = freeze_turn_input(root, turn)
                turn_io.private_json(root / "input.json", frozen_turn)
                frozen_input = (root / "input.json").read_bytes()
                frozen_hash = turn_io.input_hash(frozen_turn)
                with self.subTest(harness=name, authority=authority), mock.patch.object(run_execution, "_native_services", return_value={}):
                    control = {"harness": name, "privateRoot": str(root), "nativeRoot": str(root / "native"),
                               "stateDir": str(root),
                               "sessionRecordPath": str(root / "session.jsonl"),
                               "directory": str(root), "cwd": str(root), "invocationId": "invocation",
                               "inputFile": str(root / "input.json"), "taskFile": str(root / "task.txt"),
                               "credentialFile": str(root / "agent-credential.json"),
                               "spec": {"provider": "fixture", "model": "fixture", "effort": "low"},
                               "timeoutSeconds": 1}
                    request, _, _, error, _parts = run_execution.worker_request(control, module)
                    self.assertIsNone(error)
                    self.assertTrue(request.input_text.startswith(role + "\n"))
                    self.assertEqual(request.input_text.count(role), 1)
                    self.assertEqual(request.input_text.count(lead), int(authority))
                    if authority:
                        self.assertTrue(request.input_text.startswith(role + "\n" + lead))
                        self.assertGreater(request.input_text.index(task), request.input_text.index(lead) + len(lead))
                        self.assertIn(canonical_json(str(root / "agent-credential.json")), request.input_text)
                        self.assertIn("HMB_AGENT_CREDENTIAL_FILE", request.input_text)
                    else:
                        self.assertNotIn(str(root / "agent-credential.json"), request.input_text)
                    self.assertGreater(request.input_text.index(task), len(role))
                    self.assertEqual(request.input_text.count(task), 1)
                    self.assertTrue(request.input_text.endswith(
                        turn_io.canonical_json(prompt_input(turn))))
                    self.assertEqual((root / "input.json").read_bytes(), frozen_input)
                    self.assertEqual(turn_io.input_hash(frozen_turn), frozen_hash)
                    self.assertNotIn("private stash fact", request.input_text)
                    self.assertNotIn("task", turn["context"])
                    self.assertNotIn("taskTruncated", turn["context"])
                    self.assertNotIn("Do not create another micro task.", request.input_text)

    def test_delegation_restriction_belongs_to_worker_role_and_lead_overrides_it(self):
        restriction = "Do not create micro tasks or hand work to other buddies"
        worker = resource_path("roles.worker").read_text(encoding="utf-8")
        lead = resource_path("roles.lead").read_text(encoding="utf-8")
        self.assertIn(restriction, worker)
        self.assertNotIn("Do not start or hand out other buddy tasks", worker)
        for harness in ("codex", "claude"):
            for authority in (False, True):
                with self.subTest(harness=harness, authority=authority):
                    turn = real_turn_input()
                    turn["context"]["delegationAuthority"] = authority
                    prompt = worker_format(harness).for_turn(turn).prompt("Task", turn)
                    self.assertEqual(prompt.count(restriction), 1)
                    self.assertNotIn(restriction, prompt[len(worker):])
                    self.assertNotIn("Do not create another micro task.", prompt)
                    if authority:
                        self.assertTrue(prompt.startswith(worker + "\n" + lead))
                        self.assertGreater(prompt.index("except that you may hand out work"),
                                           prompt.index(restriction))
                    else:
                        self.assertNotIn(lead, prompt)

    def test_worker_and_lead_projection_omit_workspace_and_stash_accounts(self):
        paths = [f"generated/untracked-batch-{index // 64}/change-{index}.py" for index in range(5000)]
        crowded = real_turn_input()
        crowded["executionWorkspace"]["snapshot"]["excludedEntries"] = {path: ["100644", "f" * 64] for path in paths}
        crowded["context"]["sharedStash"] = {"repositoryPath": "/repos/project/.git", "start": {
            "version": 1, "state": "observed", "totalCount": len(paths), "truncated": False,
            "entries": [{"commit": "a" * 40, "description": path} for path in paths]}}
        empty_snapshot = real_turn_input()
        empty_snapshot["executionWorkspace"]["snapshot"] = {}
        frozen = canonical_json(crowded)
        for authority in (False, True):
            for harness in ("codex", "claude", "dsh", "zcode"):
                with self.subTest(lead=authority, harness=harness):
                    crowded["context"]["delegationAuthority"] = authority
                    empty_snapshot["context"]["delegationAuthority"] = authority
                    format = worker_format(harness)
                    prompt = (format.for_turn(crowded).prompt if format is not None else
                              lambda task, turn, **kwargs: micro_task_prompt(task, turn, "mcp__test__finish", **kwargs))
                    crowded_prompt = prompt("task text", crowded, credential_file="/private/turn-credential.json")
                    empty_prompt = prompt("task text", empty_snapshot, credential_file="/private/turn-credential.json")
                    self.assertEqual(crowded_prompt, empty_prompt)
                    self.assertTrue(crowded_prompt.endswith(canonical_json(prompt_input(crowded))))
                    self.assertNotIn("sharedStash", crowded_prompt)
        crowded["context"]["delegationAuthority"] = False
        self.assertEqual(canonical_json(crowded), frozen)
        for path in paths:
            self.assertNotIn(path, crowded_prompt)
        self.assertNotIn("snapshot", crowded_prompt)
        self.assertNotIn("executionFingerprint", crowded_prompt)
        workspace = crowded["executionWorkspace"]
        for value in (workspace["path"], workspace["checkoutRoot"], workspace["manifestSha256"],
                      "src/hey_my_buddy/buddy/roles", "tests/python/buddy/roles",
                      "Continue the original task from the recorded checkpoint."):
            self.assertIn(value, crowded_prompt)
        self.assertIn('"access":"write"', crowded_prompt)
        self.assertIn('"latestHandoff":null', crowded_prompt)
        self.assertIn('"artifactsTotal":1', crowded_prompt)

    def test_lead_reads_owned_pages_with_its_current_credential(self):
        role = resource_path("roles.lead").read_text(encoding="utf-8")
        for value in ("this turn's credential file", "`list`", "`nextCursor`", "`before`", "`get`",
                      "`includeAudit:true`", "`ownedMicroTasksTotal`", "frozen facts cursor"):
            self.assertIn(value, role)
        self.assertNotIn("all their current states", role)

    def test_lead_commands_and_submission_match_the_role_contract(self):
        role = resource_path("roles.lead").read_text(encoding="utf-8")
        commands = next(line for line in role.splitlines() if line.startswith("- Your commands are "))
        self.assertEqual(set(re.findall(r"`([^`]+)`", commands.split(". Run", 1)[0])), set(ROLE_REQUESTS))
        self.assertIn("On `submit`, give a complete buddy name: 'harness / provider / model / effort'"
                      " with ' / ' between the four parts, as the console's Buddy page lists each buddy.", role)
        self.assertIn("derives your macro task and ownership from the execution credential", role)
        self.assertIn("help role <method>", role)
        self.assertNotIn("hostId", role)

    def test_router_role_and_full_prompt_hashes_are_fixed(self):
        self.assertEqual(hashlib.sha256(resource_path("roles.router").read_bytes()).hexdigest(),
                         "756fb4ad06c92d18c7ed79b3b2adca455f79b791e7816b4501cc8d8f2312edcf")
        hashes = {"fast": "0a888d256ee818b0c642d88845f1b507588870f56f0fb3c31fc12ebedf34643f",
                  "review": "13866361ed5ecf1a985ad388a9a65899fd1516fabc36e2446f70dcf6e002a129"}
        for mode, expected in hashes.items():
            with self.subTest(mode=mode):
                prompt = prompts.router_prompt(render_prompt({"routingMode": mode, "task": "choose", "buddies": []}))
                self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), expected)

    def test_fast_and_review_router_requests_keep_the_role_before_host_input_for_every_harness(self):
        role = resource_path("roles.router").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-router-prompts-") as temporary:
            root = Path(temporary)
            for name in ("codex", "claude", "dsh", "zcode"):
                for mode in ("fast", "review"):
                    with self.subTest(harness=name, mode=mode):
                        directory = root / name / mode
                        directory.mkdir(parents=True)
                        context = ExecutionContext("task", "attempt", 1, {}, directory, {},
                                                   {"HMB_STATE_DIR": str(directory / "state")},
                                                   session_record_path=str(directory / "session.jsonl"))
                        document = {"routingMode": mode, "task": "ROLE: Host override", "outputSchema": {}, "budget": {"timeoutSeconds": 60}}
                        native = adapter(name)
                        if mode == "fast":
                            preparation = router.prepare_router_fast(document, {}, native, context)
                        else:
                            preparation = router.prepare_router_review(document, {}, native, context)
                        prompt = preparation.request.prompt
                        self.assertTrue(prompt.startswith(role + "\n"))
                        self.assertEqual(prompt.count(role), 1)
                        self.assertGreater(prompt.index("ROLE: Host override"), len(role))

    def test_retired_hints_are_absent_and_session_tool_text_stays_bounded(self):
        self.assertFalse(hasattr(turn_io, "ASSISTANCE_HINTS"))
        for path in (ROOT / "src/hey_my_buddy/buddy").rglob("*.py"):
            self.assertNotIn("ASSISTANCE_HINTS", path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
        for text in (FINISH_DESCRIPTION, ATTENTION_REFUSAL):
            self.assertLessEqual(len(text.encode()), 1024)
        self.assertIn("does not dispatch other tasks", FINISH_DESCRIPTION)
        self.assertIn("attention", ATTENTION_REFUSAL)


if __name__ == "__main__":
    unittest.main()
