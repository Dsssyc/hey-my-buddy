"""Await's suggested Host commands pass the real workflow boundary.

Private in-process boards and synthetic turn receipts exercise validation and
transactions without starting a harness or making a model call.
"""
import json
import shlex

from blackboard.tasks import test_workflow as workflow_tests
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from cli.test_blocking import VirtualClock
from support import InProcessBoard, enable_fixture_buddy

from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.protocol.transport import METHOD_MAP


class BoundaryCommandTests(WorkflowTestCase):
    def test_every_host_boundary_command_passes_workflow_validation(self):
        for boundary, command_count in (
            ("assistance", 3),
            ("attention", 3),
            ("helper-attention", 3),
            ("routing", 2),
            ("helper-routing", 2),
            ("continuation", 1),
        ):
            for command_index in range(command_count):
                with self.subTest(boundary=boundary, command=command_index):
                    # Each alternative gets the same fresh boundary: executing
                    # one suggested command must not stale the next one's revision.
                    case = f"{boundary}-{command_index}"
                    board = InProcessBoard(self.workdir(case))
                    self._stack.append(board)
                    if boundary == "routing":
                        submitted = board.call("workflow_submit", {
                            "requestId": case, "hostId": "host-1", "task": "Do the work",
                            "cwd": str(self.workdir(f"{case}-source")),
                            "executionWorkspace": {"kind": "existing", "access": "write"},
                        })
                        self.controls[submitted["runId"]] = submitted["control"]
                    elif boundary == "helper-attention":
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
                        elif boundary == "helper-routing":
                            board.call("workflow_decide", {
                                "runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
                                "commandId": f"{case}-helper", "expectedRevision": view["revision"],
                                "decision": "approve", **self.control(view),
                                "helpers": [{
                                    "requestId": f"{case}-helper", "task": "Help with the work",
                                    "cwd": str(self.workdir(f"{case}-helper")),
                                    "executionWorkspace": {"kind": "existing", "access": "write"},
                                }],
                            })

                    def service(method, params, state_dir):
                        self.assertEqual(state_dir, board.directory)
                        self.assertIn(method, ("status", "result"))
                        response = board.call(METHOD_MAP[method][1], params)
                        return response["task"] if method == "status" else response

                    envelope = await_run({"runId": submitted["runId"]}, state_dir=board.directory,
                                         service=service, clock=VirtualClock())
                    self.assertEqual(envelope["outcome"], "waiting-host")
                    self.assertEqual(len(envelope["nextCommands"]), command_count)
                    if boundary in ("routing", "helper-routing"):
                        self.assertTrue(envelope["request"]["routing"])
                    elif boundary == "continuation":
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
                    if "buddy" in params:
                        self.assertEqual(params["buddy"], {key: f"<{key}>" for key in BUDDY})
                        params["buddy"] = dict(BUDDY)
                        enable_fixture_buddy(board.store, BUDDY)
                    response = board.call(METHOD_MAP[method][1], params)
                    self.assertEqual(response["runId"], submitted["runId"])
