"""The Worker role's session tools: micro task prompt, finish contract, inquiries.

These are the harness-neutral rules of one micro task Worker root turn
(ADR-025 decision 7): the micro task prompt's full text and assembly, the
six-field finish contract, the fixed Worker role text, and the completion
rules that refuse a ``completed`` outcome while a native interactive request is
outstanding or a Host inquiry is still unanswered. Nothing here knows a
harness: the native, fully qualified tool names arrive as arguments, no branch
reads a harness or mode name, and no harness package is imported — a native
carrier (for ZCode the session-private MCP bridge) frames these rules on its
own transport and hands every tool call to :func:`call_session_tool`.

The rules sign what they mint through the shared
:mod:`hey_my_buddy.buddy.harnesses.session_receipts` primitives, so the native
driver keeps verifying the signature, the attempt binding and the root-turn
evidence independently of the role. The service holds no board database or
client: it sees only this attempt's bridge configuration — identity, turn-input
hash, signing key and the private journal/attention paths — which the owning
controller publishes.

Every expected refusal — invalid tool arguments, an outstanding native
attention request, a pending inquiry, an unknown or terminal inquiry — is
returned as an ``isError`` result whose text is a signed, attempt/input/tool-
bound tool-refusal envelope: the native wrapper may surface such an error as a
*successful* tool result, and the controller recovers the turn for a corrected
same-turn retry only after the envelope's signature, identity and tool binding
verify. A success receipt, the native root completion, the session close and
the real shutdown evidence remain separate facts; a refusal never carries an
outcome.
"""
from __future__ import annotations

import json
import secrets
from copy import deepcopy
from pathlib import Path

from ...private_files import read_text
from ...errors import BoardError
from ...protocol.inquiry_journal import read_inquiry_journal, merged_inquiry_records
from .turn_io import canonical_json, validate_outcome
from .prompts import worker_prompt
from ..harnesses.session_receipts import (
    MAX_ANSWER_BYTES,
    MAX_INQUIRIES,
    MAX_INQUIRY_ID_BYTES,
    MAX_INQUIRY_RECEIPT_BYTES,
    MAX_QUESTION_BYTES,
    MAX_TOOL_REFUSAL_BYTES,
    MAX_TOOL_REFUSAL_DETAIL_BYTES,
    MAX_TOOL_REFUSAL_PREFIX_BYTES,
    serialized_footprint,
    sign_receipt,
)

MAX_PENDING_IN_FINISH_REFUSAL = 4

STRINGS = {"type": "array", "maxItems": 32, "items": {"type": "string", "maxLength": 4096}}
OUTCOME_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["disposition", "summary", "remaining", "decisions", "artifacts", "request"],
    "properties": {
        "disposition": {"type": "string", "enum": ["completed", "assistance", "attention"]},
        "summary": {"type": "string", "minLength": 1, "maxLength": 65536,
                    "description": "The entire serialized outcome, including this report, must fit in 64 KiB of UTF-8."},
        "remaining": STRINGS, "decisions": STRINGS,
        "artifacts": {"type": "array", "maxItems": 32, "items": {"anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": True}]}},
        "request": {"description": "Required for every outcome. Use null when disposition is completed; otherwise provide the complete assistance or attention request.", "anyOf": [
            {"type": "null"},
            {"type": "object", "additionalProperties": False,
             "required": ["summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"],
             "properties": {"summary": {"type": "string"}, "attempted": {"type": "string"},
                            "neededWork": {"type": "string"}, "expectedArtifacts": STRINGS,
                            "acceptance": {"type": "string"},
                            "suggestedBuddyId": {"description": "Optional buddy suggestion the Host may ignore; an explicit null means no suggestion.",
                                                   "type": ["string", "null"], "maxLength": 256}}},
        ]},
    },
}


def outcome_schema_for(*, delegation_authority: bool = False) -> dict:
    if not delegation_authority:
        return OUTCOME_SCHEMA
    schema = deepcopy(OUTCOME_SCHEMA)
    schema["properties"]["disposition"]["enum"].append("waiting")
    schema["properties"]["request"]["description"] = (
        "Required for every outcome. Use null when disposition is completed or waiting; "
        "otherwise provide the complete assistance or attention request.")
    return schema


ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["inquiryId", "answer"],
    "properties": {
        "inquiryId": {"type": "string", "minLength": 1, "maxLength": 128,
                      "description": "The inquiryId exactly as delivered in this turn."},
        "answer": {"type": "string", "minLength": 1, "maxLength": 10000,
                   "description": "The bounded answer text for that inquiry; at most 4000 UTF-8 bytes are accepted."},
    },
}
CHECKPOINT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
FINISH_DESCRIPTION = (
    "Conclude the owning Buddy root turn with completed, assistance or attention. Include all six fields: "
    "disposition, summary, remaining, decisions, artifacts, request. For completed, request must be null. "
    "Correct validation errors and retry; stop after one successful receipt, once your work and internal "
    "subagents have settled. A completed outcome is refused while a Host inquiry is still unanswered. "
    "A refused call returns a signed JSON refusal envelope whose detail names the correction; read it, fix "
    "the call and retry in this same turn. This tool does not dispatch other tasks."
)
CHECKPOINT_DESCRIPTION = (
    "Pick up queued Host inquiries for this micro task turn. Returns a signed receipt listing the questions "
    "still awaiting an answer (nothing pending returns an empty list); when the serialized receipt budget "
    "cannot carry every pending question, morePending names how many stayed queued — answer the listed ones "
    "and checkpoint again to pick up the rest. Call it at natural work milestones and again just before "
    "hey_my_buddy_finish_turn; it is never required on a timer. Answer each listed question with "
    "hey_my_buddy_answer_inquiry using its exact inquiryId."
)
ANSWER_DESCRIPTION = (
    "Answer one Host inquiry delivered in this turn or exposed by hey_my_buddy_checkpoint, using its exact inquiryId and a bounded answer. "
    "Returns a signed tentative receipt; the recorded answer only becomes authoritative after the owning "
    "controller verifies it against this root turn's own tool evidence. A first accepted answer for an "
    "inquiry cannot be replaced."
)
ATTENTION_REFUSAL = (
    "This run refused a native interactive request because no Host approval channel is attached. Conclude the "
    "turn with disposition attention (or assistance) and describe the refused request in the outcome request; "
    "a completed outcome is not accepted while that request is unresolved."
)
INQUIRY_CHANNEL_ABSENT = "this turn has no mounted inquiry channel"


def micro_task_prompt(task_text: str, turn_input: dict, finish_tool: str, *,
                    checkpoint_tool: str | None = None, answer_tool: str | None = None,
                    credential_file: str | None = None) -> str:
    """Bounded micro task root prompt: scope, inquiry channel, finish contract."""
    inquiry = (
        f"Host inquiries arrive cooperatively: call {checkpoint_tool} at natural work milestones and again just "
        f"before finishing to pick up any queued Host questions (an empty list means none). Answer each listed "
        f"question with {answer_tool} using its exact inquiryId. A completed finish is refused while a question "
        f"is still unanswered; a question the journal shows answered, discarded or otherwise no longer answerable "
        f"does not block it. Checkpointing is voluntary and never on a timer, and no input is ever injected into "
        "your turn."
    ) if checkpoint_tool and answer_tool else ""
    return worker_prompt(task_text, turn_input, credential_file=credential_file, delivery=(
        "This is a Buddy root turn for a micro task. Complete the authorized task using the available coding tools and internal subagents. Follow the frozen Host input and its allocated workspace. Allocated workspaces share Git stash, branches and tags with the owner's repository: do not use git stash or create, switch, move or delete branches or tags; leave changes in the workspace or commit on the current detached HEAD.",
        f"Only the root may conclude this Buddy turn. After your work and internal subagents settle, obtain one successful receipt from {finish_tool} with the complete structured outcome. Include all six fields: disposition, summary, remaining, decisions, artifacts, request. Completed requires request: null. Use assistance for bounded help or attention for a Host decision. If a native permission or user-input request was refused, you must conclude with attention instead of completed. If a session tool refuses your call — including with a signed JSON refusal envelope naming the correction — correct the arguments and retry in this same turn. Plain final text is not a recorded outcome. After a successful finish receipt, do not start more tools; end the native turn.",
        *(("When you have handed off work to micro tasks you own and need their results, conclude this Lead turn with disposition waiting, request: null and a clear summary. The blackboard will start a later turn when an owned task needs your attention.",)
          if turn_input.get("context", {}).get("delegationAuthority") is True else ()),
        inquiry,
    ))


def attention_requests(configuration: dict) -> int:
    """Count refused native requests; only an absent file means none."""
    try:
        value = json.loads(read_text(Path(configuration["attentionPath"])))
    except FileNotFoundError:
        return 0
    return len(value["requests"])


def read_inquiry_entries(configuration: dict) -> dict[str, dict] | None:
    """The shared reader's merged facts for this attempt's session tools."""
    journal = configuration.get("inquiryJournalPath")
    identity = configuration.get("identity")
    if not isinstance(journal, str) or not journal or not isinstance(identity, dict):
        return None
    return merged_inquiry_records(read_inquiry_journal(identity, journal)["records"])


def pending_inquiries(configuration: dict) -> list[dict] | None:
    """The still-answerable Host questions for this turn, in journal order."""
    entries = read_inquiry_entries(configuration)
    if entries is None:
        return None
    pending = []
    for entry in entries.values():
        if entry.get("state") not in ("queued", "delivered") or not isinstance(entry.get("question"), str) or not entry["question"]:
            continue
        item = {"inquiryId": entry["inquiryId"], "question": entry["question"],
                "questionSha256": entry.get("questionSha256"), "state": entry.get("state"),
                "askedAt": entry.get("askedAt")}
        if isinstance(entry.get("deliveredAt"), str):
            item["deliveredAt"] = entry["deliveredAt"]
        pending.append(item)
    return pending[:MAX_INQUIRIES]


def _footprint_bounded(text: str, budget: int) -> str:
    """Longest prefix of ``text`` whose serialized footprint fits ``budget``.

    A prefix's footprint is monotone in its length, so a binary search keeps
    the largest fitting prefix even when control characters, quotes and
    backslashes expand under JSON escaping.
    """
    if serialized_footprint(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if serialized_footprint(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def inquiry_refusal(pending: list[dict]) -> str | None:
    """The bounded finish refusal naming the pending questions.

    Each listed question is shown within its serialized question budget so a
    pathological journal entry can never crowd out the correction guidance:
    the checkpoint/answer instructions at the tail always survive, and the
    final envelope budgeting in :func:`_refusal` remains the hard guarantee.
    """
    if not pending:
        return None
    listed = [
        f"[{item['inquiryId']}] {_footprint_bounded(item['question'], MAX_QUESTION_BYTES)}"
        for item in pending[:MAX_PENDING_IN_FINISH_REFUSAL] if isinstance(item.get("question"), str)
    ]
    more = f" (+{len(pending) - len(listed)} more)" if len(pending) > len(listed) else ""
    return (
        "This turn cannot be completed while Host inquiries are still unanswered: "
        + " | ".join(listed)
        + f"{more}. Call hey_my_buddy_checkpoint to pick them up, answer each with hey_my_buddy_answer_inquiry, then retry "
          "the finish; a question the journal shows answered, discarded or otherwise no longer answerable no "
          "longer blocks completion."
    )


def _signed(payload: dict, configuration: dict) -> str:
    receipt = {**payload, "receiptId": secrets.token_hex(16)}
    receipt["signature"] = sign_receipt(receipt, configuration["key"])
    return canonical_json(receipt)


def _refusal(configuration: dict, tool: str, reason: str, detail: str) -> dict:
    """One signed, attempt/input/tool-bound refusal envelope as an MCP error.

    The native wrapper can surface an ``isError`` response as a *successful*
    tool result whose content is exactly this text, so the refusal must be
    verifiable by the controller on that path too: the signature binds the
    attempt identity, the turn input and the session tool, and the detail is
    the same bounded correction text the plain error carried. A verified
    refusal tells the root to correct the call and retry in this same turn; it
    never carries an outcome and never mutates state.

    The detail is fitted to the envelope's verified wire budget by measuring
    the exact serialized envelope — fixed-length receipt and signature members
    included — plus the wrapper-header prefix the controller tolerates. Raw
    UTF-8 length alone cannot bound canonical JSON escaping (a control
    character costs six bytes, a quote or backslash two), so every minted
    envelope passes the native verifier byte for byte. A prefix's fitness is
    monotone in its length, so a binary search keeps the largest fitting
    prefix — the leading correction guidance survives intact and only a
    pathological input reduced to blanks falls back to the fixed retry
    instruction.
    """

    def fits(value: str) -> bool:
        probe = {"version": 1, "kind": "tool-refusal", "identity": configuration["identity"],
                 "inputSha256": configuration["inputSha256"], "tool": tool, "reason": reason,
                 "detail": value, "receiptId": "0" * 32, "signature": "0" * 64}
        return (len(canonical_json(probe).encode()) + MAX_TOOL_REFUSAL_PREFIX_BYTES <= MAX_TOOL_REFUSAL_BYTES
                and len(value.encode()) <= MAX_TOOL_REFUSAL_DETAIL_BYTES)

    text = detail
    if not fits(text):
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if fits(text[:middle]):
                low = middle
            else:
                high = middle - 1
        text = text[:low]
    if not text.strip():
        text = f"the {tool} call was refused ({reason}); correct it and retry in this turn"
    return {"isError": True, "content": [{"type": "text", "text": _signed(
        {"version": 1, "kind": "tool-refusal", "identity": configuration["identity"],
         "inputSha256": configuration["inputSha256"], "tool": tool, "reason": reason,
         "detail": text}, configuration)}]}


def _checkpoint_batch(pending: list[dict]) -> tuple[list[dict], int]:
    """Fit the pending questions into one verifiable checkpoint receipt.

    The receipt budget bounds the serialized wire, and JSON escaping can
    inflate an accepted 4000-byte question far beyond its raw length, so the
    batch is the longest prefix whose serialized entries fit the budget with
    the envelope framing reserved. Questions beyond the batch are not lost:
    they stay queued in the journal, block a completed finish by name, and are
    delivered on a later checkpoint once earlier ones are answered. The
    returned ``morePending`` count makes the batching explicit inside the
    signed receipt itself.
    """
    budget = MAX_INQUIRY_RECEIPT_BYTES - 1024  # fixed members: identity, ids, signature
    batch: list[dict] = []
    for item in pending:
        if len(canonical_json(batch + [item]).encode()) > budget:
            break
        batch.append(item)
    return batch, len(pending) - len(batch)


def _finish_receipt(arguments: dict, configuration: dict) -> dict:
    # Each call has its own receipt: a child calling this tool must not
    # prevent the owning root from later issuing its independent receipt.
    receipt = {"version": 1, "identity": configuration["identity"],
               "inputSha256": configuration["inputSha256"], "outcome": arguments,
               "receiptId": secrets.token_hex(16)}
    receipt["signature"] = sign_receipt(receipt, configuration["key"])
    return {"content": [{"type": "text", "text": canonical_json(receipt)}]}


def session_tools(*, delegation_authority: bool = False) -> list[dict]:
    """The three session tools this role exposes inside one native root turn."""
    return [
        {"name": "hey_my_buddy_checkpoint", "inputSchema": CHECKPOINT_SCHEMA, "description": CHECKPOINT_DESCRIPTION},
        {"name": "hey_my_buddy_answer_inquiry", "inputSchema": ANSWER_SCHEMA, "description": ANSWER_DESCRIPTION},
        {"name": "hey_my_buddy_finish_turn", "inputSchema": outcome_schema_for(
            delegation_authority=delegation_authority), "description": FINISH_DESCRIPTION + (
                " A Lead may use waiting with request: null after handing off owned work; the board will open a later turn."
                if delegation_authority else "")},
    ]


def call_session_tool(name: object, arguments: object, configuration: dict) -> dict | None:
    """Apply one session tool call's rules; ``None`` names no tool of this role.

    The finish boundary order is part of the contract: an invalid outcome is
    corrected before an outstanding attention request is named, and a pending
    inquiry blocks only a ``completed`` disposition, so the root always gets
    the most actionable refusal first and may correct, checkpoint, answer and
    retry inside the same native turn.
    """
    if name == "hey_my_buddy_checkpoint":
        pending = pending_inquiries(configuration)
        if pending is None:
            return _refusal(configuration, "hey_my_buddy_checkpoint", "inquiry-channel-absent", INQUIRY_CHANNEL_ABSENT)
        batch, more = _checkpoint_batch(pending)
        receipt = _signed({"version": 1, "kind": "inquiry-checkpoint", "identity": configuration["identity"],
                           "inquiries": batch, "morePending": more}, configuration)
        return {"content": [{"type": "text", "text": receipt}]}
    if name == "hey_my_buddy_answer_inquiry":
        return _answer_inquiry(arguments, configuration)
    if name == "hey_my_buddy_finish_turn":
        error = validate_outcome(arguments, delegation_authority=configuration.get("delegationAuthority") is True)
        if error:
            return _refusal(configuration, "hey_my_buddy_finish_turn", "invalid-arguments", error)
        if arguments["disposition"] in ("completed", "waiting") and attention_requests(configuration):
            # A refused native approval must reach the Host as attention, never
            # as silently completed work. The root is still live and can issue
            # the attention receipt in this same turn.
            return _refusal(configuration, "hey_my_buddy_finish_turn", "attention-outstanding", ATTENTION_REFUSAL)
        if arguments["disposition"] in ("completed", "waiting"):
            refusal = inquiry_refusal(pending_inquiries(configuration) or [])
            if refusal:
                # Same contract as a refused native approval: the root is still
                # live, so it can checkpoint, answer and retry in this turn.
                return _refusal(configuration, "hey_my_buddy_finish_turn", "inquiry-pending", refusal)
            return _finish_receipt(arguments, configuration)
        return _finish_receipt(arguments, configuration)
    return None


def _answer_inquiry(arguments: object, configuration: dict) -> dict:
    """Validate one answer against the journal view and return a signed receipt.

    This only mints the tentative receipt: the controller verifies the native
    root tool evidence and the receipt binding before the journal records the
    answer, so this handler never writes inquiry state itself. Every expected
    refusal is a signed tool-refusal envelope, so a wrapper-successful result
    still recovers as a correctable refusal controller-side.
    """
    def failure(reason: str, text: str) -> dict:
        return _refusal(configuration, "hey_my_buddy_answer_inquiry", reason, text)
    if not isinstance(arguments, dict) or set(arguments) != {"inquiryId", "answer"}:
        return failure("invalid-arguments", "hey_my_buddy_answer_inquiry requires exactly inquiryId and answer")
    inquiry_id, answer = arguments["inquiryId"], arguments["answer"]
    if not isinstance(inquiry_id, str) or not inquiry_id or len(inquiry_id.encode()) > MAX_INQUIRY_ID_BYTES:
        return failure("invalid-arguments", "hey_my_buddy_answer_inquiry requires a bounded nonempty inquiryId")
    if not isinstance(answer, str) or not answer.strip():
        return failure("invalid-arguments", "hey_my_buddy_answer_inquiry requires a nonblank answer")
    if len(answer.encode()) > MAX_ANSWER_BYTES:
        return failure("invalid-arguments", f"the answer exceeds its {MAX_ANSWER_BYTES}-byte UTF-8 bound")
    entries = read_inquiry_entries(configuration)
    if entries is None:
        return failure("inquiry-channel-absent", INQUIRY_CHANNEL_ABSENT)
    entry = entries.get(inquiry_id)
    if entry is None:
        return failure("unknown-inquiry",
                       f"unknown inquiryId {inquiry_id}: no Host question with that id was queued in this turn")
    if entry.get("state") == "answered":
        return failure("inquiry-state",
                       f"inquiry {inquiry_id} already has its recorded answer; it cannot be replaced")
    if entry.get("state") not in ("queued", "delivered"):
        return failure("inquiry-state", f"inquiry {inquiry_id} is {entry.get('state')} and can no longer be answered")
    receipt = _signed({"version": 1, "kind": "inquiry-answer", "identity": configuration["identity"],
                       "inquiryId": inquiry_id, "questionSha256": entry.get("questionSha256"),
                       "answer": answer}, configuration)
    return {"content": [{"type": "text", "text": receipt}]}
