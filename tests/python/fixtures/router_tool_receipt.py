"""Bound native event receipts for private, deterministic Router fixtures."""
from buddy.tool_evidence import ToolEventEvidence, normalize_tool_event


def tool_receipt(binding, tool_calls=0, *, native_identity=None, stream_complete=True, tool_name=None):
    identity = native_identity or {"sessionId": "private-fixture-session"}
    names = {"dsh": "read", "codex": "commandExecution", "claude": "Read", "zcode": "Read"}
    collector = ToolEventEvidence(binding)
    for number in range(tool_calls):
        for phase in ("start", "end"):
            collector.observe(normalize_tool_event(binding["adapter"], {
                "nativeIdentity": identity, "callId": f"fixture-call-{number}",
                "toolName": tool_name or names[binding["adapter"]], "phase": phase,
            }))
    return {"nativeIdentity": identity, "toolEvidence": collector.finish([identity], stream_complete)}


def claim_tool_receipt(claim, tool_calls=0, **options):
    return tool_receipt({"adapter": claim["decisionInput"]["profile"]["adapter"],
                         "taskId": claim["task"]["taskId"],
                         "attemptId": claim["attempt"]["attemptId"],
                         "generation": claim["attempt"]["generation"]}, tool_calls, **options)
