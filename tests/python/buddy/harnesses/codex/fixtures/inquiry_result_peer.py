#!/usr/bin/env python3
"""One silent Codex App Server turn that answers only after an in-turn steer."""

import json
import sys


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


steer = json.loads(sys.stdin.readline())
assert steer["method"] == "turn/steer"
assert steer["params"]["expectedTurnId"] == "native-turn-1"
inquiry_id = json.loads(steer["params"]["input"][0]["text"].splitlines()[-1])["inquiryId"]
send({"id": steer["id"], "result": {"turnId": "native-turn-1"}})
send({"id": "answer-call", "method": "item/tool/call", "params": {
    "threadId": "native-thread-1", "turnId": "native-turn-1", "callId": "answer-call",
    "tool": "hey_my_buddy_answer_inquiry",
    "arguments": {"inquiryId": inquiry_id, "answer": "The work is complete."},
}})
reply = json.loads(sys.stdin.readline())
assert reply["id"] == "answer-call" and reply["result"]["success"] is True
