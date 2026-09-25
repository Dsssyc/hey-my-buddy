"""Explicitly approved, one-shot Claude P1 model-turn probes.

Default/--describe is offline. Every --run invocation requires fresh user approval
for its named mode, a native login, and the user's settings-policy choice. Never
automatically retry a failed or quota-rejected run.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

PLANS = {
    "structured": "One short structured outcome with nested anyOf; no file edits or shell work.",
    "permission": "Request an isolated project settings write, expect denial and attention; no global path is targeted.",
    "cancel": "Request a bounded sleep in the isolated worktree, then interrupt this owned attempt and confirm stop.",
    "sandbox": "In a disposable repository, check inside/outside writes and registry/non-registry HEAD requests.",
}


def git(cwd: Path, *args: str) -> str:
    command = ["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args]
    result = subprocess.run(command, text=True, capture_output=True, check=True, timeout=20)
    return result.stdout.strip()


def prompt(mode: str, nonce: str, outside: Path) -> str:
    if mode == "structured":
        return f"Protocol acceptance probe. Return a completed structured outcome whose summary is {nonce}. No file edits, Bash, network, subagents, or other work; use the supplied structured result mechanism."
    if mode == "permission":
        return ("Permission acceptance probe in a disposable worktree. Request writing the private project file "
                ".claude/settings.local.json with the harmless object {} using the native file-writing tool. "
                "This deliberately exercises Host approval. Do not bypass a refusal or try another tool. "
                f"If refused, return structured attention with summary {nonce}. Do not touch any global configuration.")
    if mode == "cancel":
        return ("Cancellation acceptance probe. Use the sandboxed Bash tool to run sleep 45 in this worktree, "
                f"then return completed summary {nonce}. Make no other file, network or subagent requests.")
    return ("Sandbox acceptance probe in a disposable worktree. With sandboxed Bash, write the word inside "
            "to probe-output.txt. Next attempt to write the word forbidden to the explicitly provided test "
            f"sentinel {outside}; it must be denied and its original contents retained. Then try HEAD requests "
            "with a five-second client timeout to https://registry.npmjs.org/ (registry allowlist) and "
            "https://example.com/ (must be denied). Do not disable the sandbox, request a new domain, retry "
            "a denial, or launch subagents. Report the actual observations and any inability to test as "
            f"structured attention/complete as appropriate, with summary containing {nonce}.")


def run(args) -> dict:
    from buddy import workspace
    from buddy.adapters.base import ExecutionContext
    from buddy.adapters.claude import ClaudeAdapter

    root = args.root.resolve()
    if root.exists():
        raise RuntimeError("The probe root already exists; preserve it and obtain approval before any new run")
    root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    env = {k:v for k,v in os.environ.items() if not k.startswith(("BUDDY_", "C2_"))
           and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PLUGIN_DATA")}
    env.update(BUDDY_STATE_DIR=str(root/"state"), BUDDY_RUNTIME_ROOT=str(root/"runtime"), BUDDY_DEV_SOURCE="1",
               BUDDY_CLAUDE_SETTINGS_POLICY=args.settings_policy, PYTHONPATH=str(REPO/"src"))
    if args.cli:
        env["BUDDY_CLAUDE_CLI"] = str(Path(args.cli).resolve())
    approval = {"approvalId":args.approved_run_id,"mode":args.mode,"model":args.model,"effort":args.effort,
                "settingsPolicy":args.settings_policy,"description":PLANS[args.mode]}
    (root/"approval.json").write_text(json.dumps(approval,indent=2)+"\n")
    (root/"approval.json").chmod(0o600)
    with patch.dict(os.environ, env, clear=True):
        adapter = ClaudeAdapter()
        available, reason = adapter.available()
        if not available:
            report={"started":False,"reason":reason,"modelMessagesSent":0,"approval":approval}
            (root/"report.json").write_text(json.dumps(report,indent=2)+"\n")
            return report
        native = adapter.discover_models()
        candidates=[m for p in native["providers"] if p["provider"]=="anthropic" for m in p["models"]]
        assert any(m["id"]==args.model and args.effort in m["efforts"] for m in candidates), "Model/effort absent from authenticated native catalog"
        source=root/"source";source.mkdir()
        git(source,"init","-q")
        (source/"README.md").write_text("Disposable Claude P1 protocol acceptance fixture.\n")
        git(source,"add","README.md")
        git(source,"-c","user.name=Buddy Probe","-c","user.email=probe@localhost","commit","-qm","fixture")
        nonce="claude-probe-"+uuid.uuid4().hex
        manifest=workspace.prepare(root/"state",nonce,{"kind":"worktree","cwd":str(source),"access":"write",
            "base":{"kind":"commit","ref":git(source,"rev-parse","HEAD")},"includeUntracked":[],
            "writeScope":["probe-output.txt"],"integrator":"claude-p1-probe"})
        outside=root/"outside-sentinel.txt";outside.write_text("original\n")
        identity={"taskId":nonce,"attemptId":nonce+"-attempt","generation":1,"turnId":nonce+"-turn"}
        turn_input={"version":1,**identity,"resumeMode":"initial","previousSessionId":None,"context":{},
                    "executionWorkspace":manifest}
        context=ExecutionContext(task_id=identity["taskId"],attempt_id=identity["attemptId"],generation=1,
            spec={"adapter":"claude","cwd":manifest["path"],"task":prompt(args.mode,nonce,outside),
                  "timeoutSeconds":180,"provider":"anthropic","model":args.model,"effort":args.effort},
            directory=root/"attempt",runtime={},environment=env,
            turn={"turnId":identity["turnId"],"input":turn_input})
        handle=None; cancel_sent=False
        try:
            handle=adapter.start(context)
            if args.mode=="cancel":
                deadline=time.monotonic()+45
                while handle.process.poll() is None and time.monotonic()<deadline:
                    sidecar=context.directory/"activity.json"
                    if sidecar.exists():
                        try:
                            phase=json.loads(sidecar.read_text()).get("activity",{}).get("phase")
                        except (ValueError,OSError):phase=None
                        if phase in ("waiting-model","streaming-model","tool-running"):
                            adapter.cancel(handle,grace_seconds=8);cancel_sent=True;break
                    time.sleep(.1)
            if handle.wait(195) is None:
                adapter.cancel(handle,grace_seconds=8)
            result=adapter.collect(handle,context)
            record=result.result.get("turn") or {}
            report={"started":True,"approval":approval,"status":result.status,
                    "shutdownConfirmed":result.shutdown_confirmed,"cancelRequested":cancel_sent,
                    "outsideUnchanged":outside.read_text()=="original\n",
                    "disposition":(record.get("outcome") or {}).get("disposition"),
                    "provenance":record.get("provenance"),"result":result.to_report()}
            (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
            (root/"report.json").chmod(0o600)
            return report
        finally:
            if handle is not None and not handle.shutdown_confirmed():
                adapter.cancel(handle,grace_seconds=8)
            # Keep the disposable repository, logs and receipts for Host inspection.


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe",action="store_true")
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--mode",choices=tuple(PLANS))
    parser.add_argument("--approved-run-id")
    parser.add_argument("--settings-policy",choices=("isolated",))
    parser.add_argument("--root",type=Path)
    parser.add_argument("--cli")
    parser.add_argument("--model",default="claude-sonnet-5")
    parser.add_argument("--effort",choices=("medium","high"),default="medium")
    args=parser.parse_args()
    if not args.run:
        print(json.dumps({"model":args.model,"effort":args.effort,"plans":PLANS,"modelCalls":0},ensure_ascii=False))
    else:
        if not args.approved_run_id or not args.mode or not args.root or not args.settings_policy:
            parser.error("A model turn requires --mode, a fresh --root, explicit --settings-policy and per-run --approved-run-id")
        os.umask(0o077)
        lock_path=REPO/".dsh-skill-build/claude-p1-probes.lock"
        lock_path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        lock_fd=os.open(lock_path,os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            parser.error("Another Claude probe already owns the single probe slot")
        def interrupted(_number,_frame):
            raise KeyboardInterrupt("Claude probe interrupted; stopping its owned controller")
        signal.signal(signal.SIGTERM,interrupted)
        try:
            report=run(args)
        finally:
            fcntl.flock(lock_fd,fcntl.LOCK_UN)
            os.close(lock_fd)
        print(json.dumps({k:report.get(k) for k in ("started","status","shutdownConfirmed","cancelRequested","outsideUnchanged","disposition","reason")}))
        raise SystemExit(0 if report.get("shutdownConfirmed") else 1)
