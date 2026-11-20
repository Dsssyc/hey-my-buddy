"""Offline fixtures for the native snapshot query carriers."""
import hashlib
import json
from pathlib import Path

from hey_my_buddy.buddy.harnesses.snapshot_mount import prepare_snapshot_mount
from hey_my_buddy.private_files import publish_snapshot
from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence, read_tool_calls


def mount_snapshot(request):
    state = Path(request.private_state.state_dir)
    reference, _ = publish_snapshot(state, "routing", "fixture", {
        "task": "Frozen fixture input", "domainNodes": [], "buddies": [], "preferences": [],
    }, round=1)
    mount = prepare_snapshot_mount(
        invocation_root=Path(request.private_state.invocation_root), state_dir=state,
        identity=request.identity.to_payload(),
        input_sha256=hashlib.sha256(request.input_text.encode()).hexdigest(),
        snapshot={"namespace": "routing", "owner": "fixture", "round": 1, "reference": reference})
    return request.model_copy(update={"snapshot_query": mount})


def assert_snapshot_run(test, request, result, *, rounds):
    test.assertEqual(result.end.status, "ok", result.end.message)
    test.assertEqual(result.value.correction_count, rounds - 1)
    evidence = result.tool_evidence.value
    state = Path(request.private_state.state_dir)
    test.assertEqual(evidence["version"], 4)
    test.assertEqual(len(evidence["nativeIdentity"]), rounds)
    test.assertEqual(evidence["toolCalls"], rounds * 2)
    test.assertIsNone(judge_tool_evidence(evidence, request.permission, state_dir=state))
    calls = list(read_tool_calls(evidence, state_dir=state))
    test.assertTrue(all(call["sessionServiceVerified"] for call in calls))
    test.assertEqual({call["outcome"] for call in calls}, {"success", "failure"})
    coverage = next(ref for ref in result.evidence_refs if ref.kind == "snapshot-coverage")
    raw = Path(coverage.location).read_bytes()
    test.assertEqual(hashlib.sha256(raw).hexdigest(), coverage.sha256)
    test.assertEqual(len(json.loads(raw)), rounds)
