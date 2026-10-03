"""Secret-free billing labels from native account and provider metadata."""
from __future__ import annotations

UNKNOWN = "unknown"


def fact(kind: str, source: str, observed_at: str) -> dict:
    return {"kind": kind if kind in ("subscription", "metered") else UNKNOWN,
            "source": source, "observedAt": observed_at}


def codex_account(account: object, observed_at: str) -> dict:
    kind = UNKNOWN
    if isinstance(account, dict):
        if account.get("type") == "apiKey":
            kind = "metered"
        elif account.get("type") == "chatgpt" and account.get("planType") in (
                "plus", "pro", "team", "business", "enterprise", "edu"):
            kind = "subscription"
    return fact(kind, "codex/account-read", observed_at)


def claude_status(status: object, observed_at: str) -> dict:
    kind = UNKNOWN
    if isinstance(status, dict) and status.get("loggedIn") is True and status.get("apiProvider") in ("anthropic", "firstParty"):
        method = status.get("authMethod")
        if method in ("claude.ai", "claudeai") and isinstance(status.get("subscriptionType"), str):
            kind = "subscription"
        elif method in ("api_key", "apiKey", "console"):
            kind = "metered"
    return fact(kind, "claude/auth-status", observed_at)


def zcode_access(access: object, observed_at: str) -> dict:
    return fact({"api-key": "metered", "zhipu-coding-plan-api-key": "subscription"}.get(access, UNKNOWN),
                "zcode/provider-access", observed_at)


def for_provider(connection, adapter: str, provider: str) -> dict:
    """Only native metadata for this exact configuration may establish a label."""
    from ..blackboard.service.harness_health import read_health
    health = read_health(connection, adapter)
    row = health.get("billingByProvider") or {}
    if isinstance(row, dict) and isinstance(row.get(provider), dict):
        return row[provider]
    return fact(UNKNOWN, "unverified", health.get("checkedAt") or "")
