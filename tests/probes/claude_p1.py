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


def process_identity(pid: int) -> dict | None:
    """Diagnostic only: never signal a process by a PID read from a marker."""
    result = subprocess.run(["ps", "-p", str(pid), "-o", "pid=,ppid=,lstart="],
                            capture_output=True, text=True, timeout=5)
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return None
    parts = result.stdout.split(maxsplit=2)
    if result.returncode or len(parts) != 3 or int(parts[0]) != pid:
        raise RuntimeError("The probe process could not be inspected")
    return {"pid": pid, "parentPid": int(parts[1]), "started": " ".join(parts[2].split())}


def belongs_to_controller(pid: int, controller_pid: int) -> bool:
    seen = set()
    for _ in range(32):
        if pid == controller_pid:
            return bool(seen)
        if pid <= 1 or pid in seen:
            return False
        seen.add(pid)
        identity = process_identity(pid)
        if identity is None:
            return False
        pid = identity["parentPid"]
    return False


def prompt(mode: str, nonce: str, outside: Path) -> str:
    if mode == "structured":
        return f"Protocol acceptance probe. Return a completed structured outcome whose summary is {nonce}. No file edits, Bash, network, subagents, or other work; use the supplied structured result mechanism."
    if mode == "permission":
        return ("Permission acceptance probe in a disposable worktree. Request writing the private project file "
                ".claude/settings.local.json with the harmless object {} using the native file-writing tool. "
                "This deliberately exercises Host approval. Do not bypass a refusal or try another tool. "
                f"If refused, return structured attention with summary {nonce}. Do not use Bash or touch any global configuration.")
    if mode == "cancel":
        return ("Cancellation acceptance probe. Use the sandboxed Bash tool to run exactly "
                "`echo $$ > probe-output.txt; sleep 45` in this worktree (write the shell PID, then wait). "
                f"then return completed summary {nonce}. Make no other file, network or subagent requests.")
    return ("Sandbox acceptance probe in a disposable worktree. Using sandboxed Bash, run exactly "
            "`python3 sandbox-check.py` once. The Host-prepared immutable script tests an inside write, "
            "one write to a Host-owned outside sentinel, and short HEAD requests to registry.npmjs.org "
            "and example.com. Do not edit the script/input or manufacture observations; do not disable "
            "the sandbox, request new domains, retry, or launch subagents. Then return the supplied "
            f"structured outcome with summary containing {nonce} and report the actual observations.")


def run(args) -> dict:
    from buddy import workspace
    from buddy.adapters.base import ExecutionContext
    from buddy.adapters.claude import ClaudeAdapter
    from buddy.adapters.claude_config import cli_command, native_environment

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
        # This probe is explicitly for the user's subscription. Do not silently
        # spend a separately billed API key after subscription approval.
        if env.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("A subscription probe refuses inherited ANTHROPIC_API_KEY")
        auth_check = subprocess.run([*cli_command(env), "auth", "status", "--json"],
                                    env=native_environment(env), capture_output=True, text=True, timeout=15)
        auth = json.loads(auth_check.stdout)
        if auth_check.returncode or auth.get("loggedIn") is not True or auth.get("subscriptionType") not in ("pro", "max"):
            report = {"started": False, "reason": "Authenticate the native CLI with the approved Pro/Max subscription first",
                      "modelMessagesSent": 0, "approval": approval}
            (root/"report.json").write_text(json.dumps(report,indent=2)+"\n")
            return report
        adapter = ClaudeAdapter()
        available, reason = adapter.available()
        if not available:
            report={"started":False,"reason":reason,"modelMessagesSent":0,"approval":approval}
            (root/"report.json").write_text(json.dumps(report,indent=2)+"\n")
            return report
        native = adapter.discover_models()
        candidates=[m for p in native["providers"] if p["provider"]=="anthropic" for m in p["models"]]
        assert any(m["id"]==args.model and args.effort in m["efforts"] for m in candidates), "Model/effort absent from authenticated native catalog"
        nonce="claude-probe-"+uuid.uuid4().hex
        outside=root/"outside-sentinel.txt";outside.write_text("original\n")
        source=root/"source";source.mkdir()
        git(source,"init","-q")
        (source/"README.md").write_text("Disposable Claude P1 protocol acceptance fixture.\n")
        if args.mode=="sandbox":
            (source/"sandbox-check.py").write_bytes((REPO/"tests/probes/claude_sandbox_check.py").read_bytes())
            (source/"sandbox-input.json").write_text(json.dumps({"outside":str(outside),"nonce":nonce})+"\n")
        git(source,"add",".")
        git(source,"-c","user.name=Buddy Probe","-c","user.email=probe@localhost","commit","-qm","fixture")
        manifest=workspace.prepare(root/"state",nonce,{"kind":"worktree","cwd":str(source),
            "access":"read" if args.mode=="structured" else "write",
            "base":{"kind":"commit","ref":git(source,"rev-parse","HEAD")},"includeUntracked":[],
            "writeScope":(["probe-output.txt","probe-observations.json"] if args.mode=="sandbox" else
                          [".claude/settings.local.json" if args.mode=="permission" else "probe-output.txt"]),
            "integrator":"claude-p1-probe"})
        if args.mode=="permission":
            (Path(manifest["path"])/".claude").mkdir(exist_ok=True)
        identity={"taskId":nonce,"attemptId":nonce+"-attempt","generation":1,"turnId":nonce+"-turn"}
        turn_input={"version":1,**identity,"resumeMode":"initial","previousSessionId":None,"context":{},
                    "executionWorkspace":manifest}
        context=ExecutionContext(task_id=identity["taskId"],attempt_id=identity["attemptId"],generation=1,
            spec={"adapter":"claude","cwd":manifest["path"],"task":prompt(args.mode,nonce,outside),
                  "timeoutSeconds":180,"provider":"anthropic","model":args.model,"effort":args.effort},
            directory=root/"attempt",runtime={},environment=env,
            turn={"turnId":identity["turnId"],"input":turn_input})
        handle=None; cancel_sent=False; probe_child=None; child_bound=False
        try:
            handle=adapter.start(context)
            if args.mode=="cancel":
                deadline=time.monotonic()+45
                while handle.process.poll() is None and time.monotonic()<deadline:
                    marker=Path(manifest["path"])/"probe-output.txt"
                    if marker.exists():
                        try:
                            with marker.open("rb") as stream:
                                value=stream.read(128).strip()
                            pid=int(value) if value.isdigit() and len(value)<=10 else 0
                            candidate=process_identity(pid) if pid>1 else None
                            child_bound=bool(candidate and belongs_to_controller(pid,handle.process.pid))
                        except (ValueError,OSError):child_bound=False
                        if child_bound:
                            probe_child=candidate
                            adapter.cancel(handle,grace_seconds=8);cancel_sent=True;break
                    time.sleep(.1)
                if handle.process.poll() is None and not cancel_sent:
                    # End the authorized probe if the required live marker never
                    # became observable. Cancellation alone cannot make it pass.
                    adapter.cancel(handle,grace_seconds=8);cancel_sent=True
            if handle.wait(195) is None:
                adapter.cancel(handle,grace_seconds=8)
            result=adapter.collect(handle,context)
            record=result.result.get("turn") or {}
            provenance=record.get("provenance") or {}
            outcome=record.get("outcome") or {}
            checked = result.shutdown_confirmed
            sandbox_observations=None; sandbox_checks=None
            if args.mode == "structured":
                checked = checked and result.status == "ok" and outcome.get("disposition") == "completed" \
                    and outcome.get("summary") == nonce and provenance.get("structuredOutputValidated") is True
            elif args.mode == "permission":
                checked = checked and result.status == "ok" and outcome.get("disposition") == "attention" \
                    and result.result.get("attentionRequired") is True
            elif args.mode == "cancel":
                child_absent=bool(probe_child and process_identity(probe_child["pid"]) is None)
                checked = checked and cancel_sent and result.status == "cancelled" and not record \
                    and result.result.get("nativeInterruptAcknowledged") is True and child_bound and child_absent
            else:
                try:
                    sandbox_observations=json.loads((Path(manifest["path"])/"probe-observations.json").read_text())
                    registry=sandbox_observations["registry"]; blocked=sandbox_observations["nonRegistry"]
                    sandbox_checks={
                        "insideWrite":sandbox_observations["insideWritten"] is True and
                            (Path(manifest["path"])/"probe-output.txt").read_text()=="inside\n",
                        "outsideDenied":sandbox_observations["outsideDenied"] is True and outside.read_text()=="original\n",
                        "registryReachable":registry["observed"] is True and registry["exitCode"]==0 and
                            200<=registry["httpStatus"]<400 and registry["tlsVerify"]==0,
                        "nonRegistryDenied":blocked["observed"] is True and blocked["exitCode"]!=0 and
                            blocked["httpStatus"]==0 and blocked["connectStatus"]==403,
                    }
                    checked = checked and result.status=="ok" and sandbox_observations["nonce"]==nonce \
                        and all(sandbox_checks.values())
                except (OSError,ValueError,KeyError,TypeError):
                    checked = False
            report={"started":True,"approval":approval,"status":result.status,
                    "probeChecksPassed":bool(checked),"requiresHostInspection":args.mode=="sandbox",
                    "sandboxObservations":sandbox_observations,"sandboxChecks":sandbox_checks,
                    "shutdownConfirmed":result.shutdown_confirmed,"cancelRequested":cancel_sent,
                    "probeChild":probe_child,"probeChildBoundBeforeCancel":child_bound,
                    "probeChildAbsentAfterStop":child_absent if args.mode=="cancel" else None,
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
        print(json.dumps({k:report.get(k) for k in ("started","status","shutdownConfirmed","probeChecksPassed","requiresHostInspection","cancelRequested","outsideUnchanged","disposition","reason")}))
        raise SystemExit(0 if report.get("probeChecksPassed") else 1)
