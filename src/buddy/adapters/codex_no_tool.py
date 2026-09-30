"""Codex 0.157.0 structured Router call with an empty native tool inventory."""
from __future__ import annotations

import json
import os
import queue
import tomllib
from pathlib import Path

from .codex_protocol import CodexProtocolError, decode_json
from .read_only import correction_code, valid_answer
from .turn_io import private_json

# These switches cover the sources in codex-rs/core/src/tools/spec_plan.rs.
# ModelInfo additionally advertises clock and asynchronous message tools, so
# feature switches alone cannot establish an empty native inventory.
_TOOL_FEATURES = (
    "shell_tool", "unified_exec", "code_mode", "code_mode_host", "code_mode_only",
    "multi_agent", "multi_agent_v2", "apps",
    "plugins", "remote_plugin", "hooks", "view_image", "image_generation",
    "token_budget", "current_time_reminder", "sleep_tool", "send_message_to_user_async",
    "request_permissions_tool", "goals", "browser_use", "computer_use",
    "in_app_browser", "tool_suggest", "recommended_plugins", "tool_search",
    "standalone_web_search", "deferred_executor", "skill_search", "enable_mcp_apps",
    "memories", "agent_message_board", "send_async_message", "shell_snapshot",
    "default_mode_request_user_input", "tool_call_mcp_elicitation", "artifact",
    "in_app_local_automation", "realtime_conversation",
)


def _config(catalog_path: Path) -> str:
    lines = [f"model_catalog_json = {json.dumps(str(catalog_path))}",
             'web_search = "disabled"', 'approval_policy = "never"',
             'project_doc_max_bytes = 0', 'developer_instructions = ""',
             'include_environment_context = false', 'include_permissions_instructions = false',
             'include_apps_instructions = false', 'include_collaboration_mode_instructions = false',
             '[skills]', 'include_instructions = false', '[skills.bundled]', 'enabled = false',
             '[tools.update_plan]', 'enabled = false',
             '[tools.experimental_request_user_input]', 'enabled = false',
             '[cloud.skills]', 'enabled = false', '[orchestrator.mcp]', 'enabled = false',
             '[features]']
    lines.extend(f"{name} = false" for name in _TOOL_FEATURES)
    lines.append('skip_host_skill_discovery = true')
    return "\n".join(lines) + "\n"


def prepare_home(native_root: Path, environment: dict, spec: dict) -> Path:
    """Copy only native public model metadata; leave the real account home intact."""
    old_home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
    home = native_root / "codex-home"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        cache = decode_json((old_home / "models_cache.json").read_bytes())
        models = cache["models"]
        matches = [model for model in models if isinstance(model, dict) and model.get("slug") == spec.get("model")]
        if not isinstance(cache, dict) or not isinstance(models, list) or len(matches) != 1:
            raise ValueError("missing or mismatched native model metadata")
        model = dict(matches[0])
        if not isinstance(model.get("supported_reasoning_levels"), list) or not any(
                entry.get("effort") == spec.get("effort") for entry in model["supported_reasoning_levels"]
                if isinstance(entry, dict)):
            raise ValueError("native effort metadata differs")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise CodexProtocolError("no-tool-policy-unverified", "Codex native model metadata is unavailable or mismatched") from None
    model.update(shell_type="disabled", apply_patch_tool_type=None,
                 experimental_supported_tools=[], supports_search_tool=False,
                 node_repl_disabled=True, tool_mode="direct", multi_agent_version=None,
                 include_skills_usage_instructions=False, include_plugin_usage_instructions=False,
                 include_apps_usage_instructions=False)
    catalog_path = home / "no-tool-models.json"
    private_json(catalog_path, {"models": [model]})
    (home / "config.toml").write_text(_config(catalog_path))
    os.chmod(home / "config.toml", 0o600)
    auth = old_home / "auth.json"
    if auth.is_file():
        (home / "auth.json").symlink_to(auth)
    return home


def _matches(actual, wanted):
    if isinstance(wanted, dict):
        return isinstance(actual, dict) and all(_matches(actual.get(key), value) for key, value in wanted.items())
    return type(actual) is type(wanted) and actual == wanted


def reject_tool_event(message: dict):
    """Apply before filtering identities, so early, raw and child calls cannot hide."""
    pending = [message]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            kind = value.get('type', '')
            if kind in ('function_call', 'custom_tool_call', 'local_shell_call', 'web_search_call',
                        'image_generation_call', 'mcp_tool_call', 'commandExecution', 'fileChange',
                        'mcpToolCall', 'dynamicToolCall', 'webSearch', 'imageGeneration', 'collabAgentToolCall'):
                raise CodexProtocolError('no-tool-violation', 'Native tool item in a no-tool stream')
            pending.extend(item for item in value.values() if isinstance(item, (dict, list)))
        elif isinstance(value, list):
            pending.extend(value)


def _observe(message: dict, thread_id: str, turn_id: str, state: dict):
    reject_tool_event(message)
    method = message.get("method")
    params = message.get("params")
    item = params.get("item") if isinstance(params, dict) else None
    kind = item.get("type") if isinstance(item, dict) else None
    if (isinstance(method, str) and method.startswith(("collabAgent/", "tool/", "mcp/"))
            or method in ("item/started", "item/updated", "item/completed") and
            kind not in ("agentMessage", "reasoning", "userMessage")
            or isinstance(method, str) and method.startswith("rawResponseItem/") and
            kind not in ("message", "reasoning")):
        raise CodexProtocolError("no-tool-violation", "Native tool or unrecognized item appeared")
    native_turn = params.get("turn") if isinstance(params, dict) else None
    if method == "turn/completed" and isinstance(native_turn, dict):
        for completed_item in native_turn.get("items", []):
            if not isinstance(completed_item, dict) or completed_item.get("type") not in ("agentMessage", "reasoning", "userMessage"):
                raise CodexProtocolError("no-tool-violation", "Native completed turn contains a tool")
    if method in ('account/rateLimits/updated', 'remoteControl/status/changed', 'deprecationNotice'):
        return
    if method == 'thread/started' and isinstance(params, dict):
        thread = params.get('thread') or {}
        if thread.get('id') == thread_id and thread.get('environments') == []:
            return
    if not isinstance(params, dict) or params.get("threadId") != thread_id:
        raise CodexProtocolError("invalid-native-result", "Unbound native event in no-tool stream")
    if method in ('thread/tokenUsage/updated', 'thread/status/changed', 'thread/settings/updated'):
        return
    if method == 'warning':
        return
    if method == 'error':
        if params.get('willRetry') is True:
            return
        raise CodexProtocolError('native-turn-failed', 'Native no-tool turn reported an error')
    event_turn = native_turn.get("id") if isinstance(native_turn, dict) else params.get("turnId")
    if event_turn != turn_id:
        raise CodexProtocolError("invalid-native-result", "Foreign native turn in no-tool stream")
    if method == "turn/started":
        if state["started"]:
            raise CodexProtocolError("invalid-native-result", "Duplicate native turn start")
        state["started"] = True
    elif method in ("item/started", "item/updated", "item/completed"):
        if method == "item/completed" and kind == "agentMessage" and item.get("phase") == "final_answer":
            if state["final"] is not None:
                raise CodexProtocolError("invalid-native-result", "Multiple final answers")
            state["final"] = item
    elif method.startswith("rawResponseItem/"):
        pass
    elif method == "turn/completed":
        if state["completed"] is not None:
            raise CodexProtocolError("invalid-native-result", "Duplicate native turn completion")
        state["completed"] = native_turn
    elif method in ('item/agentMessage/delta', 'item/reasoning/summaryTextDelta',
                    'item/reasoning/textDelta', 'item/reasoning/summaryPartAdded', 'rawResponse/completed'):
        pass
    elif method == 'turn/diff/updated' and params.get('diff') == '':
        pass
    else:
        raise CodexProtocolError("invalid-native-result", "Unrecognized native no-tool event")


def _drain(connection):
    """Consume every queued native frame through EOF after closing the server input."""
    connection.process.stdin.close()
    while True:
        remaining = connection._remaining()
        try:
            message = connection.messages.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            continue
        if message is None:
            return
        if isinstance(message, CodexProtocolError):
            raise message
        if "id" in message and "method" in message:
            connection.on_request(message)
        elif "id" in message:
            raise CodexProtocolError("invalid-native-result", "Unexpected late native response")
        elif isinstance(message.get("method"), str):
            connection.on_notification(message)
        else:
            raise CodexProtocolError("invalid-native-result", "Unrecognized late native frame")


def _format_correction(raw, schema):
    return correction_code(raw, schema)


def run_call(connection, control: dict, result: dict, catalog: dict):
    spec, request = control["spec"], control["noToolRequest"]
    for message in control.get("_earlyNoToolNotifications", []):
        method = message.get("method")
        if isinstance(method, str) and method.startswith(("item/", "rawResponseItem/", "collabAgent/", "tool/", "mcp/")):
            raise CodexProtocolError("no-tool-violation", "Native tool event preceded the no-tool turn")
    if (spec.get("provider") != "openai" or not any(
            model["id"] == spec.get("model") and spec.get("effort") in model["efforts"]
            for model in catalog["providers"][0]["models"])):
        raise CodexProtocolError("invalid-configuration", "Unknown native no-tool configuration")
    home = Path(control["_noToolHome"])
    expected = tomllib.loads((home / "config.toml").read_text())
    config_read = connection.call("config/read", {"cwd": control["cwd"], "includeLayers": True})
    configured, layers = config_read.get("config"), config_read.get("layers")
    own = [layer for layer in layers or [] if isinstance(layer, dict) and
           isinstance(layer.get("name"), dict) and layer["name"].get("type") == "user" and
           layer["name"].get("file") == str(home / "config.toml")]
    foreign = [layer for layer in layers or [] if isinstance(layer, dict) and
               isinstance(layer.get("name"), dict) and
               layer["name"].get("type") not in ("user", "packagedDefaults") and layer.get("config")]
    if (not isinstance(configured, dict) or configured.get("web_search") != "disabled"
            or configured.get("approval_policy") != "never" or configured.get("mcp_servers")
            or not isinstance(layers, list) or len(own) != 1 or foreign
            or not _matches(own[0].get("config"), expected)
            or any(layer is not own[0] and isinstance(layer, dict) and
                   isinstance(layer.get("name"), dict) and layer["name"].get("type") == "user"
                   for layer in layers)):
        raise CodexProtocolError("no-tool-policy-unverified", "Codex effective no-tool policy differs")
    response = connection.call("thread/start", {
        "cwd": control["cwd"], "model": spec["model"], "modelProvider": "openai",
        "approvalPolicy": "never", "sandbox": "read-only", "serviceName": "hey-my-buddy",
        "experimentalRawEvents": True, "environments": [], "dynamicTools": [],
        "selectedCapabilityRoots": [],
        "allowProviderModelFallback": False, "ephemeral": True,
        "baseInstructions": "Return only JSON matching the supplied output schema. Do not call tools.",
    })
    thread = response.get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if (not isinstance(thread_id, str) or not thread_id
            or Path(thread.get("cwd") or "").resolve() != Path(control["cwd"]).resolve()
            or thread.get("turns") not in (None, [])
            or response.get("model") != spec["model"] or response.get("modelProvider") != "openai"
            or response.get("approvalPolicy") != "never" or thread.get('environments') != []):
        raise CodexProtocolError("no-tool-policy-unverified", "Codex did not acknowledge the no-tool thread")
    result.update(sessionId=thread_id, resolved=dict(spec), nativePolicy={"configuration": "private-no-tool",
                  "environments": [], "dynamicTools": [], "modelCatalog": str(home / "no-tool-models.json")})
    prompt = request["prompt"]
    for index in range(2):
        pending = []
        def queue_pending(message):
            if len(pending) >= 128:
                raise CodexProtocolError("invalid-native-result", "Codex no-tool event stream exceeded its bound")
            pending.append(message)
        connection.on_notification = queue_pending
        result["modelStarted"] = True
        response = connection.call("turn/start", {
            "threadId": thread_id, "cwd": control["cwd"], "model": spec["model"],
            "effort": spec["effort"], "input": [{"type": "text", "text": prompt}],
            "approvalPolicy": "never", "environments": [],
            "outputSchema": request["outputSchema"],
        })
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexProtocolError("invalid-native-result", "Codex returned no native turn identity")
        if turn.get("items") not in (None, []):
            if not isinstance(turn["items"], list) or any(not isinstance(item, dict) or
                    item.get("type") not in ("agentMessage", "reasoning", "userMessage") for item in turn["items"]):
                raise CodexProtocolError("no-tool-violation", "Native turn started with a tool item")
        result["nativeTurnId"] = turn_id
        result["nativeIdentity"] = {"sessionId": thread_id, "turnId": turn_id}
        result["modelStarted"] = True
        state = {"started": False, "completed": None, "final": None}
        def observed(message):
            _observe(message, thread_id, turn_id, state)
        connection.on_notification = observed
        for message in pending:
            observed(message)
        while state["completed"] is None:
            connection.pump()
        final = state["final"]
        if (not state["started"] or state["completed"].get("status") != "completed"
                or not isinstance(final, dict) or not isinstance(final.get("text"), str)
                or not isinstance(final.get("id"), str)):
            raise CodexProtocolError("invalid-native-result", "No complete native no-tool answer")
        raw = final["text"]
        result.update(rawAnswer=raw, answerValid=valid_answer(raw, request["outputSchema"]), correctionCount=index)
        correction = _format_correction(raw, request["outputSchema"])
        if index or correction is None:
            break
        prompt = request["prompt"] + "\n\nFormat correction: " + correction + ". Return exactly the supplied JSON Schema."
    _drain(connection)
    result.update(status="ok", usage={"toolCalls": 0, "bytesRead": None}, _noToolStreamComplete=True)
