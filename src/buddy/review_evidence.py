"""Strict diagnostic projection of private review traces, without native prose.

Never serialize arbitrary native objects. Paths and event identities become local
aliases; unknown strings, commands, output and account/config fields are omitted.
"""
from __future__ import annotations

import json
from pathlib import Path
import tomllib

from .harness_review import CHECKS
from .review_probe import _DENIAL, _SCRIPT_META, _native_call, _response, _turn_ids, commands

FILE = "review-evidence.json"
FORMAT = "buddy-review-evidence-v2"
_METHODS = {"rawResponseItem/completed", "item/started", "item/completed",
            "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
            "item/permissions/requestApproval", "item/tool/requestUserInput"}
_TYPES = {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output",
          "commandExecution", "commandExecutionOutput", "functionCall", "customToolCall",
          "local_shell_call", "web_search_call", "webSearch", "fileChange", "mcpToolCall"}
_TOOLS = {"functions.exec", "exec", "functions.exec_command", "exec_command"}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _value(value, allowed=()):
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str) and value in allowed:
        return value
    return {"kind": type(value).__name__ if type(value) in (str, int, float, list, dict) else "other", "redacted": True}


def _number(value, maximum=2**31 - 1):
    return value if type(value) is int and -maximum <= value <= maximum else None


def policy_readback(payload, frozen, configuration):
    from .adapters.codex_config import read_only_config
    config = _dict(payload.get("nativeConfigPolicy"))
    native = _dict(payload.get("nativePolicy"))
    expected = tomllib.loads(read_only_config(str(frozen)))
    profile = _dict(_dict(config.get("permissions")).get("buddy-router"))
    filesystem = _dict(profile.get("filesystem"))
    aliases = {str(frozen): "frozen", ":root": "root", ":minimal": "minimal", ":tmpdir": "tmpdir",
               ":slash_tmp": "slashTmp", "/private/tmp": "macTmp"}
    aliases[str(Path(frozen).resolve())] = "frozen"
    active = _dict(native.get("activePermissionProfile"))
    sandbox = _dict(native.get("sandbox"))
    return {
        "configPresent": isinstance(payload.get("nativeConfigPolicy"), dict),
        "threadPolicyPresent": isinstance(payload.get("nativePolicy"), dict),
        "settings": {key: _value(config.get(key), ("disabled", "never", "buddy-router"))
                     for key in ("web_search", "approval_policy", "default_permissions", "allow_login_shell")},
        "features": {key: _value(_dict(config.get("features")).get(key)) for key in expected["features"]},
        "otherFeatureEntries": sum(key not in expected["features"] for key in _dict(config.get("features"))),
        "shellEnvironment": {key: _value(_dict(config.get("shell_environment_policy")).get(key), ("core",))
                             for key in expected["shell_environment_policy"]},
        "otherShellEntries": sum(key not in expected["shell_environment_policy"] for key in _dict(config.get("shell_environment_policy"))),
        "filesystem": {alias: _value(filesystem.get(path), ("deny", "read", "write")) for path, alias in aliases.items()},
        "otherFilesystemEntries": sum(key not in aliases for key in filesystem),
        "networkEnabled": _value(_dict(profile.get("network")).get("enabled")),
        "otherNetworkEntries": sum(key != "enabled" for key in _dict(profile.get("network"))),
        "extendsPresent": bool(profile.get("extends")), "mcpServersPresent": bool(config.get("mcp_servers")),
        "activeProfile": {"id": _value(active.get("id"), ("buddy-router",)), "extendsPresent": bool(active.get("extends")),
                          "extraFields": sum(key not in ("id", "extends") for key in active)},
        "sandbox": {"type": _value(sandbox.get("type"), ("readOnly", "workspaceWrite", "dangerFullAccess", "externalSandbox")),
                    "networkAccess": _value(sandbox.get("networkAccess")),
                    "extraFields": sum(key not in ("type", "networkAccess") for key in sandbox)},
        "approvalPolicy": _value(native.get("approvalPolicy"), ("never", "on-request", "on-failure", "untrusted")),
        "modelMatches": native.get("model") == configuration.get("model"),
        "providerMatches": native.get("modelProvider") == configuration.get("provider"),
        "cwdMatches": native.get("cwd") == str(frozen),
    }


def _response_shape(item):
    """Known envelope landmarks aid parser diagnosis without keeping body text."""
    raw = item.get("output")
    shape = {"valueKind": type(raw).__name__ if type(raw) in (str, dict, list) else "other"}
    if isinstance(raw, list):
        shape["contentBlockCount"] = len(raw)
        shape["inputTextBlockCount"] = sum(_dict(block).get("type") == "input_text" for block in raw)
        shape["scriptMetadataMatched"] = bool(raw and isinstance(_dict(raw[0]).get("text"), str)
                                                and _SCRIPT_META.fullmatch(raw[0]["text"]))
        if len(raw) == 2:
            raw = _dict(raw[1]).get("text")
    if isinstance(raw, str):
        shape["textExitEnvelope"] = "Process exited with code " in raw
        try:
            raw = json.loads(raw)
            shape["jsonDecoded"] = True
        except (ValueError, RecursionError):
            shape["jsonDecoded"] = False
    if isinstance(raw, dict):
        shape["knownFields"] = [key for key in ("content", "exit_code", "exitCode", "output", "session_id", "wall_time_seconds") if key in raw]
        shape["otherFieldCount"] = len(raw) - len(shape["knownFields"])
        content = raw.get("content")
        if isinstance(content, list):
            shape["contentBlockCount"] = len(content)
            shape["textBlockCount"] = sum(_dict(block).get("type") == "text" for block in content)
    return shape


def event_summaries(payload, frozen, sentinel, url, marker):
    expected = commands(frozen, sentinel, url)
    identity = _dict(payload.get("nativeIdentity"))
    try:
        allowed_turns = _turn_ids(payload, identity)
    except (TypeError, ValueError):
        allowed_turns = set()
    turns, calls = {}, {}
    summary = []
    def alias(table, key, prefix):
        if not isinstance(key, str) or not key:
            return None
        if key not in table:
            table[key] = prefix + str(len(table) + 1)
        return table[key]
    for source, limit in (("nativeRawToolEvents", 128), ("nativeToolEvents", 64), ("nativeDeniedRequests", 24)):
        events = payload.get(source)
        if not isinstance(events, list):
            continue
        for index, event in enumerate(events[:limit]):
            event = _dict(event)
            params = _dict(event.get("params")) or event
            item = _dict(params.get("item"))
            turn = params.get("turnId")
            call = item.get("call_id") or item.get("id")
            parser_readable = True
            try:
                operation = _native_call(item, frozen, expected)
                response = _response(item)
            except (TypeError, ValueError, AttributeError, KeyError, IndexError, RecursionError):
                operation, response, parser_readable = None, None, False
            entry = {"source": source, "index": index, "method": _value(event.get("method"), _METHODS),
                     "type": _value(item.get("type"), _TYPES), "tool": _value(item.get("name"), _TOOLS),
                     "sameThread": params.get("threadId") == identity.get("sessionId") and bool(identity.get("sessionId")),
                     "allowedTurn": isinstance(turn, str) and turn in allowed_turns,
                     "turn": alias(turns, turn, "t"),
                     "call": alias(calls, json.dumps([turn if isinstance(turn, str) else None, call]) if isinstance(call, str) else None, "c"),
                     "operation": operation, "parserReadable": parser_readable,
                     "operationAuxiliary": True,
                     "knownFields": [key for key in ("arguments", "input", "command", "cwd", "output", "exitCode", "aggregatedOutput") if key in item]}
            if item.get("type") == "commandExecution" and type(item.get("exitCode")) is int and isinstance(item.get("aggregatedOutput"), str):
                response = (item["exitCode"], item["aggregatedOutput"])
            entry["responseReadable"] = response is not None
            if response is not None:
                entry.update(exitCode=_number(response[0]), outputBytes=min(len(response[1].encode()), 262144),
                             denialMatched=bool(_DENIAL.search(response[1])), markerMatches=response[1].strip() == marker)
            if "output" in item:
                entry["responseShape"] = _response_shape(item)
            summary.append(entry)
    return summary


def diagnostic(payload, *, plan, checks, reasons, basis, frozen, sentinel, url, marker, usage):
    """All retained keys are owned here; no raw text/config/event is passed through."""
    return {"format": FORMAT,
        "adapter": "codex", "version": plan["version"], "platform": plan["platform"],
        "nativeStatus": _value(payload.get("status"), ("ok", "error", "cancelled")),
        "nativeFailure": _value(payload.get("code"), (
            "readonly-policy-unverified", "readonly-configuration-mismatch", "wrong-native-workspace",
            "wrong-native-turn", "native-turn-failed", "invalid-configuration", "readonly-budget-exhausted",
            "native-rpc-error", "invalid-native-result", "native-exit", "deadline", "user-cancel", "native-shutdown-failed")),
        "policyReadback": policy_readback(payload, frozen, plan["configuration"]),
        "sandboxProbes": probe_summaries(payload.get("nativeSandboxProbes"), marker),
        "events": event_summaries(payload, frozen, sentinel, url, marker),
        "eventCounts": {key: len(payload[key]) if isinstance(payload.get(key), list) else None
                        for key in ("nativeRawToolEvents", "nativeToolEvents", "nativeDeniedRequests")},
        "truncated": payload.get("nativeEvidenceTruncated") is True, "usage": usage,
        "checks": {key: {"passed": checks[key] is True, "reasonCode": reasons.get(key),
                         "basis": basis.get(key, {"evidenceReadable": False})} for key in CHECKS}}


def probe_summaries(value, marker):
    from .sandbox_probe import PROBES, PROFILE, results
    parsed, complete = results(value)
    records = _dict(value).get("operations")
    records = records if isinstance(records, list) else []
    return {"complete": complete, "permissionProfileMatches": _dict(value).get("permissionProfile") == PROFILE,
        "operations": [{"operation": _value(record.get("operation"), PROBES),
            "request": "p" + str(index + 1), "requestId": _number(record.get("requestId")),
            "methodMatches": record.get("method") == "command/exec",
            "permissionProfileMatches": record.get("permissionProfile") == PROFILE,
            "exitCode": _number(record.get("exitCode")), "truncated": record.get("truncated") is not False,
            "stdoutBytes": min(len(record["stdout"].encode()), 8192) if isinstance(record.get("stdout"), str) else None,
            "stderrBytes": min(len(record["stderr"].encode()), 8192) if isinstance(record.get("stderr"), str) else None,
            "denialMatched": bool(isinstance(record.get("stderr"), str) and _DENIAL.search(record["stderr"])),
            "markerMatches": isinstance(record.get("stdout"), str) and record["stdout"].strip() == marker}
            for index, record in enumerate(records[:5]) if isinstance(record, dict)]}
