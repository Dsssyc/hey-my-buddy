"""Exercise signed query delivery and native evidence on a private board."""
from pathlib import Path

from hey_my_buddy.buddy.harnesses.snapshot_mount import (
    mounted_snapshot, prepare_snapshot_mount, snapshot_coverage_refs, stdio_tool_names,
)
from hey_my_buddy.json_codec import decode_strict_json
from hey_my_buddy.private_dirs import attempt_root
from hey_my_buddy.protocol.tool_evidence import ToolEventEvidence, normalize_tool_event


def read_sections(claim, state, queries, *, mount=None, forbidden=False, stream_complete=True, after_query=None):
    task_id = claim["task"]["taskId"]
    attempt = claim["attempt"]
    document = claim["decisionInput"]
    binding = {"harness": "dsh", "taskId": task_id, "attemptId": attempt["attemptId"],
               "generation": attempt["generation"]}
    native = {"sessionId": "fake-maintenance-native"}
    if mount is None:
        root = attempt_root(state, "dsh", task_id, attempt["attemptId"]) / ("no-tool-" + "0" * 32)
        mount = prepare_snapshot_mount(invocation_root=root, state_dir=state, identity=binding,
                                       input_sha256="a" * 64, snapshot=document["snapshot"])
    else:
        root = Path(mount.config_path).parent
    service = mounted_snapshot(mount)
    tool = next(name for name in stdio_tool_names(mount) if name.endswith("__query_micro_tasks"))
    recorder = ToolEventEvidence(binding, state_dir=state, session_service_tools=stdio_tool_names(mount))
    calls = []
    for index, (arguments, complete) in enumerate(queries):
        while True:
            call_id = str(len(calls))
            result = service.mcp_call("query_micro_tasks", arguments)
            calls.append({"nativeIdentity": native, "callId": call_id, "toolName": tool,
                          "arguments": arguments, "result": result, "started": True,
                          "completed": True, "ok": not result["isError"]})
            for phase in ("start", "end"):
                recorder.observe(normalize_tool_event("dsh", {
                    "nativeIdentity": native, "callId": call_id, "toolName": tool, "phase": phase,
                    "outcome": "success" if phase == "end" else "unknown",
                }))
            data = decode_strict_json(result["content"][0]["text"])["response"]["data"]
            if not complete or data["nextCursor"] is None:
                break
            arguments = {**arguments, "cursor": data["nextCursor"]}
        if after_query is not None:
            after_query(index, len(calls))
    if forbidden:
        for phase in ("start", "end"):
            recorder.observe(normalize_tool_event("dsh", {
                "nativeIdentity": native, "callId": "foreign", "toolName": "read", "phase": phase,
                "outcome": "unknown",
            }))
    keys, coverage = service.verify_calls(calls, [native])
    evidence = recorder.finish([native], stream_complete, verified_session_calls=keys)
    return native, evidence, snapshot_coverage_refs(root, coverage)
