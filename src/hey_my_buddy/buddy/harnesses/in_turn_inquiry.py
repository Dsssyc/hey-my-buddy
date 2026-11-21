"""Session-private MCP inquiry and history tools for Claude Code CLI Worker turns."""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from ...private_dirs import ensure_private_dir
from ...protocol.session_reader import PRIOR_SESSION_TOOL_NAMES
from ..roles.turn_io import private_json
from .session_receipts import (
    ReceiptError, refusal_shaped,
    verify_inquiry_receipt, verify_tool_refusal,
)

INQUIRY_TOOLS = ("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry")


@dataclass(frozen=True)
class InquiryServices:
    configuration: dict
    server_name: str
    config_path: str

    @property
    def has_inquiry(self) -> bool:
        return "inquiryJournalPath" in self.configuration

    @property
    def tool_names(self) -> tuple[str, ...]:
        return ((INQUIRY_TOOLS if self.has_inquiry else ()) +
                (PRIOR_SESSION_TOOL_NAMES if self.configuration.get("priorSessions") else ()))

    @property
    def prior_session_tools(self) -> tuple[str, ...]:
        if not self.configuration.get("priorSessions"):
            return ()
        return tuple(f"mcp__{self.server_name}__{name}" for name in PRIOR_SESSION_TOOL_NAMES)

    @property
    def answer_tool(self) -> str | None:
        return f"mcp__{self.server_name}__{INQUIRY_TOOLS[1]}" if self.has_inquiry else None

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
            verify_tool_refusal(raw_text, self.configuration, tool_name, INQUIRY_TOOLS)
            return
        kind = "inquiry-answer" if qualified[tool_name] == INQUIRY_TOOLS[1] else "inquiry-checkpoint"
        receipt = verify_inquiry_receipt(raw_text, self.configuration, kind)
        if kind == "inquiry-answer":
            bridge.record_answer(receipt, call_id)
        else:
            bridge.deliver_inquiries(receipt, call_id)


def prepare_inquiry_services(*, invocation_root: Path, identity: dict, input_sha256: str,
                             attention_path: Path, inquiry: dict | None = None,
                             answer_schema: dict | None = None,
                             prior_sessions: list[dict] | None = None) -> InquiryServices:
    root = ensure_private_dir(invocation_root)
    server_name = "hey_my_buddy_" + hashlib.sha256(identity["attemptId"].encode()).hexdigest()[:16]
    configuration = {"identity": dict(identity), "inputSha256": input_sha256,
                     "key": secrets.token_hex(32), "attentionPath": str(attention_path)}
    if inquiry is not None:
        configuration["inquiryJournalPath"] = inquiry["resultsPath"]
    if prior_sessions:
        configuration["priorSessions"] = prior_sessions
    config_path = root / "inquiry-bridge.json"
    services = InquiryServices(configuration=configuration, server_name=server_name, config_path=str(config_path))
    configuration["toolNames"] = list(services.tool_names)
    private_json(config_path, configuration, exclusive=True)
    return services
