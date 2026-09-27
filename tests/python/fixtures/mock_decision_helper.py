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


def stated_policy_check(request, profile_id):
    """The typed policy acknowledgment for one selection, from the input facts."""
    facts = request.get("policyFacts") or {}
    task = facts.get("taskPreference") or {}
    rule_index = task.get("ruleIndex")
    if rule_index is None:
        outcome = "fallback" if request.get("routingPreferences") else "none"
    elif profile_id in (task.get("matchingProfileIds") or []):
        outcome = "matched"
    else:
        outcome = "alternative"
    user_preferred = facts.get("userPreferredProfileIds") or []
    if not user_preferred:
        user = "none"
    elif profile_id in user_preferred:
        user = "matched"
    else:
        user = "alternative"
    return {
        "hardConstraints": facts.get("hardConstraints") or {},
        "taskPreference": {"ruleIndex": rule_index, "outcome": outcome},
        "userPreference": user,
    }


def stated_support(request, profile_id, policy_check, cited):
    """Eligible support for an alternative; empty unless one is required."""
    support = {"cardProfileIds": [], "annotationProfileIds": []}
    if "alternative" not in (policy_check["taskPreference"]["outcome"], policy_check["userPreference"]) or cited:
        return support
    facts = request.get("policyFacts") or {}
    scoped = {profile_id, *(facts.get("taskPreference") or {}).get("matchingProfileIds", []),
              *facts.get("userPreferredProfileIds", [])}
    for key, collection in (("annotationProfileIds", "annotations"), ("cardProfileIds", "cards")):
        seen = []
        for entry in request.get(collection) or []:
            candidate = entry.get("profileId")
            if candidate in scoped and candidate not in seen:
                seen.append(candidate)
            if len(seen) >= 32:
                break
        support[key] = seen
        if seen:
            break
    return support


def select_envelope(request):
    profiles = request.get("profiles") or []
    if MODE == "policy_bad_abstention":
        # A malformed abstention: profileId null but evidence cited. Python must
        # refuse it at publication and settle the decision needs-host.
        return {
            "profileId": None,
            "reason": "no candidate is clearly better; defer to the Host",
            "evidenceIds": ["ev-not-allowed-for-an-abstention"],
            "policyCheck": None,
            "support": {"cardProfileIds": [], "annotationProfileIds": []},
        }
    if MODE == "abstain" or not profiles:
        return {
            "profileId": None,
            "reason": "no candidate is clearly better; defer to the Host",
            "evidenceIds": [],
            "policyCheck": None,
            "support": {"cardProfileIds": [], "annotationProfileIds": []},
        }
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
    if MODE == "policy_input_echo":
        policy_check = stated_policy_check(request, chosen)
        policy_check["taskPreference"] = {**request["policyFacts"]["taskPreference"], "outcome": "matched"}
    elif MODE == "policy_false_fallback":
        # The historical inversion: a legal preference match reported as if no
        # rule matched. Python must settle this needs-host, never adopt it.
        policy_check = {"hardConstraints": {}, "taskPreference": {"ruleIndex": None, "outcome": "fallback"}, "userPreference": "none"}
    elif MODE == "policy_invented_constraint":
        # A constraint invented from outside the request (e.g. from a filename).
        policy_check = {"hardConstraints": {"adapter": "dsh"}, "taskPreference": {"ruleIndex": None, "outcome": "none"}, "userPreference": "none"}
    elif MODE == "policy_bad_index":
        # A typed index that JSON allows as a value but the contract forbids:
        # False (bool), 0.0 (float) or "0" (string) where an integer belongs.
        policy_check = stated_policy_check(request, chosen)
        malformed = {"false": False, "float": 0.0, "string": "0"}
        key = os.environ.get("MOCK_DECISION_RULE_INDEX", "false")
        policy_check["taskPreference"] = {
            "ruleIndex": malformed[key],
            "outcome": policy_check["taskPreference"]["outcome"],
        }
    else:
        policy_check = stated_policy_check(request, chosen)
    support = stated_support(request, chosen, policy_check, cited)
    if MODE == "policy_unsupported_alternative":
        support = {"cardProfileIds": [], "annotationProfileIds": []}
    return {
        "profileId": chosen,
        "reason": f"mock select chose {chosen}",
        "evidenceIds": cited,
        "policyCheck": policy_check,
        "support": support,
    }


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
    if MODE == "policy_error":
        # A bounded machine code from the helper's own policy check. The decision
        # settles needs-host; the Worker receipt keeps its real outcome.
        write_output(
            output,
            {
                "status": "error",
                "operation": request.get("operation"),
                "tableRevision": request.get("tableRevision"),
                "code": "policy-outcome-false",
                "message": "the decision answer violated the routing policy",
                "details": {"code": "policy-outcome-false", "description": "the program-derived task outcome disagrees"},
                "elapsedSeconds": 0.2,
                "shutdownConfirmed": True,
            },
        )
        return 1
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
        envelope["decision"] = select_envelope(request)
        write_output(output, envelope)
        return 0
    envelope = {
        "status": "ok",
        "operation": "select-v2" if request["operation"] == "select" and MODE == "wrong_operation" else request["operation"],
        "tableRevision": request["tableRevision"] + (5 if MODE == "wrong_revision" else 0),
        "requested": request.get("profile"),
        "resolved": {**(request.get("profile") or {}), "reasoningEffort": (request.get("profile") or {}).get("effort")},
        "observed": None,
        "usage": {"inputTokens": 10, "outputTokens": 5},
        "elapsedSeconds": 0.2,
        "shutdownConfirmed": True,
    }
    envelope["decision"] = select_envelope(request)
    write_output(output, envelope)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    sys.exit(main(sys.argv[1:]))
