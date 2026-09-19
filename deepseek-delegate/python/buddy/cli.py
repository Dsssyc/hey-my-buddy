"""Structured CLI for the C-Two-backed local service (the supported Buddy entrypoint).

Everything the plugin offers is one ``buddy`` command: this CLI talks to the same
uv-managed C-Two service that owns the dsh runners, persists results and serves the
per-run inquiry bridge. There is no MCP server and no SDK dependency.
"""
import argparse
import json
from .transport import call_service

METHODS = ["health", "run", "await", "start", "status", "wait", "result", "list", "cancel", "acknowledge", "dashboard", "inquire", "stop"]

EPILOG = """\
examples:
  buddy run '{"requestId":"fix-123","task":"...","cwd":"/abs/path","timeoutSeconds":7200}'
      One-call convenience: start (or recover) one durable dsh run and stay connected
      until it finishes. Bounded by waitSeconds, which defaults to timeoutSeconds +
      60 s shutdown grace, capped at 86400 s (24 h). The execution deadline
      (timeoutSeconds, default 1800 s, 10..86400) is independent: if the wait window
      ends first you get an honest outcome=wait-timeout envelope and the run keeps
      going - recover it with the SAME requestId or with `buddy await`.

  buddy start '{"requestId":"fix-123","task":"...","cwd":"/abs/path"}'
      Default first step: start (or recover) the durable run and print its runId
      immediately. Keep the runId and await the SAME run in this turn:

  buddy await '{"runId":"<runId>"}'
      wait on an existing run without starting anything. Explicit and recoverable:
      waitSeconds defaults to 86400 (24 h) and may be set to any value in 1..86400.
      Reaching the window is never an execution failure and never relaunches work.

  buddy inquire '{"runId":"<runId>"}'
      read-only bounded progress for one owned run: authoritative execution
      state, elapsed/deadline, and the live agent/tool activity the run's private
      bridge can observe. Fields it cannot observe are named explicitly.

  buddy inquire '{"runId":"<runId>","inquiryId":"q1","question":"what is blocking you?"}'
      ask the SAME live owned dsh agent one question through its public input
      API. The question is queued at the agent's next step boundary; it never
      cancels, restarts or re-scopes the run.

  buddy inquire '{"runId":"<runId>","inquiryId":"q1","question":"what is blocking you?","waitMs":20000}'
      same, and wait up to waitMs (max 30000) for the correlated answer.

  buddy status '{"runId":"<runId>"}'   buddy result '{"runId":"<runId>"}'
  buddy cancel '{"runId":"<runId>"}'   buddy list '{"limit":20}'
  buddy acknowledge '{"runId":"<runId>","note":"inspected the diff and ran the checks"}'
      inspect the real artifacts first; acknowledgement records that a human/agent
      accepted the run, it never changes the execution status.

  buddy health   buddy dashboard   buddy stop
      service control. `buddy stop` stops this owned service and its active owned
      runs (their process groups are signalled) and never touches unrelated dsh
      sessions; complete results and durable records stay on disk.

  Repeating an inquiryId never injects the question twice; the same id with
  different text is an error.
"""


def _abandoned(abandoned, commands: list[str]) -> dict:
    return {
        "error": {"code": "WAIT_ABANDONED", "message": str(abandoned)},
        "runId": abandoned.run_id,
        "requestId": abandoned.request_id,
        "recovery": {
            "action": "The wait was abandoned; the owned run keeps executing and is not cancelled. Recover it with the same run.",
            "commands": commands,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Buddy service: C-Two IPC, dsh delegation, durable results and per-run inquiry",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("method", choices=METHODS)
    parser.add_argument("params", nargs="?", default="{}", help="JSON object")
    args = parser.parse_args()
    try:
        if args.method in ("run", "await"):
            # `run` starts or recovers a durable run and waits inside this one CLI
            # call (same wait-window rules for every caller). `await` only waits on
            # an already existing durable run and never launches work.
            from .blocking import WaitAbandoned, await_run, recovery_commands, run_blocking
            try:
                result = run_blocking(json.loads(args.params)) if args.method == "run" else await_run(json.loads(args.params))
            except WaitAbandoned as abandoned:
                commands = recovery_commands(abandoned.request_id, abandoned.run_id)
                print(json.dumps(_abandoned(abandoned, commands), ensure_ascii=False))
                raise SystemExit(1)
        else:
            result = call_service(args.method, json.loads(args.params))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({"error":{"code":getattr(error,"code","SERVICE_ERROR"),"message":str(error)}}))
        raise SystemExit(1)

if __name__ == "__main__": main()
