# New-blackboard acceptance work item

This task must run through the new Python blackboard, using the installed or staged
new Buddy CLI. The coordinating Codex task will restart only that blackboard daemon
while you work, ask one correlated inquiry, and reconnect to the same run. Continue
this one task normally; do not start another Buddy run or restart any service.

Your working directory is the dedicated acceptance directory supplied by the caller.
Only create or modify files in that directory. The coordinator will place input.json
there before dispatch. Do not modify repository source, installed plugins, service
state or any unrelated process. This is an authorized integration test.

Write and execute one Python script via uv. The script must:

1. Append exactly one JSON `started` entry to execution.jsonl, including its own PID
   and UTC timestamp, flush/fsync it, and write live.json with the same identity.
2. Sleep for 90 seconds in this foreground tool call. This gives the coordinator a
   deterministic interval for daemon restart and the running-agent inquiry.
3. Read input.json as bytes. Compute the SHA-256 of those exact bytes, the length,
   sum, sum of squares, and sorted order of its `values` integer list.
4. Atomically write result.json with keys caseId, inputSha256, count, sum,
   sumOfSquares, sortedValues. Append one `finished` journal entry and exit.

Run the script exactly once. If already started during reconnect, observe its
existing foreground execution rather than rerun it. After the tool finishes,
answer any correlated Buddy inquiry through the provided buddy_inquiry_reply tool.
Then read result.json, confirm the calculation, and report its absolute path and
hash concisely. Ordinary final prose is not an inquiry answer.

The coordinator independently checks the artifact, execution journal, unchanged
attempt/worker identity, event replay, persisted inquiry and acceptance record.
