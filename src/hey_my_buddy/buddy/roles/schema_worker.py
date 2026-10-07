"""Worker policy for a native schema delivery, independent of its transport.

The registry supplies the existing prompt and receipt vocabulary. This role
owns the outcome schema, attention decision and validation; it neither starts
a process nor interprets a vendor event.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from .turn_io import ASSISTANCE_HINTS, validate_outcome


def outcome_schema(*, summary_description: str | None = None, suggested_profile: bool = False) -> dict:
    request = {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"],
        "properties": {"summary": {"type": "string"}, "attempted": {"type": "string"},
                       "neededWork": {"type": "string"},
                       "expectedArtifacts": {"type": "array", "items": {"type": "string"}},
                       "acceptance": {"type": "string"}},
    }
    if suggested_profile:
        request["properties"]["suggestedProfileId"] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    summary = {"type": "string"}
    if summary_description is not None:
        summary["description"] = summary_description

    def branch(dispositions, request_schema):
        return {
            "type": "object", "additionalProperties": False,
            "required": ["disposition", "summary", "remaining", "decisions", "artifacts", "request"],
            "properties": {"disposition": {"type": "string", "enum": dispositions}, "summary": summary,
                           **{key: {"type": "array", "items": {"type": "string"}}
                              for key in ("remaining", "decisions", "artifacts")},
                           "request": request_schema},
        }
    return {"type": "object", "additionalProperties": False, "required": ["outcome"],
            "properties": {"outcome": {"anyOf": [branch(["completed"], {"type": "null"}),
                                                 branch(["assistance", "attention"], request)]}}}


@dataclass(frozen=True)
class NativeSchemaWorker:
    prefixes: tuple[str, ...]
    schema: dict
    validation_key: str
    display_name: str
    interaction_kind: str
    follow_workspace_access: bool = False
    bind_account_environment: bool = False
    native_quota_failure: bool = False
    native_identity_keys: tuple[str, ...] = ()
    validation_error_key: str | None = None
    reject_previous_on_initial: bool = False

    def prompt(self, task_text: str, turn_input: dict) -> str:
        return "\n\n".join([*self.prefixes, *ASSISTANCE_HINTS, task_text, canonical_json(turn_input)])

    def tool_scope(self, turn_input: dict) -> str:
        workspace = turn_input.get("executionWorkspace") or {}
        return "read" if self.follow_workspace_access and workspace.get("access") == "read" else "write"

    def parse_value(self, result) -> tuple[dict | None, str | None]:
        """Use the same outcome validator for the decision and its diagnostic."""
        outcome = None
        error = "no completed final message"
        value = result.value
        if value is not None:
            try:
                wrapper = value.parsed.value if value.parsed is not None else decode_strict_json(value.raw)
                if not isinstance(wrapper, dict) or set(wrapper) != {"outcome"}:
                    raise ValueError("The native result must contain exactly the structured outcome")
                error = validate_outcome(wrapper["outcome"])
                if error is None:
                    outcome = wrapper["outcome"]
            except (ValueError, TypeError, RecursionError) as problem:
                error = str(problem)[:500]
        return outcome, error

    def receipt_fields(self, result) -> dict:
        fields = {}
        if result.native_identity is not None and self.native_identity_keys:
            identity = result.native_identity.to_payload()
            if all(identity.get(key) is not None for key in self.native_identity_keys):
                fields["nativeIdentity"] = {key: identity[key] for key in self.native_identity_keys}
        if self.validation_error_key and result.end.status == "ok" and result.value is not None:
            _outcome, error = self.parse_value(result)
            if error is not None:
                fields[self.validation_error_key] = error
        return fields

    def delivery(self, result, facts: dict) -> tuple[dict, dict]:
        """Validate the role value, retaining the old denied-request decision."""
        outcome, error = self.parse_value(result)
        if self.interaction_kind == "request":
            denied = bool(facts.get("nativeRequestMethod"))
        else:
            denied = bool(facts.get("deniedControlRequestIds") or facts.get("permissionDenials"))
        attention = denied and (outcome is None or outcome["disposition"] == "completed")
        if attention:
            outcome = self.attention_outcome(facts)
        elif outcome is None:
            message = "the native root turn has no strict structured final outcome"
            if self.interaction_kind == "request":
                message += ": " + (error or "no completed final message")
            raise BoardError("invalid-result", message)
        provenance = {**facts, "turnEnd": "completed", self.validation_key: not attention}
        if attention:
            provenance["controllerAttention"] = True
        return outcome, provenance

    def attention_outcome(self, facts: dict) -> dict:
        if self.interaction_kind == "request":
            summary = f"{self.display_name} requested Host interaction through {facts['nativeRequestMethod']}"
            attempted = "The owned controller declined the correlated native request without granting access"
        else:
            names = facts.get("deniedToolNames") or []
            if facts.get("deniedControlRequestIds"):
                labels = ", ".join(dict.fromkeys(names))[:200]
                summary = f"{self.display_name} requested Host interaction through a native tool permission ({labels})"
            else:
                summary = f"{self.display_name} denied {facts.get('permissionDenials', 0)} native tool permission request(s) during the turn"
            attempted = "The owned controller denied the correlated native permission request without granting access"
        return {"disposition": "attention", "summary": summary, "remaining": [], "decisions": [],
                "artifacts": [], "request": {"summary": summary, "attempted": attempted,
                    "neededWork": "Host must review the requested action and choose a permitted continuation",
                    "expectedArtifacts": [],
                    "acceptance": "The Host resolves this boundary and starts an authorized continuation"}}
