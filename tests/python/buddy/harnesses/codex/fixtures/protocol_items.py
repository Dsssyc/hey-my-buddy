"""Codex 0.162.1 protocol samples; expectations are independent of production tables."""

# name, carrier, item, start count, projected phases, complete stream
PROTOCOL_ITEMS = (
    ("hookPrompt", "typed", {"fragments": [{"hookRunId": "hook-1", "text": "hook prompt"}]}, 0, (), True),
    ("functionCallOutput", "typed", {"name": "shell", "output": "done"}, 0, ("end",), True),
    ("plan", "typed", {"text": "proposed plan"}, 0, (), True),
    ("subAgentActivity", "typed", {"agentPath": "root/child", "agentThreadId": "child-1", "kind": "completed"}, 0, (), True),
    ("imageView", "typed", {"path": "/fixture/image.png"}, 1, ("start", "end"), True),
    ("sleep", "typed", {"durationMs": 1}, 1, ("start", "end"), True),
    ("enteredReviewMode", "typed", {"review": "review task"}, 0, (), True),
    ("exitedReviewMode", "typed", {"review": "review done"}, 0, (), True),
    ("contextCompaction", "typed", {}, 0, (), True),
    ("additional_tools", "raw", {"role": "developer", "tools": [{"type": "function", "name": "shell"}]}, 0, (), True),
    ("agent_message", "raw", {"author": "agent-1", "recipient": "agent-2", "content": [{"type": "input_text", "text": "hello"}]}, 0, (), True),
    ("tool_search_call", "raw", {"call_id": "item-1", "arguments": {"query": "tools"}, "execution": "client"}, 1, ("start",), True),
    ("tool_search_output", "raw", {"call_id": "item-1", "execution": "client", "status": "completed", "tools": []}, 0, ("end",), True),
    ("compaction", "raw", {"encrypted_content": "opaque-context"}, 0, (), True),
    ("configuration_update", "raw", {"reasoning": {"effort": "low"}}, 0, (), True),
    ("compaction_trigger", "raw", {}, 0, (), True),
    ("context_compaction", "raw", {"encrypted_content": "opaque-context"}, 0, (), True),
    ("other", "raw", {}, 0, (None,), False),
)
FUTURE_ITEMS = (
    ("futureThreadItem", "typed", {}, 1, ("start", "end"), True),
    ("future_response_item", "raw", {}, 0, (None,), False),
)


def item_frames(name):
    """Emit the same protocol sample on both offline App Server paths."""
    sample = next(case for case in PROTOCOL_ITEMS + FUTURE_ITEMS if case[0] == name)
    kind, carrier, fields, *_ = sample
    item = {"type": kind, "id": "item-1", **fields}
    if carrier == "typed":
        return [("item/started", item), ("item/completed", item)]
    return [("rawResponseItem/completed", item)]


def session_frames(input_text):
    """Synthetic streaming timing fixture, not additional real CLI observations.

    Only PROTOCOL_ITEMS above establishes the recorded 0.162.1 samples.
    Reasoning indices and command output delta fields below remain unverified
    against a real CLI; these exercise the projector's explicit field mapping.
    """
    return [
        ("item/started", {"item": {"id": "input", "type": "userMessage", "content": [{"type": "text", "text": input_text}]}}),
        ("item/completed", {"item": {"id": "input", "type": "userMessage", "content": [{"type": "text", "text": input_text}]}}),
        ("item/started", {"item": {"id": "stream", "type": "agentMessage", "text": "", "phase": "commentary"}}),
        ("item/agentMessage/delta", {"itemId": "stream", "delta": "运行中"}),
        ("item/agentMessage/delta", {"itemId": "stream", "delta": "𝄞"}),
    ]
