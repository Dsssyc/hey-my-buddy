"""Synthetic governed-native turn facts for the blackboard's mechanical-rules tests.

The tests served here admit real governed runs through the real worker API but
have no model process, so they synthesize the native turn record a governed
harness would have imported. The provenance below matches the current adapter
validators field for field, so a record from this module passes the same
production provenance check a real imported turn passes.

That is also the boundary of this fixture: nothing here is real native
verification. No harness ran, no native carrier minted a receipt, and the
ordinals and identifiers are fixture constants, not observed evidence. Tests
that need a genuinely minted receipt run this project's own fake agent through
the shared role controller instead (see the dsh harness fixtures).
"""
from __future__ import annotations

import hashlib

from hey_my_buddy.json_codec import canonical_json

FINISH_TOOL = "buddy_finish_turn"

#: A 32-hex-character fixture receipt identity; the dsh validator only checks
#: the shape, and no fixture receipt is ever a real carrier receipt.
FIXTURE_RECEIPT_ID = "f" * 32


def dsh_provenance(session_id: str, *, receipt_id: str = FIXTURE_RECEIPT_ID) -> dict:
    """Synthetic finish-tool provenance in the current dsh format.

    The ordinals satisfy the validator's order (call, then result, settlement
    and the acknowledged session close) and the root session binds to
    ``session_id``, so a reconstructed continuation must pass a session that
    differs from its previous one exactly like a rebuilt native session.
    """
    return {
        "version": 1, "adapter": "dsh", "tool": FINISH_TOOL, "turnEnd": "completed",
        "stopReason": "end_turn", "rootSessionMatched": True, "receiptVerified": True,
        "sessionClose": "acknowledged",
        "nativeSessionId": session_id, "toolCallId": "fixture-finish-1", "receiptId": receipt_id,
        "callOrdinal": 1, "resultOrdinal": 2, "settledOrdinal": 2, "sessionCloseOrdinal": 3,
    }


def zcode_provenance(identity: dict, session_id: str) -> dict:
    """Synthetic finish-tool provenance in the current zcode format.

    ``identity`` is the attempt's ``{taskId, attemptId, generation, turnId}``;
    the native input binding is derived exactly the way the validator does.
    """
    return {
        "adapter": "zcode", "tool": FINISH_TOOL, "turnEnd": "completed",
        "rootSessionMatched": True, "receiptVerified": True, "toolResultSuccess": True,
        "toolResultTruncated": False, "turnResultType": "success", "settlement": "session-closed",
        "nativeSessionId": session_id,
        "inputId": "buddy-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest(),
        "nativeTurnId": "fixture-native-turn-1", "toolCallId": "fixture-finish-1",
        "receiptId": FIXTURE_RECEIPT_ID,
        "turnStartSeq": 1, "toolCallSeq": 2, "toolResultSeq": 3, "turnEndSeq": 4,
        "turnCompletedOrdinal": 1, "promptCompletedOrdinal": 2, "sessionCloseOrdinal": 3,
    }


def session_id_for(turn: dict, *, fallback: str = "sess-fixture") -> str:
    """A fresh fixture session id for one turn, honoring native-session resume.

    A native-session continuation must retain the exact previous session, so
    that resume mode reuses the previous identity; every other turn gets its
    own id derived from the service-owned turn id.
    """
    turn_input = turn.get("input") if isinstance(turn, dict) else None
    turn_input = turn_input if isinstance(turn_input, dict) else {}
    previous = turn_input.get("previousSessionId")
    if turn_input.get("resumeMode") == "native-session" and isinstance(previous, str) and previous:
        return previous
    turn_id = turn.get("turnId")
    return f"sess-{turn_id[:8]}" if isinstance(turn_id, str) and turn_id else fallback


def provenance_for(adapter: str, turn: dict, session_id: str) -> dict:
    """The current synthetic finish-tool provenance of one adapter's turn."""
    turn_input = turn.get("input") if isinstance(turn, dict) else None
    turn_input = turn_input if isinstance(turn_input, dict) else {}
    if adapter == "zcode":
        identity = {key: turn_input.get(key) for key in ("taskId", "attemptId", "generation", "turnId")}
        return zcode_provenance(identity, session_id)
    return dsh_provenance(session_id)


def governed_record(*, adapter: str, task_id: str, attempt_id: str, generation: int,
                    turn_id: str, resume_mode: str, previous_session_id, input_sha256: str,
                    outcome: dict, session_id: str | None = None,
                    prompt_sha256: str = "a" * 64) -> dict:
    """One governed turn record carrying current synthetic native provenance."""
    turn = {"turnId": turn_id, "input": {"taskId": task_id, "attemptId": attempt_id,
                                         "generation": generation, "turnId": turn_id,
                                         "resumeMode": resume_mode,
                                         "previousSessionId": previous_session_id}}
    session = session_id or session_id_for(turn)
    return {
        "version": 1, "taskId": task_id, "attemptId": attempt_id, "generation": generation,
        "turnId": turn_id, "resumeMode": resume_mode, "previousSessionId": previous_session_id,
        "sessionId": session, "promptSha256": prompt_sha256, "inputSha256": input_sha256,
        "outcome": outcome,
        "provenance": provenance_for(adapter, turn, session),
    }


def claim_record(claim: dict, *, outcome: dict, session_id: str | None = None,
                 prompt_sha256: str = "a" * 64) -> dict:
    """One governed turn record for a ``worker_claim`` response's attempt."""
    attempt, turn = claim["attempt"], claim["turn"]
    turn_input = turn.get("input") or {}
    return governed_record(
        adapter=attempt["adapter"],
        task_id=attempt["taskId"], attempt_id=attempt["attemptId"],
        generation=attempt["generation"], turn_id=turn["turnId"],
        resume_mode=turn["resumeMode"],
        previous_session_id=turn_input.get("previousSessionId"),
        input_sha256=turn["inputSha256"], outcome=outcome,
        session_id=session_id, prompt_sha256=prompt_sha256,
    )
