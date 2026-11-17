"""Current routing facts and legal Host actions, without command templates."""
from copy import deepcopy

KINDS = ("router-unavailable", "routing-changed", "router-abstained")


def build_boundary(*, kind, code, reason, candidates, facts, router_trials, retry_at,
                   decision_id, run_id=None, shutdown_confirmed=False,
                   continuation_problem=None, target_run_id=None, helper_policy=None,
                   explicit_buddy=False):
    """Project the current facts; the caller still supplies the exact response id."""
    available = run_id is not None and shutdown_confirmed and continuation_problem is None
    return deepcopy({"kind": kind, "code": code, "reason": reason, "routerTrials": router_trials,
                     "candidates": candidates, "facts": facts, "retryAt": retry_at,
                     "decisionId": decision_id, "runId": run_id, "targetRunId": target_run_id,
                     "helperPolicy": helper_policy, "continuationProblem": continuation_problem,
                     "actions": ["continue", "cancel"] if available else ["get", "cancel"],
                     "userAction": "settingsChangeOnly"})
