#!/usr/bin/env python3
"""Offline deletion-only app-server: every unexpected RPC fails the process."""
import json
import os
import sys
from pathlib import Path

if "--version" in sys.argv:
    print("codex-cli deletion-fixture")
    raise SystemExit(0)

trace = Path(os.environ["HMB_CODEX_FIXTURE_STATE"])
for raw in sys.stdin:
    request = json.loads(raw)
    with trace.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(request) + "\n")
    method = request["method"]
    if method == "initialized":
        continue
    frame = {"id": request["id"]}
    if method == "initialize":
        frame["result"] = {"userAgent": "offline-delete-fixture"}
    elif method == "thread/delete":
        session_id = request["params"]["threadId"]
        errors = {
            "gone": {"code": -32600, "message": "no rollout found for thread id gone"},
            "invalid": {"code": -32600, "message": "invalid thread id: invalid"},
            "denied": {"code": -32000, "message": "permission denied"},
            "same-prefix-other-code": {"code": -32000, "message": "no rollout found for thread id x"},
            "malformed-error": {"code": "-32600", "message": "no rollout found for thread id x"},
            "missing-message": {"code": -32600},
            "long-error": {"code": -32000, "message": "native-reason-" * 60 + "-end"},
        }
        if session_id in errors:
            frame["error"] = errors[session_id]
        elif session_id == "invalid-result":
            frame["result"] = []
        elif session_id == "missing-result":
            pass
        elif session_id == "extra-result":
            frame["result"] = {"unexpected": True}
        else:
            frame["result"] = {}
    else:
        raise RuntimeError("unexpected model/account/config RPC: " + method)
    print(json.dumps(frame), flush=True)
