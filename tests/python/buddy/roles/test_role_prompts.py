"""ADR-030 role resources and the actual Worker/Router request boundaries."""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import adapter
from hey_my_buddy.buddy.harnesses.run_contract import SessionService
from hey_my_buddy.buddy.roles import router, run_execution, turn_io
from hey_my_buddy.buddy.roles.worker_services import ATTENTION_REFUSAL, FINISH_DESCRIPTION
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install.runtime import resource_path

ROOT = Path(__file__).resolve().parents[4]


class RolePromptTests(unittest.TestCase):
    def test_resources_preserve_the_approved_text_except_the_worker_additions(self):
        approved = re.findall(r"```text\n(.*?)```", (ROOT / "docs/design/role-texts.md").read_text(), re.S)
        for name, original in zip(("worker", "router", "lead"), approved):
            with self.subTest(role=name):
                published = resource_path("roles." + name).read_text()
                if name == "worker":
                    lines = published.splitlines(keepends=True)
                    additions = [line for line in lines if line not in original.splitlines(keepends=True)]
                    self.assertEqual(len(additions), 2)
                    self.assertEqual("".join(line for line in lines if line not in additions), original)
                    for phrase in ("outside the authorized scope", "no further evidence", "capability clearly does not fit",
                                   "review condition", "attempted", "neededWork", "acceptance", "expectedArtifacts",
                                   "suggestedProfileId", "Never claim that a reviewer or helper already ran"):
                        self.assertIn(phrase, "".join(additions))
                else:
                    self.assertEqual(published, original)

    def test_all_four_worker_requests_start_with_the_same_fixed_role(self):
        role = resource_path("roles.worker").read_text()
        with tempfile.TemporaryDirectory(prefix="buddy-role-prompts-") as temporary:
            root = Path(temporary)
            task = "ROLE: Host override from task"
            turn = {"taskId": "task", "attemptId": "attempt", "generation": 1, "turnId": "turn",
                    "resumeMode": "initial", "ROLE": "Host override from input"}
            (root / "task.txt").write_text(task)
            turn_io.private_json(root / "input.json", turn)
            binding = SimpleNamespace(completion_tool="buddy_finish_turn", checkpoint_tool="buddy_checkpoint",
                                      answer_tool="buddy_answer_inquiry", services={},
                                      description=SessionService(tool_names=("buddy_finish_turn", "buddy_checkpoint", "buddy_answer_inquiry")))
            module = SimpleNamespace(prepare_services=mock.Mock(return_value=binding))
            for name in ("codex", "claude", "dsh", "zcode"):
                with self.subTest(harness=name), mock.patch.object(run_execution, "_native_services", return_value={}):
                    control = {"harness": name, "privateRoot": str(root), "nativeRoot": str(root / "native"),
                               "directory": str(root), "cwd": str(root), "invocationId": "invocation",
                               "inputFile": str(root / "input.json"), "taskFile": str(root / "task.txt"),
                               "spec": {"provider": "fixture", "model": "fixture", "effort": "low"},
                               "timeoutSeconds": 1}
                    request, _, _, error = run_execution.worker_request(control, module)
                    self.assertIsNone(error)
                    self.assertTrue(request.input_text.startswith(role + "\n"))
                    self.assertEqual(request.input_text.count(role), 1)
                    self.assertGreater(request.input_text.index(task), len(role))
                    self.assertTrue(request.input_text.endswith(turn_io.canonical_json(turn)))

    def test_fast_and_review_router_requests_keep_the_role_before_host_input_for_every_harness(self):
        role = resource_path("roles.router").read_text()
        with tempfile.TemporaryDirectory(prefix="buddy-router-prompts-") as temporary:
            root = Path(temporary)
            for name in ("codex", "claude", "dsh", "zcode"):
                for mode in ("fast", "review"):
                    with self.subTest(harness=name, mode=mode):
                        directory = root / name / mode
                        directory.mkdir(parents=True)
                        context = ExecutionContext("task", "attempt", 1, {}, directory, {},
                                                   {"BUDDY_STATE_DIR": str(directory / "state")})
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
