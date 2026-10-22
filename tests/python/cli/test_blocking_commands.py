"""Await's suggested Host commands pass the real workflow boundary.

Private in-process boards and synthetic turn receipts exercise validation and
transactions without starting a harness or making a model call.
"""
import json
import shlex

from blackboard.tasks import test_workflow as workflow_tests
from blackboard.tasks.test_workflow import WorkflowTestCase
from cli.test_blocking import VirtualClock
from support import InProcessBoard

from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.protocol.transport import METHOD_MAP


class BoundaryCommandTests(WorkflowTestCase):
    def test_every_host_boundary_command_passes_workflow_validation(self):
        for boundary, command_count in (
            ("assistance", 3),
            ("attention", 3),
            ("helper-attention", 3),
            ("continuation", 1),
        ):
            for command_index in range(command_count):
                with self.subTest(boundary=boundary, command=command_index):
                    # Each alternative gets the same fresh boundary: executing
                    # one suggested command must not stale the next one's revision.
                    case = f"{boundary}-{command_index}"
                    board = InProcessBoard(self.workdir(case))
                    self._stack.append(board)
                    if boundary == "helper-attention":
                        submitted, _, _ = workflow_tests.HelperAttentionResolutionTests._attention_with_helper(
                            self, board, request_id=case,
                        )
                    else:
                        submitted = self.submit(board, request_id=case, cwd=str(self.workdir(f"{case}-source")))
                        self.finish_turn(board, self.claim(board), disposition=(
                            "attention" if boundary == "attention" else "assistance"
                        ))
                        view = board.call("workflow_get", {"runId": submitted["runId"]})
                        if boundary == "continuation":
                            self.decide(board, view, view["activeRequest"]["requestId"],
                                        decision="decline", autoContinue=False)

                    def service(method, params, state_dir):
                        self.assertEqual(state_dir, board.directory)
                        self.assertIn(method, ("status", "result"))
                        response = board.call(METHOD_MAP[method][1], params)
                        return response["task"] if method == "status" else response

                    envelope = await_run({"runId": submitted["runId"]}, state_dir=board.directory,
                                         service=service, clock=VirtualClock())
                    self.assertEqual(envelope["outcome"], "waiting-host")
                    self.assertEqual(len(envelope["nextCommands"]), command_count)
                    if boundary == "continuation":
                        self.assertIsNone(envelope["request"])
                    else:
                        self.assertEqual(envelope["request"]["kind"], boundary)
                    executable, method, payload = shlex.split(envelope["nextCommands"][command_index])
                    self.assertEqual(executable, "hey-my-buddy")
                    params = json.loads(payload)
                    self.assertEqual(params.pop("controlFile"), "<saved-controlFile>")
                    params.update(self.control(submitted))
                    # Fill only values offered as placeholders; never add a
                    # missing parameter that the generated command needs.
                    for key in ("input", "note"):
                        if params.get(key) == "...":
                            params[key] = "Host reviewed this boundary and authorized the next step."
                    response = board.call(METHOD_MAP[method][1], params)
                    self.assertEqual(response["runId"], submitted["runId"])
