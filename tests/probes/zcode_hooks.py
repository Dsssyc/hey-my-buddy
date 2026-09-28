#!/usr/bin/env python3
"""P3 probe: private-storage ZCode hook isolation and PostToolUse nonce injection.

The probe answers two questions against the installed native ZCode CLI without
touching the user's ``~/.zcode`` state:

1. Can a hook be installed for one attempt only? ``ZCODE_STORAGE_DIR`` verifiably
   redirects all native state (session DB, plugin cache, exec, logs) into a
   private directory, but the user-level config file stays hard-coded at
   ``$HOME/.zcode/cli/config.json``: a hooks block written to
   ``<storage>/cli/config.json`` never fires (observed, paid run 1). The
   working per-attempt registration is a workspace-scoped inline plugin:
   ``<cwd>/.zcode/config.json`` lists ``plugins.dirs`` and the plugin ships
   ``hooks/hooks.json`` (the resolver enables inline plugins by default).
2. Does a ``PostToolUse`` hook's ``additionalContext`` reach the running model
   turn? Yes — observed (paid run 2): the model quoted the fixed harmless
   nonce that only the hook injected, with the hook's ``session_id`` matching
   the CLI session exactly.

Every native subprocess runs with an explicitly constructed minimal
environment: Buddy runtime/worker/agent credentials and virtual-environment
variables are scrubbed (AGENTS.md), ZCode state/log/session paths point inside
the private work directory, and provider credentials are used only through the
same ``ZCODE_*_PROVIDER_CONFIG_FILE`` variables the harness passes to this
attempt. The user-level ``~/.zcode`` tree is hashed before and after every
native run and any difference is reported; background appends by the user's
separately running desktop app are attributed in the acceptance record.

Paid model traffic is gated behind ``--paid``; without it the probe only
assembles the private workspace, self-tests the hook process, lists plugins,
hashes the global root and asks the CLI for its version.

Raw CLI stdout/stderr stay inside the work directory (default: the ignored
``tmp/`` tree); everything printed or summarized is redacted.
This probe is a bounded experiment. It declares no production capability and
must not be treated as acceptance of a production integration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROBE_VERSION = "1"

DEFAULT_BUNDLE = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"
DEFAULT_APP_PLIST = "/Applications/ZCode.app/Contents/Info.plist"
DEFAULT_GLOBAL_ROOT = str(Path.home() / ".zcode")
DEFAULT_NODE_CANDIDATES = (
    "/opt/homebrew/opt/node/bin/node",
    "/usr/local/bin/node",
    "/opt/homebrew/bin/node",
)

# AGENTS.md: clear inherited runtime, Worker and agent credentials for every
# test subprocess, plus the virtual-environment variables uv injects.
CREDENTIAL_ENV_VARS = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE",
    "BUDDY_WORKER_ID",
    "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

PROVIDER_ENV_VARS = (
    "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE",
    "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE",
)

# Minimal passthrough for a native CLI child: home, lookup paths, proxies and
# the deployment marker. Everything else is deliberately not inherited.
PASSTHROUGH_ENV_VARS = (
    "HOME",
    "PATH",
    "TMPDIR",
    "USER",
    "SHELL",
    "LANG",
    "LC_ALL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "ZCODE_RUNTIME_ENV",
)

HOOK_EVENT = "PostToolUse"
HOOK_MATCHER = "Bash"
HOOK_TOOL_COMMAND_ECHO = "echo p3-hook-probe"
NONCE_PREFIX = "P3HOOK-"
PLUGIN_NAME = "p3-probe-hooks"
HOOK_VIA_CHOICES = ("plugin", "config")

PROBE_PROMPT = (
    "Use the Bash tool to run exactly: " + HOOK_TOOL_COMMAND_ECHO + "\n"
    "After the tool result you may receive an additional context note. "
    "Reply with the exact note text on its own final line. "
    "If you did not receive any additional context note, reply NO_NOTE."
)

HOOK_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""Probe PostToolUse hook: records bounded event metadata, injects a nonce.

Reads one hook JSON payload from stdin, appends a redacted record (no tool
input or tool response contents, only a hash/presence marker) to a log file,
and prints the additionalContext payload for the model turn.
"""
import hashlib
import json
import sys
import time

logfile, nonce, event = sys.argv[1], sys.argv[2], sys.argv[3]
run_token = sys.argv[4] if len(sys.argv) > 4 else ""
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
    parse_error = None
except Exception as exc:  # noqa: BLE001 - probe records any malformed payload
    payload, parse_error = {}, str(exc)
kept = {
    key: payload.get(key)
    for key in (
        "hook_event_name",
        "tool_name",
        "tool_use_id",
        "session_id",
        "transcript_path",
        "cwd",
        "permission_mode",
    )
}
tool_input = payload.get("tool_input")
kept["tool_input_sha256"] = (
    hashlib.sha256(json.dumps(tool_input, sort_keys=True, default=str).encode()).hexdigest()
    if tool_input is not None
    else None
)
kept["tool_response_present"] = payload.get("tool_response") is not None
record = {
    "nonce": nonce,
    "run_token": run_token,
    "parse_error": parse_error,
    "received_at": time.time(),
    **kept,
}
with open(logfile, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
json.dump(
    {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": "PROBE_NONCE=" + nonce,
        }
    },
    sys.stdout,
)
'''


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_tree(root: str) -> dict:
    """Map every file under ``root`` to its size and sha256 (symlinks recorded as such).

    Only digests and sizes are captured, never file contents, so snapshots of
    trees that contain credentials stay nonsecret.
    """
    root_path = Path(root)
    entries: dict = {}
    if not root_path.exists():
        return entries
    for current, _dirs, files in os.walk(root_path):
        for name in files:
            path = Path(current) / name
            rel = str(path.relative_to(root_path))
            if path.is_symlink():
                entries[rel] = {"symlink": os.readlink(path)}
            else:
                try:
                    entries[rel] = {"sha256": sha256_file(str(path)), "bytes": path.stat().st_size}
                except OSError as exc:
                    entries[rel] = {"error": str(exc)}
    return entries


def diff_snapshots(before: dict, after: dict) -> dict:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(
        path for path in set(before) & set(after) if before[path] != after[path]
    )
    return {"added": added, "removed": removed, "changed": changed}


def redact_text(text: str) -> str:
    """Scrub credential-shaped substrings from captured output before it is summarized."""
    patterns = (
        (re.compile(r"(?i)\b(?:sk|zai|xai)-[A-Za-z0-9._-]{12,}"), "<redacted-key>"),
        (re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"), "Bearer <redacted>"),
        (
            re.compile(
                r"(?i)(\"(?:api[_-]?key|apikey|token|secret|password|authorization)\"\s*[:=]\s*)\"[^\"]+\""
            ),
            r'\1"<redacted>"',
        ),
    )
    redacted = text
    for pattern, replacement in patterns:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def scrub_environment(env: dict | None = None) -> dict:
    """Copy ``env`` without the credential and virtual-environment variables."""
    source = dict(os.environ if env is None else env)
    return {key: value for key, value in source.items() if key not in CREDENTIAL_ENV_VARS}


def build_private_environment(work: Path, require_provider: bool) -> dict:
    """Minimal environment for a native CLI child with fully private state paths."""
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV_VARS if os.environ.get(key)}
    env["ZCODE_STORAGE_DIR"] = str(work / "storage")
    env["ZCODE_LOG_DIR"] = str(work / "log")
    env["ZCODE_LOG_CONSOLE"] = "0"
    env["ZCODE_SESSION_DB_PATH"] = str(work / "sessions.sqlite")
    for var in PROVIDER_ENV_VARS:
        value = os.environ.get(var)
        if value:
            env[var] = value
        elif require_provider:
            raise SystemExit(
                f"paid probe needs {var} in the environment "
                "(the harness-provided provider config for this attempt); "
                "refusing to run a native model prompt without it"
            )
    return env


def config_document(hook_script: str, hook_log: str, nonce: str, event: str, run_token: str) -> dict:
    return {
        "hooks": {
            "enabled": True,
            "timeoutMs": 20000,
            "events": {
                HOOK_EVENT: [
                    {
                        "matcher": HOOK_MATCHER,
                        "hooks": [
                            {
                                "type": "process",
                                "command": "/usr/bin/python3",
                                "args": [hook_script, hook_log, nonce, event, run_token],
                                "timeoutMs": 15000,
                            }
                        ],
                    }
                ]
            },
        }
    }


def assemble_workspace(work: Path, hook_via: str) -> dict:
    """Create the private storage, hook script, hook registration and empty cwd.

    ``plugin`` mode registers the hook through a workspace-scoped inline plugin
    (``<cwd>/.zcode/config.json`` ``plugins.dirs`` + plugin ``hooks/hooks.json``),
    which the native resolver enables by default. ``config`` mode writes the
    hooks block into ``<storage>/cli/config.json`` — the mode the paid run
    showed is not honored for hook loading, kept for reproducibility of that
    negative result.
    """
    storage = work / "storage"
    (storage / "cli").mkdir(parents=True, exist_ok=True)
    (work / "hook").mkdir(parents=True, exist_ok=True)
    (work / "log").mkdir(parents=True, exist_ok=True)
    (work / "cwd").mkdir(parents=True, exist_ok=True)
    hook_script = work / "hook" / "hook.py"
    hook_script.write_text(HOOK_SCRIPT_TEMPLATE, encoding="utf-8")
    hook_log = work / "hook-log.jsonl"
    run_token = "run-" + secrets.token_hex(8)
    plugin_root = None
    workspace_config = None
    if hook_via == "plugin":
        plugin_root = work / "plugin"
        (plugin_root / ".zcode-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_root / "hooks").mkdir(parents=True, exist_ok=True)
        manifest = {
            "name": PLUGIN_NAME,
            "version": "0.1.0",
            "description": "P3 probe-only plugin injecting a harmless PostToolUse nonce",
            "hooks": "hooks/hooks.json",
        }
        (plugin_root / ".zcode-plugin" / "plugin.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        workspace_config_dir = work / "cwd" / ".zcode"
        workspace_config_dir.mkdir(parents=True, exist_ok=True)
        workspace_config = workspace_config_dir / "config.json"
        workspace_config.write_text(
            json.dumps({"plugins": {"dirs": [str(plugin_root)]}}, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "storage": storage,
        "hook_script": hook_script,
        "hook_log": hook_log,
        "run_token": run_token,
        "hook_via": hook_via,
        "plugin_root": plugin_root,
        "workspace_config": workspace_config,
        "config": config_document(str(hook_script), str(hook_log), "", HOOK_EVENT, run_token)
        if hook_via == "config"
        else None,
    }


def write_plugin(parts: dict, nonce: str) -> Path:
    """Write the plugin hooks.json with the concrete nonce and per-run token."""
    hooks_doc = {
        "hooks": {
            HOOK_EVENT: [
                {
                    "matcher": HOOK_MATCHER,
                    "hooks": [
                        {
                            "type": "process",
                            "command": "/usr/bin/python3",
                            "args": [
                                str(parts["hook_script"]),
                                str(parts["hook_log"]),
                                nonce,
                                HOOK_EVENT,
                                parts["run_token"],
                            ],
                            "timeoutMs": 15000,
                        }
                    ],
                }
            ]
        }
    }
    hooks_json = parts["plugin_root"] / "hooks" / "hooks.json"
    hooks_json.write_text(json.dumps(hooks_doc, indent=2) + "\n", encoding="utf-8")
    return hooks_json


def write_config(work: Path, parts: dict, nonce: str) -> Path:
    config = json.loads(json.dumps(parts["config"]))
    hook_entry = config["hooks"]["events"][HOOK_EVENT][0]["hooks"][0]
    hook_entry["args"] = [
        str(parts["hook_script"]),
        str(parts["hook_log"]),
        nonce,
        HOOK_EVENT,
        parts["run_token"],
    ]
    config_path = parts["storage"] / "cli" / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def selftest_hook(parts: dict, nonce: str) -> dict:
    """Run the hook script directly with a sample payload (free, no native CLI)."""
    hook_log = parts["hook_log"]
    if hook_log.exists():
        hook_log.unlink()
    sample = {
        "hook_event_name": HOOK_EVENT,
        "tool_name": HOOK_MATCHER,
        "tool_use_id": "selftest",
        "session_id": "sess_selftest",
        "cwd": "/tmp",
        "tool_input": {"command": "echo hi"},
    }
    proc = subprocess.run(
        [
            "/usr/bin/python3",
            str(parts["hook_script"]),
            str(hook_log),
            nonce,
            HOOK_EVENT,
            parts["run_token"],
        ],
        input=json.dumps(sample),
        capture_output=True,
        text=True,
        env=scrub_environment(),
        timeout=30,
    )
    records = read_hook_log(hook_log)
    output_ok = False
    try:
        parsed = json.loads(proc.stdout)
        output_ok = (
            parsed.get("hookSpecificOutput", {}).get("hookEventName") == HOOK_EVENT
            and parsed["hookSpecificOutput"].get("additionalContext") == "PROBE_NONCE=" + nonce
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        output_ok = False
    return {
        "exit_code": proc.returncode,
        "stdout_is_valid_context": output_ok,
        "stderr_head": redact_text(proc.stderr[:400]),
        "log_records": records,
    }


def read_hook_log(hook_log: Path) -> list:
    if not hook_log.exists():
        return []
    records = []
    for line in hook_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"unparsable_line": redact_text(line[:200])})
    return records


def resolve_node(explicit: str | None) -> str:
    if explicit:
        return explicit
    for candidate in DEFAULT_NODE_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("node")
    if found:
        return found
    raise SystemExit("no node runtime found; pass --node")


def run_native(node: str, bundle: str, args: list, env: dict, work: Path, label: str, timeout: int) -> dict:
    started = time.time()
    stdout_path = work / f"{label}-stdout.txt"
    stderr_path = work / f"{label}-stderr.txt"
    argv = [node, bundle, *args]
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = subprocess.run(argv, stdout=out, stderr=err, env=env, timeout=timeout)
    duration_ms = int((time.time() - started) * 1000)
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    return {
        "label": label,
        "argv": argv,
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
        "stdout_file": str(stdout_path),
        "stderr_file": str(stderr_path),
        "stdout_head": redact_text(stdout_text[:2000]),
        "stderr_head": redact_text(stderr_text[:2000]),
        "_stdout_text": stdout_text,
        "_stderr_text": stderr_text,
    }


def session_ids_in(text: str) -> list:
    return sorted(set(re.findall(r"sess_[0-9a-fA-F-]{8,}", text)))


def summarize_prompt_result(run: dict, nonce: str) -> dict:
    combined = run["_stdout_text"] + "\n" + run["_stderr_text"]
    reply = ""
    turn_id = None
    try:
        parsed = json.loads(run["_stdout_text"])
        for key in ("result", "text", "response", "message"):
            value = parsed.get(key) if isinstance(parsed, dict) else None
            if isinstance(value, str) and value.strip():
                reply = value
                break
        if isinstance(parsed, dict):
            turn_id = parsed.get("turnId") or parsed.get("turn_id")
    except json.JSONDecodeError:
        parsed = None
    if not reply:
        # Defensive fallback: the nonce is the decision signal either way.
        reply = run["_stdout_text"][-1500:]
    return {
        "cli_session_ids": session_ids_in(combined),
        "turn_id": turn_id,
        "reply_contains_nonce": nonce in reply,
        "nonce_in_any_output": nonce in combined,
        "parsed_json_stdout": parsed is not None,
    }


def gather_versions(node: str, bundle: str) -> dict:
    versions: dict = {
        "node": subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout.strip(),
        "node_path": node,
        "bundle": bundle,
        "bundle_sha256": sha256_file(bundle),
        "platform": f"{platform.system()}/{platform.machine()}",
        "probe_version": PROBE_VERSION,
    }
    try:
        proc = subprocess.run([node, bundle, "--version"], capture_output=True, text=True, timeout=60)
        versions["cli"] = proc.stdout.strip() or proc.stderr.strip()
    except subprocess.TimeoutExpired:
        versions["cli"] = "timeout"
    if os.path.exists(DEFAULT_APP_PLIST):
        try:
            import plistlib

            with open(DEFAULT_APP_PLIST, "rb") as handle:
                plist = plistlib.load(handle)
            versions["app"] = str(
                plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion") or ""
            )
        except (OSError, ValueError):
            versions["app"] = "unreadable"
    return versions


def try_plugins_list(node: str, bundle: str, env: dict, work: Path, cwd: Path) -> dict:
    """Free check: `zcode plugins list` from the private cwd (option order tolerant)."""
    for argv in (
        ["--cwd", str(cwd), "plugins", "list"],
        ["plugins", "list", "--cwd", str(cwd)],
    ):
        run = run_native(node, bundle, argv, env, work, "plugins-list", 90)
        if run["exit_code"] == 0:
            return run
    return run


def run_probe(
    bundle: str,
    node: str | None,
    work_dir: str | None,
    paid: bool,
    timeout: int,
    global_root: str,
    hook_via: str = "plugin",
) -> dict:
    work = Path(work_dir).resolve() if work_dir else default_work_dir()
    if work.exists():
        raise SystemExit(f"work directory already exists, refusing to reuse: {work}")
    work.mkdir(parents=True)
    node = resolve_node(node)
    nonce = NONCE_PREFIX + secrets.token_hex(8)

    summary: dict = {
        "probe": "zcode-hooks-p3",
        "work_dir": str(work),
        "paid_run": paid,
        "hook_via": hook_via,
        "global_root": global_root,
        "nonce": nonce,
    }
    summary["versions"] = gather_versions(node, bundle)

    global_before = snapshot_tree(global_root)

    parts = assemble_workspace(work, hook_via)
    if hook_via == "config":
        config_path = write_config(work, parts, nonce)
        summary["private_config"] = {
            "path": str(config_path),
            "hooks_enabled": True,
            "event": HOOK_EVENT,
            "matcher": HOOK_MATCHER,
            "executor": "process",
        }
    else:
        hooks_json = write_plugin(parts, nonce)
        summary["private_plugin"] = {
            "root": str(parts["plugin_root"]),
            "hooks_json": str(hooks_json),
            "workspace_config": str(parts["workspace_config"]),
            "name": PLUGIN_NAME,
        }

    summary["hook_selftest"] = selftest_hook(parts, nonce)

    env_free = build_private_environment(work, require_provider=False)
    summary["version_run"] = {
        key: value
        for key, value in run_native(node, bundle, ["--version"], env_free, work, "version", 60).items()
        if not key.startswith("_")
    }
    list_run = try_plugins_list(node, bundle, env_free, work, work / "cwd")
    plugins_list_summary = {key: value for key, value in list_run.items() if not key.startswith("_")}
    if hook_via == "plugin":
        plugins_list_summary["plugin_name_listed"] = (
            list_run["exit_code"] == 0 and PLUGIN_NAME in list_run["_stdout_text"]
        )
    summary["plugins_list_run"] = plugins_list_summary

    if paid:
        # Keep only native-run evidence in the hook log: the self-test wrote its
        # own record, so truncate before the prompt run.
        parts["hook_log"].unlink(missing_ok=True)
        env_paid = build_private_environment(work, require_provider=True)
        run = run_native(
            node,
            bundle,
            ["--prompt", PROBE_PROMPT, "--cwd", str(work / "cwd"), "--json"],
            env_paid,
            work,
            "prompt",
            timeout,
        )
        summary["prompt_run"] = {key: value for key, value in run.items() if not key.startswith("_")}
        summary["observation"] = summarize_prompt_result(run, nonce)
        summary["hook_records"] = read_hook_log(parts["hook_log"])
        hook_sessions = sorted({r.get("session_id") for r in summary["hook_records"] if r.get("session_id")})
        cli_sessions = summary["observation"]["cli_session_ids"]
        summary["session_binding"] = {
            "cli_session_ids": cli_sessions,
            "hook_session_ids": hook_sessions,
            "match": bool(cli_sessions) and bool(hook_sessions) and set(hook_sessions).issubset(set(cli_sessions)),
            "turn_id": summary["observation"].get("turn_id"),
            "tool_use_ids": sorted({r.get("tool_use_id") for r in summary["hook_records"] if r.get("tool_use_id")}),
            "tool_input_sha256": sorted({r.get("tool_input_sha256") for r in summary["hook_records"] if r.get("tool_input_sha256")}),
            "run_token": parts["run_token"],
            "run_token_in_hook_records": bool(summary["hook_records"])
            and all(r.get("run_token") == parts["run_token"] for r in summary["hook_records"]),
        }

    global_after = snapshot_tree(global_root)
    diff = diff_snapshots(global_before, global_after)
    summary["global_root_unchanged"] = not (diff["added"] or diff["removed"] or diff["changed"])
    summary["global_root_diff"] = diff

    storage_files = sorted(
        str(path.relative_to(work)) for path in work.rglob("*") if path.is_file()
    )
    summary["private_work_files"] = storage_files
    summary["verdict"] = {
        "hook_selftest_ok": bool(summary["hook_selftest"]["stdout_is_valid_context"]),
        "global_root_unchanged": summary["global_root_unchanged"],
    }
    if hook_via == "plugin":
        summary["verdict"]["plugin_listed_free"] = bool(
            summary["plugins_list_run"].get("plugin_name_listed")
        )
    if paid:
        fired = bool(summary.get("hook_records"))
        summary["verdict"] = {
            **summary["verdict"],
            "hook_fired_in_native_run": fired,
            "nonce_in_model_turn": bool(summary["observation"]["reply_contains_nonce"]),
            "session_bindings_match": summary["session_binding"]["match"],
        }

    (work / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def default_work_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return repo_root / "tmp" / "zcode-hooks-probe" / stamp


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", default=DEFAULT_BUNDLE, help="native zcode.cjs bundle path")
    parser.add_argument("--node", default=None, help="node runtime path (auto-detected when omitted)")
    parser.add_argument("--work-dir", default=None, help="private work directory (default: tmp/zcode-hooks-probe/<ts>)")
    parser.add_argument("--global-root", default=DEFAULT_GLOBAL_ROOT, help="user state root to hash before/after")
    parser.add_argument("--timeout-seconds", type=int, default=240, help="wall-clock limit for the paid prompt run")
    parser.add_argument("--hook-via", choices=HOOK_VIA_CHOICES, default="plugin", help="register the hook via a workspace-scoped inline plugin (default) or via the private storage config.json hooks block")
    parser.add_argument("--paid", action="store_true", help="allow the small native model run (max two per task)")
    args = parser.parse_args(argv)

    if not os.path.exists(args.bundle):
        raise SystemExit(f"bundle not found: {args.bundle}")

    summary = run_probe(
        bundle=args.bundle,
        node=args.node,
        work_dir=args.work_dir,
        paid=args.paid,
        timeout=args.timeout_seconds,
        global_root=args.global_root,
        hook_via=args.hook_via,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
