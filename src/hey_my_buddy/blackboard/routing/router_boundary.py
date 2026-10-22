"""Pure ADR-021 Host boundary data and micro-task continuation templates."""
from copy import deepcopy
import hashlib
import json

from ...protocol.schemas import BUDDY_FIELDS

KINDS = ("router-unavailable", "routing-changed", "router-abstained")


def build_boundary(*, kind: str, code: str | None, reason: dict | None, candidates: list,
                   facts: dict, router_trials: list, retry_at: str | None,
                   decision_id: str, run_id: str | None = None, revision: int | None = None,
                   shutdown_confirmed: bool = False, continuation_problem: str | None = None,
                   target_run_id: str | None = None, helper_policy: str | None = None) -> dict:
    """Copy frozen facts and explain the Host's existing operations; start nothing.

    The caller computes legality, retry times and actual lineage stop evidence.
    Commands carry a saved-control-file placeholder, never a credential, and
    ``notBefore`` is template metadata rather than an unsupported CLI argument.
    A nested target uses the owning run's authority and revision. The caller
    either explicitly preserves live helpers with ``keep`` or blocks through
    ``continuation_problem``; these templates never cancel helpers.
    """
    identities = [{key: candidate[key] for key in BUDDY_FIELDS} for candidate in candidates]

    blocked = run_id is None or not shutdown_confirmed or continuation_problem is not None or revision is None
    base = {"runId": run_id, "expectedRevision": revision, "controlFile": "<saved-control-file>"}
    if target_run_id is not None:
        base["targetRunId"] = target_run_id
    if helper_policy is not None:
        base["helperPolicy"] = helper_policy

    def command_id(operation, *, index=None, identity=None):
        # A new owner/revision or changed tuple is a different request for the
        # command ledger. Hash the complete template binding to keep long
        # decision IDs within continue's 128-character commandId bound.
        binding = {"decisionId": decision_id, "operation": operation, "base": base,
                   "index": index, "buddy": identity}
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False).encode("utf-8")).hexdigest()
        return f"router-{operation}-{digest}"

    choices = []
    if not blocked:
        for index, (candidate, identity) in enumerate(zip(candidates, identities)):
            choices.append({"buddyId": candidate["buddyId"], "method": "continue", "params": {
                **base, "commandId": command_id("continue", index=index, identity=identity),
                "buddy": identity}})
    reroute_blocked = blocked or retry_at is None
    reroute_params = None if reroute_blocked else {
        **base, "commandId": command_id("reroute"), "reroute": True}
    return deepcopy({"kind": kind, "code": code, "reason": reason, "routerTrials": router_trials,
                     "candidates": candidates, "facts": facts, "retryAt": retry_at,
                     "commands": {
                         "continue": {"blocked": blocked or not choices, "code": continuation_problem, "choices": choices},
                         "reroute": {"blocked": reroute_blocked, "code": continuation_problem,
                                     "method": "continue", "params": reroute_params, "notBefore": retry_at}},
                     "userAction": "settingsChangeOnly"})
