"""Native metadata probe: initialize only; never send a user/model message.

This consumes no model turn. Model-turn probes are separate and require the
user's explicit approval for each run. Output contains no credentials/account ID.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from hey_my_buddy.buddy.harnesses.base import ProcessHandle


def run_probe(root: Path, cli: str) -> dict:
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    settings, mcp = root / "settings.json", root / "mcp.json"
    settings.write_text("{}\n"); mcp.write_text('{"mcpServers":{}}\n')
    settings.chmod(0o600); mcp.chmod(0o600)
    keep = {"HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL",
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
            "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy",
            "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS"}
    forbidden = {k for k,v in os.environ.items() if v and (
        (k.startswith("ANTHROPIC_") and ("BASE_URL" in k or k=="ANTHROPIC_CUSTOM_HEADERS"))
        or k in {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"})}
    if forbidden:
        raise RuntimeError("Provider overrides must be resolved before probing: " + ", ".join(sorted(forbidden)))
    env = {k:v for k,v in os.environ.items() if k in keep}
    env.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1")
    version = subprocess.run([cli,"--version"], env=env, text=True, capture_output=True, timeout=5, check=True).stdout.strip()
    argv = [cli,"-p","--input-format","stream-json","--output-format","stream-json","--verbose",
            "--safe-mode","--no-session-persistence","--strict-mcp-config","--mcp-config",str(mcp),
            "--settings",str(settings),"--setting-sources","","--tools","","--no-chrome",
            "--permission-prompt-tool","stdio","--session-id",str(uuid.uuid4())]
    messages: queue.Queue = queue.Queue(maxsize=128)
    stderr_path = root / "stderr.log"
    request_id = str(uuid.uuid4())
    request = {"type":"control_request","request_id":request_id,"request":{"subtype":"initialize","hooks":None}}
    with stderr_path.open("wb") as err:
        process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=err, start_new_session=True, close_fds=True)
    stderr_path.chmod(0o600)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    def read():
        try:
            while raw := process.stdout.readline(8*1024*1024+1):
                if len(raw)>8*1024*1024:raise ValueError("oversized native frame")
                messages.put(json.loads(raw))
        except Exception as e:messages.put(e)
        finally:messages.put(None)
    threading.Thread(target=read,daemon=True).start()
    frames=[]; metadata=None
    try:
        process.stdin.write((json.dumps(request)+"\n").encode());process.stdin.flush()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try: frame=messages.get(timeout=min(.2,max(.001,deadline-time.monotonic())))
            except queue.Empty:continue
            if frame is None:raise RuntimeError("native stream ended before initialize response")
            if isinstance(frame,Exception):raise frame
            frames.append({"type":frame.get("type"),"subtype":frame.get("subtype")})
            if frame.get("type")=="control_response":
                response=frame.get("response") or {}
                if response.get("request_id")==request_id:
                    if response.get("subtype")!="success":raise RuntimeError("native initialize was refused")
                    metadata=response.get("response");break
            if frame.get("type") in {"assistant","user","result","rate_limit_event"}:
                raise RuntimeError("Unexpected model event during metadata-only discovery")
        if not isinstance(metadata,dict):raise RuntimeError("initialize metadata unavailable")
    finally:
        process.stdin.close()
        if handle.wait(5) is None:handle.terminate(grace_seconds=3)
        process.stdout.close()
    stopped=handle.shutdown_confirmed()
    account=metadata.get("account") or {}
    models=[{k:item.get(k) for k in ("value","resolvedModel","displayName","supportsEffort","supportedEffortLevels")}
            for item in metadata.get("models",[]) if isinstance(item,dict)]
    report={"version":version,"metadataOnly":True,"modelMessagesSent":0,"hostRequestSubtypes":["initialize"],
            "account":{k:account.get(k) for k in ("apiProvider","tokenSource")},"models":models,
            "observedFrameTypes":frames,"exitCode":process.returncode,"shutdownConfirmed":stopped}
    (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    (root/"report.json").chmod(0o600)
    if not stopped:raise RuntimeError("native metadata process group shutdown unconfirmed")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--cli",default=shutil.which("claude"))
    args=parser.parse_args()
    if not args.cli:parser.error("Claude CLI is unavailable")
    print(json.dumps(run_probe(args.root.resolve(),args.cli),ensure_ascii=False))
