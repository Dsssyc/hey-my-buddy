"""Read-only historical diagnostic replay; it never issues a certificate.

The model tool stream is replayed structurally under the current rules; output
content and any consistency with the fixed probes are never evidence. V1
omitted the native denial categories and controlled command request binding, so
it can establish stream correlation and marker observation, not all boundaries.
Supplemental same-run sanitized events require the original canonical digest.
"""
from __future__ import annotations

import hashlib

from .db import canonical_json
from .sandbox_probe import PROBES

_FORMATS = ("buddy-review-evidence-v1", "buddy-review-evidence-v2", "buddy-review-evidence-v3")
_TOOLS = ("exec", "functions.exec", "exec_command", "functions.exec_command")
_BOUNDARIES = ("outside-read", "inside-write", "outside-write", "network")
_RUNTIME_CHECKS = ("requestIdentity", "inputUnchanged", "sentinelUnchanged", "shutdownConfirmed", "budgetConsistent")


def _stream(events, truncated) -> tuple[bool, dict]:
    """Correlate the retained tool stream by identity and type, never by output."""
    raw = [event for event in events if event.get("source") == "nativeRawToolEvents"]
    high = [event for event in events if event.get("source") == "nativeToolEvents"]
    pairs = {"function_call": "function_call_output", "custom_tool_call": "custom_tool_call_output",
             "commandExecution": "commandExecutionOutput"}
    requests, replies = {}, {}
    clean = truncated is False
    use_raw = bool(raw)
    for event in raw if use_raw else high:
        key = (event.get("turn"), event.get("call"))
        kind, method = event.get("type"), event.get("method")
        if (event.get("sameThread") is not True or event.get("allowedTurn") is not True
                or not all(isinstance(value, str) and value for value in key)):
            clean = False
        if use_raw:
            if method != "rawResponseItem/completed":
                clean = False
            if kind in pairs:
                if (kind != "commandExecution" and event.get("tool") not in _TOOLS) or key in requests:
                    clean = False
                requests[key] = pairs[kind]
            elif kind in pairs.values():
                if key in replies:
                    clean = False
                replies[key] = event
            else:
                clean = False
        else:
            if kind != "commandExecution" or method not in ("item/started", "item/completed"):
                clean = False
            elif method == "item/started":
                if key in requests:
                    clean = False
                requests[key] = "commandExecution"
            elif key in replies or event.get("responseReadable") is not True or type(event.get("exitCode")) is not int:
                clean = False
            else:
                replies[key] = event
    clean = bool(clean and len(requests) == 5 and set(requests) == set(replies)
                 and all(replies[key].get("type") == kind for key, kind in requests.items() if key in replies))
    starts, ends = set(), set()
    for event in high:
        key = (event.get("turn"), event.get("call"))
        target = starts if event.get("method") == "item/started" else ends if event.get("method") == "item/completed" else None
        if (target is None or key in target or event.get("type") not in ("commandExecution", "functionCall", "customToolCall")
                or event.get("sameThread") is not True or event.get("allowedTurn") is not True
                or not all(isinstance(value, str) and value for value in key)):
            clean = False
        else:
            target.add(key)
    if starts != ends or len(starts) > len(requests):
        clean = False
    return clean, replies


def replay(document: dict, *, supplemental: dict | None = None) -> dict:
    if document.get("format") not in _FORMATS:
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
    clean, replies = _stream(events, document.get("truncated"))
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
    records = probes.get("operations") if isinstance(probes.get("operations"), list) else []
    operations = {record.get("operation"): record for record in records if isinstance(record, dict)}
    def probe_denied(name):
        record = operations.get(name)
        return bool(isinstance(record, dict) and record.get("methodMatches") is True
                    and record.get("permissionProfileMatches") is True and record.get("truncated") is False
                    and isinstance(record.get("exitCode"), int) and record["exitCode"] != 0
                    and record.get("denialMatched") is True)
    def probe_read():
        record = operations.get("internal-read")
        return bool(isinstance(record, dict) and record.get("methodMatches") is True
                    and record.get("permissionProfileMatches") is True and record.get("truncated") is False
                    and record.get("exitCode") == 0 and record.get("markerMatches") is True)
    # Historic category-less denials must never be relabelled by their order,
    # exit code, length, model answer or auxiliary operation matcher.
    categorized = bool(probes.get("complete") is True and probes.get("permissionProfileMatches") is True
                       and set(operations) == set(PROBES))
    boundary = bool(categorized and all(probe_denied(name) for name in _BOUNDARIES)
                    and (document.get("checks", {}).get("boundaryDenials", {}).get("basis", {})
                         .get("positiveNetworkControl") is True))
    internal = bool(categorized and probe_read())
    def recorded_pass(name):
        entry = document.get("checks", {}).get(name) or {}
        basis = entry.get("basis")
        return bool(entry.get("passed") is True and isinstance(basis, dict) and basis
                    and all(value is True for value in basis.values()))
    runtime_only = all(recorded_pass(name) for name in _RUNTIME_CHECKS)
    checks_pass = bool(clean and policy_ok and not approvals and categorized and boundary and internal and runtime_only)
    return {"toolStreamCorrelated": clean, "forbiddenTools": bool(clean and policy_ok and not approvals),
            "nativePolicy": policy_ok,
            "internalReadObserved": bool(internal if categorized else clean and reads == 1),
            "nativeDenialCount": sum(probe_denied(name) for name in _BOUNDARIES) if categorized else denied,
            "fixedBoundaryProofPresent": categorized, "boundaryDenials": boundary if categorized else None,
            "checksPass": checks_pass, "certifiable": False,
            "reasonCode": "HISTORICAL_PROBE_BINDING_MISSING" if not categorized else "REPLAY_IS_NOT_CERTIFICATION"}
