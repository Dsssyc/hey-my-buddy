"""Session-private MCP inquiry tools for Claude Code CLI Worker turns."""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from ...private_dirs import ensure_private_dir
from ..roles.turn_io import private_json
from .session_receipts import (
    BOUNDED_REFUSAL_DETAIL, ReceiptError, refusal_shaped,
    verify_inquiry_receipt, verify_tool_refusal,
)

INQUIRY_TOOLS = ("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry")
IN_TURN_NOTE = ("Questions enter the running native turn and are read at its next decision. "
                "They never start a turn or extend its deadline. Only a signed root tool receipt "
                "from hey_my_buddy_answer_inquiry records an answer.")


@dataclass(frozen=True)
class InquiryServices:
    configuration: dict
    server_name: str
    config_path: str

    @property
    def answer_tool(self) -> str:
        return f"mcp__{self.server_name}__hey_my_buddy_answer_inquiry"

    @property
    def mcp_server(self) -> dict:
        return {"command": sys.executable,
                "args": ["-m", "hey_my_buddy.buddy.roles.session_mcp", "--config", self.config_path],
                "env": {"PYTHONPATH": os.environ["PYTHONPATH"]} if os.environ.get("PYTHONPATH") else {}}

    def accept_tool_result(self, bridge, tool_name: str, raw_text: str, call_id: str) -> None:
        """Verify a result whose successful native root tool binding the driver proved."""
        qualified = {f"mcp__{self.server_name}__{name}": name for name in INQUIRY_TOOLS}
        if tool_name not in qualified:
            raise ReceiptError("invalid-inquiry-evidence", "the inquiry tool belongs to another session service")
        if refusal_shaped(raw_text):
            verify_tool_refusal(raw_text, self.configuration, tool_name, INQUIRY_TOOLS,
                                rules=BOUNDED_REFUSAL_DETAIL)
            return
        kind = "inquiry-answer" if qualified[tool_name] == INQUIRY_TOOLS[1] else "inquiry-checkpoint"
        receipt = verify_inquiry_receipt(raw_text, self.configuration, kind, rules=BOUNDED_REFUSAL_DETAIL)
        if kind == "inquiry-answer":
            bridge.record_answer(receipt, call_id)
        else:
            bridge.deliver_inquiries(receipt, call_id)


def prepare_inquiry_services(*, invocation_root: Path, identity: dict, input_sha256: str,
                             attention_path: Path, inquiry: dict, answer_schema: dict | None = None) -> InquiryServices:
    root = ensure_private_dir(invocation_root)
    server_name = "hey_my_buddy_" + hashlib.sha256(identity["attemptId"].encode()).hexdigest()[:16]
    configuration = {"identity": dict(identity), "inputSha256": input_sha256,
                     "key": secrets.token_hex(32), "attentionPath": str(attention_path),
                     "inquiryJournalPath": inquiry["resultsPath"], "toolNames": list(INQUIRY_TOOLS)}
    config_path = root / "inquiry-bridge.json"
    private_json(config_path, configuration, exclusive=True)
    return InquiryServices(configuration=configuration, server_name=server_name, config_path=str(config_path))
