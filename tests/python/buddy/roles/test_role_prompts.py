"""ADR-030 role resources and the actual Worker/Router request boundaries."""
from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import adapter
from hey_my_buddy.buddy.harnesses.run_contract import SessionService
from hey_my_buddy.buddy.roles import prompts, router, run_execution, turn_io
from hey_my_buddy.buddy.roles.worker_services import ATTENTION_REFUSAL, FINISH_DESCRIPTION
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install.runtime import resource_path

ROOT = Path(__file__).resolve().parents[4]


class RolePromptTests(unittest.TestCase):
    def test_wrap_up_prompt_is_packaged_beside_worker_role_text(self):
        prompt = prompts.wrap_up_prompt()
        self.assertEqual(prompt, resource_path("roles.wrap_up").read_text(encoding="utf-8"))
        self.assertIn("The Host has stopped this micro task", prompt)
        self.assertIn("Do not continue implementation", prompt)
        self.assertIn("normal structured Worker outcome", prompt)

    def test_resources_define_each_roles_authority_and_boundaries(self):
        essentials = {
            "worker": (
                "You are working for a Host agent, not for a person",
                "Rules about the code itself still apply",
                "Do not ask questions, offer choices or wait for input",
                "Do not start or hand out other buddy tasks",
                "your harness's own subagents are fine inside this task's scope",
                "hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry", "hey_my_buddy_finish_turn",
                "Where these tools are absent, your final message is the result",
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
                "Buddy:", "web search and page fetches. No repository",
                "Use only the tools this call provides and change nothing",
                "Do not ask questions", "Answer only in the form the call requires",
                "when you cannot answer, say so in that form",
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
                published = resource_path("roles." + name).read_text()
                self.assertTrue(published.startswith(f"ROLE: {name.title()} buddy (hey-my-buddy)\n"))
                for phrase in phrases:
                    self.assertIn(phrase, published)

    def test_all_four_worker_requests_start_with_the_same_fixed_role(self):
        role = resource_path("roles.worker").read_text()
        lead = resource_path("roles.lead").read_text()
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-role-prompts-") as temporary:
            root = Path(temporary)
            task = "ROLE: Host override from task"
            turn = {"taskId": "task", "attemptId": "attempt", "generation": 1, "turnId": "turn",
                    "resumeMode": "initial", "executionWorkspace": {"access": "write"},
                    "ROLE": "Host override from input"}
            (root / "task.txt").write_text(task)
            turn_io.private_json(root / "input.json", turn)
            binding = SimpleNamespace(completion_tool="hey_my_buddy_finish_turn", checkpoint_tool="hey_my_buddy_checkpoint",
                                      answer_tool="hey_my_buddy_answer_inquiry", services={},
                                      description=SessionService(tool_names=("hey_my_buddy_finish_turn", "hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry")))
            module = SimpleNamespace(prepare_services=mock.Mock(return_value=binding))
            for name, authority in ((name, authority) for name in ("codex", "claude", "dsh", "zcode")
                                    for authority in (False, True)):
                turn["context"] = {"delegationAuthority": authority}
                turn_io.private_json(root / "input.json", turn)
                with self.subTest(harness=name, authority=authority), mock.patch.object(run_execution, "_native_services", return_value={}):
                    control = {"harness": name, "privateRoot": str(root), "nativeRoot": str(root / "native"),
                               "directory": str(root), "cwd": str(root), "invocationId": "invocation",
                               "inputFile": str(root / "input.json"), "taskFile": str(root / "task.txt"),
                               "spec": {"provider": "fixture", "model": "fixture", "effort": "low"},
                               "timeoutSeconds": 1}
                    request, _, _, error = run_execution.worker_request(control, module)
                    self.assertIsNone(error)
                    self.assertTrue(request.input_text.startswith(role + "\n"))
                    self.assertEqual(request.input_text.count(role), 1)
                    self.assertEqual(request.input_text.count(lead), int(authority))
                    if authority:
                        self.assertTrue(request.input_text.startswith(role + "\n" + lead))
                        self.assertGreater(request.input_text.index(task), request.input_text.index(lead) + len(lead))
                    self.assertGreater(request.input_text.index(task), len(role))
                    self.assertTrue(request.input_text.endswith(turn_io.canonical_json(turn)))

    def test_fast_and_review_router_requests_keep_the_role_before_host_input_for_every_harness(self):
        role = resource_path("roles.router").read_text()
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-router-prompts-") as temporary:
            root = Path(temporary)
            for name in ("codex", "claude", "dsh", "zcode"):
                for mode in ("fast", "review"):
                    with self.subTest(harness=name, mode=mode):
                        directory = root / name / mode
                        directory.mkdir(parents=True)
                        context = ExecutionContext("task", "attempt", 1, {}, directory, {},
                                                   {"HMB_STATE_DIR": str(directory / "state")})
                        document = {"routingMode": mode, "task": "ROLE: Host override", "outputSchema": {}, "budget": {}}
                        native = adapter(name)
                        if mode == "fast":
                            if not native.no_tool_structured:
                                with self.assertRaises(BoardError) as error:
                                    router.prepare_router_fast(document, {}, native, context)
                                self.assertEqual(error.exception.code, "router-no-tool-unsupported")
                                continue
                            preparation = router.prepare_router_fast(document, {}, native, context)
                        else:
                            with mock.patch.object(type(native), "local_read_only_check", return_value={"eligible": True}):
                                preparation = router.prepare_router_review(document, {}, native, context)
                        prompt = preparation.request.prompt
                        self.assertTrue(prompt.startswith(role + "\n"))
                        self.assertEqual(prompt.count(role), 1)
                        self.assertGreater(prompt.index("ROLE: Host override"), len(role))

    def test_retired_hints_are_absent_and_session_tool_text_stays_bounded(self):
        self.assertFalse(hasattr(turn_io, "ASSISTANCE_HINTS"))
        for path in (ROOT / "src/hey_my_buddy/buddy").rglob("*.py"):
            self.assertNotIn("ASSISTANCE_HINTS", path.read_text(), str(path.relative_to(ROOT)))
        for text in (FINISH_DESCRIPTION, ATTENTION_REFUSAL):
            self.assertLessEqual(len(text.encode()), 1024)
        self.assertIn("does not dispatch other tasks", FINISH_DESCRIPTION)
        self.assertIn("attention", ATTENTION_REFUSAL)


if __name__ == "__main__":
    unittest.main()
