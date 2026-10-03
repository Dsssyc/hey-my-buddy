#!/usr/bin/env python3
"""The native Codex protocol fixture with managed edits before a quota failure."""
from pathlib import Path
import mock_codex

original_send = mock_codex.send


def send(message):
    params = message.get("params") or {}
    if message.get("method") == "turn/started":
        thread = mock_codex.read_state()["threads"][params["threadId"]]
        (Path(thread["cwd"]) / "tracked.txt").write_text("Codex partial output before native quota failure\n")
    if message.get("method") == "item/completed" and (params.get("item") or {}).get("type") == "agentMessage":
        params["item"]["text"] = "The Codex fixture saved the edit; quota stopped validation."
    original_send(message)


mock_codex.send = send
mock_codex.main()
