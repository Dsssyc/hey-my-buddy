"""Pure ADR-021 Host boundary data and governed continuation templates."""
from copy import deepcopy

from .errors import BoardError
from .schemas import CONFIGURATION_FIELDS

KINDS = ("router-unavailable", "routing-changed", "router-abstained")


def build_boundary(*, kind: str, code: str | None, reason: str, candidates: list,
                   facts: dict, router_trials: list, retry_at: str | None,
                   decision_id: str, run_id: str | None = None, revision: int | None = None,
                   shutdown_confirmed: bool = False, continuation_problem: str | None = None) -> dict:
    """Copy frozen facts and explain the Host's existing operations; start nothing.

    The caller computes legality, retry times and actual lineage stop evidence.
    Commands carry a saved-control-file placeholder, never a credential, and
    ``notBefore`` is template metadata rather than an unsupported CLI argument.
    """
    if not isinstance(kind, str) or kind not in KINDS:
        raise BoardError("INVALID_ARGUMENT", "Unknown Router boundary kind")
    if not isinstance(reason, str) or not reason.strip():
        raise BoardError("INVALID_ARGUMENT", "Router boundary reason must be nonempty")
    if not isinstance(candidates, list) or not isinstance(router_trials, list) or not isinstance(facts, dict):
        raise BoardError("INVALID_ARGUMENT", "Router boundary requires frozen candidates, trials and facts")
    if type(shutdown_confirmed) is not bool:
        raise BoardError("INVALID_ARGUMENT", "Router boundary stop evidence must be boolean")
    if not isinstance(decision_id, str) or not decision_id:
        raise BoardError("INVALID_ARGUMENT", "Router boundary requires its decision identity")
    for value, name in ((code, "code"), (retry_at, "retry_at"), (run_id, "run_id"),
                        (continuation_problem, "continuation_problem")):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise BoardError("INVALID_ARGUMENT", f"Router boundary {name} must be a nonempty string or null")
    if revision is not None and (type(revision) is not int or revision < 0):
        raise BoardError("INVALID_ARGUMENT", "Router boundary revision must be a nonnegative integer or null")
    identities = []
    for candidate in candidates:
        if (not isinstance(candidate, dict) or not isinstance(candidate.get("profileId"), str)
                or any(not isinstance(candidate.get(key), str) or not candidate[key].strip()
                       for key in CONFIGURATION_FIELDS)):
            raise BoardError("INVALID_ARGUMENT", "A frozen Router candidate needs a complete buddy identity")
        identities.append({key: candidate[key] for key in CONFIGURATION_FIELDS})

    blocked = ("独立 selection-request 没有关联业务委派，不能生成 continue 命令" if run_id is None else
               "Router 或拥有的执行尚未确认停止" if not shutdown_confirmed else
               continuation_problem or ("当前目标 revision 未提供" if revision is None else None))
    base = {"runId": run_id, "expectedRevision": revision, "controlFile": "<saved-control-file>",
            "input": "继续既定工作目标。"}
    choices = []
    if blocked is None:
        for index, (candidate, identity) in enumerate(zip(candidates, identities)):
            choices.append({"profileId": candidate["profileId"], "method": "continue", "params": {
                **base, "commandId": f"router-continue-{decision_id}-{index}",
                "configuration": identity, "reason": "Host 根据路由边界的冻结候选指定 buddy 继续"}})
    continue_reason = blocked or ("没有冻结的合法候选" if not choices else None)
    reroute_reason = blocked or ("尚无可知的 Router 恢复时间；可先读取 health 再决定" if retry_at is None else None)
    reroute_params = None if reroute_reason else {
        **base, "commandId": f"router-reroute-{decision_id}", "reroute": True,
        "reason": "在 Router 再试时间之后重新路由同一工作目标"}
    if kind in ("router-unavailable", "routing-changed") and "取消后重新提交没有用" not in reason:
        reason += "；取消后重新提交没有用。"
    return deepcopy({"kind": kind, "code": code, "reason": reason, "routerTrials": router_trials,
                     "candidates": candidates, "facts": facts, "retryAt": retry_at,
                     "commands": {
                         "continue": {"blocked": continue_reason is not None, "reason": continue_reason, "choices": choices},
                         "reroute": {"blocked": reroute_reason is not None, "reason": reroute_reason,
                                     "method": "continue", "params": reroute_params, "notBefore": retry_at}},
                     "userAction": "settingsChangeOnly"})
