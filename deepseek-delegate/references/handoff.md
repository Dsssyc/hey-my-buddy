# Resume the owner after delegation

Choose by available capabilities, not the executable's location or the app name.
`codex` bundled inside ChatGPT.app does not establish connectivity to its tasks.
These routes implement transport and durable results; neither asserts task correctness.

## App / ChatGPT App

Use the App's heartbeat automation tool when available. Shell scripts cannot call
`send_message_to_thread` merely because that tool is available to the agent.

1. Choose an absolute, new `run-dir` under a persistent private directory (for
   example `~/.local/state/deepseek-delegate/<unique-name>`). Do not create the
   leaf directory: the wrapper creates it exclusively to prevent duplicate runs.
   Resolve the exact original thread ID from trusted task context or App tools.
2. Create an active heartbeat targeting that original task, using the currently
   exposed automation tool schema. A five-minute check interval is usually enough.
   Verify the returned automation ID, target thread, and active status. If creation
   fails, continue waiting in the current turn; do not claim asynchronous handoff.
3. Include the absolute `run-dir`, thread ID, original goal and acceptance criteria
   in the heartbeat prompt. Use the behavior below, substituting concrete paths:

   > Inspect run.json, result.json, and notification.json in RUN_DIR. Missing files
   > immediately after registration mean startup may still be pending; do not launch
   > another dsh. If still missing on a later check, inspect RUN_DIR.launcher.log and
   > report a startup problem. While the supervisor is running and no terminal result
   > exists, stay quiet. If the recorded process disappears without a result, report
   > interrupted/unknown execution; inspect existing dsh activity before any retry.
   > If executionConfirmedStopped is false, first inspect the recorded runner/dsh
   > activity; a dead runner does not establish that detached dsh work stopped.
   > When result.json exists, deduplicate by runId and resume the original task in
   > this thread. Read runner errors and real artifacts, independently verify every
   > acceptance criterion, and continue the original goal. A result's success only
   > means the runner succeeded. Notify only on completion, failure, or required user
   > action. Do not restart the delegation or edit alongside a still-running dsh.
   > After verification and handling the result, run handoff.mjs --run-dir RUN_DIR
   > --accept, then pause/delete this heartbeat. If acceptedAt is already present,
   > only close the heartbeat. For an unresolved failure, report it and pause the
   > heartbeat to avoid repeated notifications; do not mark it accepted as a success.

4. Start the wrapper, passing the verified automation ID:

   ```sh
   node <skill-dir>/scripts/handoff.mjs --background \
     --run-dir <absolute-new-run-dir> --thread <original-thread-id> \
     --mode app --heartbeat-id <verified-automation-id> -- \
     --cwd <workspace> --task-file <packet-file>
   ```

5. Confirm startup output and inspect `run.json` (or `result.json` for fast runs).
   Only then may the current turn end. If startup fails, inspect the launcher log
   and close/update the heartbeat. Do not leave an orphan monitor or start duplicates.

No live App heartbeat is automatically created by this script: tool registration
is the agent's job. `--heartbeat-id` records its verified ID, not proof by itself.
Recheck runtime tool availability whenever routing a new delegation.

## CLI

Before choosing CLI callbacks, verify that the selected binary/endpoint manages
the original thread and that queuing a harmless test message actually resumes
that idle thread. Use the available thread status interface to observe the new
turn. Do this only within the user's authorized scope and avoid perturbing an
unrelated active task. `--help`, a daemon version, and queue exit 0 alone are not
a wakeup test. Do not start a second app-server to imitate the App's original one.

If this cannot be verified, use the App route if available; otherwise keep the
current turn waiting on `run.mjs`. Do not silently start a background CLI handoff.

```sh
node <skill-dir>/scripts/handoff.mjs --background \
  --run-dir <absolute-new-run-dir> --thread <original-thread-id> \
  --mode cli --codex-bin <verified-absolute-codex-path> \
  --cli-wakeup-verified --remote <verified-endpoint> -- \
  --cwd <workspace> --task-file <packet-file>
```

Omit `--remote` only if the default endpoint was verified. The flag
`--cli-wakeup-verified` records the operator/agent's verification; it does not run
a probe. Currently this adapter implements `codex queue` only; it does not implement
a separate App Server RPC client. Endpoints requiring additional CLI authentication
flags are not supported by this adapter; retain foreground waiting in that case.

After run.mjs exits (including workspace finalization), the wrapper atomically saves
`result.json`, then executes `codex queue --thread ID --message TEXT` with an argument
array. The message contains the run ID and paths, not task logs or credentials.
Queue calls time out after 15 seconds. Failed delivery receives at most three total
attempts with bounded delays, then remains `pending`. Exit 0 is recorded as `queued`,
not as proof of reception or acceptance. A timeout can mean delivery is unknown;
the receiving agent must deduplicate by run ID before handling any repeated message.

Retry delivery without rerunning dsh:

```sh
node <skill-dir>/scripts/handoff.mjs --run-dir <existing-run-dir> --retry-notification
```

A retry performs at most one queue attempt. A queued or accepted run is not sent
again. Concurrent notification attempts are rejected by an exclusive lock. If a
supervisor was killed with SIGKILL while delivering, inspect its recorded PID and
delivery state before manually removing a stale `notification.lock`; never assume
that a missing acknowledgement means the original message was not delivered.

After independent verification and handling, acknowledge the run:

```sh
node <skill-dir>/scripts/handoff.mjs --run-dir <existing-run-dir> --accept
```

This records consumption, not a test verdict. Preserve the actual failure/success
in result.json and the owner's report. Do not accept merely because dsh exited 0.

## Files and lifetime

- `run.json`: schema version, run ID, owner thread, route, supervisor/runner PIDs, heartbeat
  or CLI connection details, terminal status and optional acceptance timestamp.
- `result.json`: terminal runner JSON, supervisor outcome and log paths. Preflight
  failures without runner JSON become `runner-error`. dsh nonzero, timeout and
  cancellation are terminal results too. `executionConfirmedStopped` is separate:
  a killed runner may leave detached dsh alive. Notification failure never changes them.
- `notification.json`: heartbeat mode or CLI pending/sending/queued state and attempt count.
- `runner.stdout.log` and `runner.stderr.log`: private wrapper-child output; underlying
  dsh logs default to `dsh-logs` inside this run directory unless `--log-dir` is supplied.
  The sibling `.launcher.log` records detached
  supervisor startup/final output. Do not paste raw logs or credentials into notifications.

Files are atomically replaced with mode 0600 in a 0700 run directory. Reusing a
run directory is rejected. `--background` launches a detached supervisor with file
stdio, so the launching tool can return without owning its pipes. The supervisor
still needs a running machine; it is not a reboot-persistent service. SIGTERM/SIGINT
are forwarded for controlled cancellation. SIGKILL, reboot or disk failure cannot
guarantee a terminal file; missing results are unknown, never success. App heartbeat
also needs the App's scheduler to be available. No global hooks or daemon config
are installed, and existing in-flight delegations are not retrofitted automatically.
