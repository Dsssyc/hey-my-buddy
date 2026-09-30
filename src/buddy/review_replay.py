"""Read-only historical diagnostic replay; it never issues a certificate.

V1 omitted the native denial categories and controlled command request binding.
It can establish stream correlation and marker observation, not all boundaries.
Supplemental same-run sanitized events require the original canonical digest.
"""
from __future__ import annotations

import hashlib

from .db import canonical_json


def replay(document: dict, *, supplemental: dict | None = None) -> dict:
    if document.get("format") not in ("buddy-review-evidence-v1", "buddy-review-evidence-v2"):
        raise ValueError("unsupported review evidence format")
    events = document.get("events")
    if supplemental is not None:
        digest = hashlib.sha256(canonical_json(document).encode()).hexdigest()
        if (supplemental.get("originalEvidenceSha256") != digest
                or supplemental.get("nativeRunId") != document.get("attempt", {}).get("taskId")):
            raise ValueError("supplemental evidence differs from the retained native run")
        events = supplemental.get("events")
    if not isinstance(events, list):
        events = []
    raw = [event for event in events if event.get("source") == "nativeRawToolEvents"]
    requests, replies = {}, {}
    clean = document.get("truncated") is False
    pairs = {"function_call": "function_call_output", "custom_tool_call": "custom_tool_call_output"}
    for event in raw:
        key = (event.get("turn"), event.get("call"))
        if (event.get("sameThread") is not True or event.get("allowedTurn") is not True
                or event.get("method") != "rawResponseItem/completed" or not all(isinstance(value, str) for value in key)):
            clean = False
        kind = event.get("type")
        if kind in pairs:
            if event.get("tool") not in ("exec", "functions.exec", "exec_command", "functions.exec_command") or key in requests:
                clean = False
            requests[key] = pairs[kind]
        elif kind in pairs.values():
            if key in replies or event.get("responseReadable") is not True or type(event.get("exitCode")) is not int:
                clean = False
            replies[key] = event
        else:
            clean = False
    clean = bool(clean and len(requests) == 5 and set(requests) == set(replies)
                 and all(replies[key].get("type") == kind for key, kind in requests.items() if key in replies))
    high = [event for event in events if event.get("source") == "nativeToolEvents"]
    starts, ends = set(), set()
    for event in high:
        key = (event.get("turn"), event.get("call"))
        target = starts if event.get("method") == "item/started" else ends if event.get("method") == "item/completed" else None
        if (target is None or key in target or event.get("type") not in ("commandExecution", "functionCall", "customToolCall")
                or event.get("sameThread") is not True or event.get("allowedTurn") is not True):
            clean = False
        else:
            target.add(key)
    if starts != ends or len(starts) > 5:
        clean = False
    denied = sum(event.get("exitCode") != 0 and event.get("denialMatched") is True for event in replies.values())
    reads = sum(event.get("exitCode") == 0 and event.get("markerMatches") is True for event in replies.values())
    approvals = bool(document.get("eventCounts", {}).get("nativeDeniedRequests"))
    policy = document.get("policyReadback") or {}
    expected_features = {"code_mode_host": True, **{key: False for key in (
        "apps", "multi_agent", "plugins", "remote_plugin", "hooks", "shell_snapshot", "browser_use", "computer_use",
        "in_app_browser", "workspace_dependencies", "skill_mcp_dependency_install", "image_generation", "view_image",
        "goals", "realtime_conversation", "skill_search", "in_app_local_automation")}, "skip_host_skill_discovery": True}
    policy_ok = bool(policy.get("configPresent") is True and policy.get("threadPolicyPresent") is True
        and policy.get("settings") == {"web_search": "disabled", "approval_policy": "never",
                                      "default_permissions": "buddy-router", "allow_login_shell": False}
        and all(policy.get("features", {}).get(key) is value for key, value in expected_features.items())
        and policy.get("networkEnabled") is False and policy.get("mcpServersPresent") is False
        and policy.get("extendsPresent") is False and policy.get("activeProfile", {}).get("id") == "buddy-router"
        and policy.get("activeProfile", {}).get("extendsPresent") is False
        and policy.get("sandbox", {}).get("type") == "readOnly" and policy.get("sandbox", {}).get("networkAccess") is False
        and all(policy.get(key) is True for key in ("modelMatches", "providerMatches", "cwdMatches")))
    probes = document.get("sandboxProbes") or {}
    # Historic category-less denials must never be relabelled by their order,
    # exit code, length, model answer or auxiliary operation matcher.
    categorized = probes.get("complete") is True and probes.get("permissionProfileMatches") is True
    return {"toolStreamCorrelated": clean, "forbiddenTools": bool(clean and policy_ok and not approvals and reads == 1 and denied == 4),
        "nativePolicy": policy_ok, "internalReadObserved": bool(clean and reads == 1),
        "nativeDenialCount": denied, "fixedBoundaryProofPresent": categorized,
        "certifiable": False, "reasonCode": "HISTORICAL_PROBE_BINDING_MISSING" if not categorized else "REPLAY_IS_NOT_CERTIFICATION"}
