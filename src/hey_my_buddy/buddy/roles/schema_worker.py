"""Worker policy for a native schema delivery, independent of its transport.

The registry supplies the existing prompt and receipt vocabulary. This role
owns the outcome schema, attention decision and validation; it neither starts
a process nor interprets a vendor event.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from ...errors import BoardError
from ...json_codec import decode_strict_json
from .turn_io import truncate_outcome_summaries, validate_outcome
from .prompts import worker_prompt


def outcome_schema(*, summary_description: str | None = None, suggested_buddy: bool = False,
                   require_suggested_buddy: bool = False) -> dict:
    request = {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"],
        "properties": {"summary": {"type": "string"}, "attempted": {"type": "string"},
                       "neededWork": {"type": "string"},
                       "expectedArtifacts": {"type": "array", "items": {"type": "string"}},
                       "acceptance": {"type": "string"}},
    }
    if suggested_buddy:
        request["properties"]["suggestedBuddyId"] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        if require_suggested_buddy:
            request["required"].append("suggestedBuddyId")
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
class WorkerReceiptOptions:
    """Registered receipt projections over the same Worker collection path."""

    capture_session_from_turn: bool = False


@dataclass(frozen=True)
class NativeSchemaWorker:
    prefixes: tuple[str, ...]
    schema: dict
    validation_key: str
    display_name: str
    interaction_kind: str
    native_identity_keys: tuple[str, ...] = ()
    reject_previous_on_initial: bool = False
    delegation_authority: bool = False

    def for_turn(self, turn_input: dict) -> NativeSchemaWorker:
        if turn_input.get("context", {}).get("delegationAuthority") is not True:
            return self
        schema = deepcopy(self.schema)
        schema["properties"]["outcome"]["anyOf"][0]["properties"]["disposition"]["enum"].append("waiting")
        return replace(self, schema=schema, delegation_authority=True)

    def prompt(self, task_text: str, turn_input: dict, *, credential_file: str | None = None) -> str:
        delivery = self.prefixes + ((
            "When you have handed off work to micro tasks you own and need their results, conclude this Lead turn with disposition waiting, request: null and a clear summary. The blackboard will start a later turn when an owned task needs your attention.",
        ) if self.delegation_authority else ())
        return worker_prompt(task_text, turn_input, delivery=delivery, credential_file=credential_file)

    def parse_value(self, result) -> tuple[dict | None, str | None, dict]:
        """Use the same outcome validator for the decision and its diagnostic."""
        outcome = None
        truncated = {}
        error = "no completed final message"
        value = result.value
        if value is not None:
            try:
                wrapper = value.parsed.value if value.parsed is not None else decode_strict_json(value.raw)
                if not isinstance(wrapper, dict) or set(wrapper) != {"outcome"}:
                    raise ValueError("The native result must contain exactly the structured outcome")
                bounded, truncated = truncate_outcome_summaries(wrapper["outcome"])
                error = validate_outcome(bounded, delegation_authority=self.delegation_authority)
                if error is None:
                    outcome = bounded
            except (ValueError, TypeError, RecursionError) as problem:
                error = str(problem)[:500]
        return outcome, error, truncated

    def receipt_fields(self, result) -> dict:
        fields = {}
        if result.native_identity is not None and self.native_identity_keys:
            identity = result.native_identity.to_payload()
            if all(identity.get(key) is not None for key in self.native_identity_keys):
                fields["nativeIdentity"] = {key: identity[key] for key in self.native_identity_keys}
        return fields

    def delivery(self, result, facts: dict) -> tuple[dict, dict, dict]:
        """Validate the role value, retaining the old denied-request decision."""
        outcome, error, truncated = self.parse_value(result)
        if self.interaction_kind == "request":
            denied = bool(facts.get("nativeRequestMethod"))
        else:
            denied = bool(facts.get("deniedControlRequestIds") or facts.get("permissionDenials"))
        unanswered = bool(facts.get("unansweredDeliveredInquiries"))
        attention = unanswered or (denied and (outcome is None or outcome["disposition"] in ("completed", "waiting")))
        if attention:
            if unanswered and outcome is not None and outcome["disposition"] in ("assistance", "attention"):
                outcome = {**outcome, "disposition": "attention"}
            else:
                outcome = self.attention_outcome(facts)
                truncated = {}
        elif outcome is None:
            message = "the native root turn has no strict structured final outcome"
            if self.interaction_kind == "request":
                message += ": " + (error or "no completed final message")
            raise BoardError("invalid-result", message)
        provenance = {**facts, "turnEnd": "completed", self.validation_key: not attention}
        if attention:
            provenance["controllerAttention"] = True
        return outcome, provenance, truncated

    def attention_outcome(self, facts: dict) -> dict:
        if facts.get("unansweredDeliveredInquiries"):
            summary = f"{self.display_name} ended its turn with delivered Host inquiries still unanswered"
            return {"disposition": "attention", "summary": summary, "remaining": [], "decisions": [],
                    "artifacts": [], "request": {"summary": summary,
                        "attempted": "The native turn received the questions but supplied no verified answer tool receipt",
                        "neededWork": "Host must review the unanswered questions and choose a continuation",
                        "expectedArtifacts": [],
                        "acceptance": "The Host resolves the unanswered inquiries"}}
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
            if facts.get("deniedOperationSummary"):
                attempted += ": " + facts["deniedOperationSummary"]
        return {"disposition": "attention", "summary": summary, "remaining": [], "decisions": [],
                "artifacts": [], "request": {"summary": summary, "attempted": attempted,
                    "neededWork": "Host must review the requested action and choose a permitted continuation",
                    "expectedArtifacts": [],
                    "acceptance": "The Host resolves this boundary and starts an authorized continuation"}}
