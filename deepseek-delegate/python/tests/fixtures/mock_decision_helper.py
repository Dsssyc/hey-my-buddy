#!/usr/bin/env python3
"""Deterministic stand-in for ``scripts/decision.mjs`` in focused Python tests.

It speaks exactly the helper's documented CLI and envelope —
``--input-file``, ``--output-file``, ``--timeout``, one JSON document in and one
JSON envelope out — so the Python adapter, the Worker, the receipt path and the
publication transaction are exercised for real without ever calling a model. The
behaviour is selected with ``MOCK_DECISION_MODE``; an unknown mode is a usage error,
never a silent success.
"""
import json
import os
import signal
import subprocess
import sys
import time

MODE = os.environ.get("MOCK_DECISION_MODE", "select_first")


def write_output(path, value):
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def supplied_evidence(request, profile_id):
    return [
        entry["evidenceId"]
        for entry in request.get("evidence", [])
        if entry.get("profileId") == profile_id
    ]


def select_envelope(request):
    profiles = request.get("profiles") or []
    if MODE == "abstain" or not profiles:
        return {"profileId": None, "reason": "no candidate is clearly better; defer to the Host", "evidenceIds": []}
    chosen = os.environ.get("MOCK_DECISION_PROFILE_ID") or profiles[0]["profileId"]
    evidence = supplied_evidence(request, chosen)
    style = os.environ.get("MOCK_DECISION_EVIDENCE", "first")
    if style == "none":
        cited = []
    elif style == "foreign":
        cited = ["ev-not-supplied"]
    elif style == "all":
        cited = evidence
    else:
        cited = evidence[:2]
    if MODE == "out_of_candidate":
        chosen = "dsh:not-a-candidate:model:off"
    return {"profileId": chosen, "reason": f"mock select chose {chosen}", "evidenceIds": cited}


def maintain_proposal(request):
    style = os.environ.get("MOCK_DECISION_CARD_MODE", "preserve")
    cards = request.get("cards") or []
    if style == "empty":
        return {"cards": [], "reason": "mock found nothing to update"}
    proposed = []
    for card in cards:
        entry = {
            "profileId": card["profileId"],
            "summary": f"mock maintenance summary for {card['profileId']}",
            "strengths": list(card.get("strengths") or []),
            "limitations": list(card.get("limitations") or []),
            "risks": list(card.get("risks") or []),
            "evidenceIds": list(card.get("evidenceIds") or []),
        }
        if style in ("preserve", "compact"):
            # Incorporation plus bounded compaction: the supplied pending evidence for
            # this profile is appended, then the reference window keeps only the newest
            # entries, so older references retire while risks and limitations stay.
            window = 64 if style == "preserve" else int(os.environ.get("MOCK_DECISION_WINDOW", "8"))
            merged_refs = list(card.get("evidenceIds") or []) + supplied_evidence(request, card["profileId"])
            entry["evidenceIds"] = list(dict.fromkeys(merged_refs))[-window:]
        if style == "cite_first_pending":
            pending_refs = supplied_evidence(request, card["profileId"])
            if pending_refs:
                entry["evidenceIds"] = [*entry["evidenceIds"], pending_refs[0]]
        if style == "no_op":
            entry["summary"] = card.get("summary") or "unchanged"
        if style == "drop_risk" and entry["risks"]:
            entry["risks"] = entry["risks"][1:]
        if style == "drop_limitation" and entry["limitations"]:
            entry["limitations"] = entry["limitations"][1:]
        if style == "drop_evidence" and entry["evidenceIds"]:
            entry["evidenceIds"] = entry["evidenceIds"][1:]
        if style == "foreign_evidence":
            entry["evidenceIds"] = [*entry["evidenceIds"], "ev-not-supplied"]
        if style == "extra_field":
            entry["sampleCount"] = 99
        if style == "noisy" and entry["summary"]:
            entry["summary"] = entry["summary"] * 40
        proposed.append(entry)
    if style == "propose_new" and profiles_available(request):
        for request_profile in request["profiles"]:
            if request_profile["profileId"] not in {card["profileId"] for card in cards}:
                proposed.append(
                    {
                        "profileId": request_profile["profileId"],
                        "summary": "mock proposed a new card from pending evidence",
                        "strengths": [],
                        "limitations": [],
                        "risks": [],
                        "evidenceIds": supplied_evidence(request, request_profile["profileId"])[:1],
                    }
                )
                break
    if style == "config_change":
        return {"cards": proposed, "reason": "mock", "configuration": {"autoMaintain": False}}
    return {"cards": proposed, "reason": "mock maintenance preserved risks and references"}


def profiles_available(request):
    return bool(request.get("profiles"))


def main(argv):
    values = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token not in ("--input-file", "--output-file", "--timeout"):
            sys.stderr.write(f"usage: unknown option {token}\n")
            return 2
        if index + 1 >= len(argv):
            sys.stderr.write(f"usage: {token} requires a value\n")
            return 2
        values[token] = argv[index + 1]
        index += 2
    if "--input-file" not in values or "--output-file" not in values:
        sys.stderr.write("usage: --input-file and --output-file are required\n")
        return 2
    try:
        timeout = int(values.get("--timeout", "120"))
    except ValueError:
        sys.stderr.write("usage: --timeout must be an integer\n")
        return 2
    if not 5 <= timeout <= 1800:
        sys.stderr.write("usage: --timeout is out of range\n")
        return 2
    try:
        with open(values["--input-file"], encoding="utf-8") as stream:
            request = json.load(stream)
    except (OSError, ValueError):
        sys.stderr.write("input-unreadable\n")
        return 1
    output = values["--output-file"]
    if MODE == "no_output":
        return 0
    if MODE == "sleep":
        # The real helper owns a detached child group; a survivor makes "the group is
        # gone" an insufficient stop claim, which is what the adapter must not accept.
        if os.environ.get("MOCK_DECISION_SURVIVOR") == "1":
            subprocess.Popen(["sleep", "30"], start_new_session=True)
        time.sleep(float(os.environ.get("MOCK_DECISION_SLEEP", "30")))
        return 0
    if MODE == "malformed":
        with open(output, "w", encoding="utf-8") as stream:
            stream.write("{not json")
        return 0
    if MODE == "error":
        write_output(
            output,
            {
                "status": "error",
                "operation": request.get("operation"),
                "tableRevision": request.get("tableRevision"),
                "code": "call-timeout",
                "message": "mock helper timed out inside the model call",
                "elapsedSeconds": 1.0,
                "shutdownConfirmed": True,
            },
        )
        return 3
    if MODE in ("no_shutdown", "string_shutdown"):
        envelope = {
            "status": "ok",
            "operation": request["operation"],
            "tableRevision": request["tableRevision"],
            "requested": request.get("profile"),
            "resolved": None,
            "observed": None,
            "usage": None,
            "elapsedSeconds": 0.1,
            "shutdownConfirmed": "false" if MODE == "string_shutdown" else False,
        }
        envelope["decision" if request["operation"] == "select" else "proposal"] = (
            select_envelope(request) if request["operation"] == "select" else maintain_proposal(request)
        )
        write_output(output, envelope)
        return 0
    envelope = {
        "status": "ok",
        "operation": "maintain" if request["operation"] == "select" and MODE == "wrong_operation" else request["operation"],
        "tableRevision": request["tableRevision"] + (5 if MODE == "wrong_revision" else 0),
        "requested": request.get("profile"),
        "resolved": {**(request.get("profile") or {}), "reasoningEffort": (request.get("profile") or {}).get("effort")},
        "observed": None,
        "usage": {"inputTokens": 10, "outputTokens": 5},
        "elapsedSeconds": 0.2,
        "shutdownConfirmed": True,
    }
    if request["operation"] == "select":
        envelope["decision"] = select_envelope(request)
    else:
        envelope["proposal"] = maintain_proposal(request)
    write_output(output, envelope)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    sys.exit(main(sys.argv[1:]))
