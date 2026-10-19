"""The ACP v1 subset written to an attempt's session.jsonl.

These are display facts, independent of activity, settlement and stop evidence.
Decode JSON with the shared strict decoder before validating at a read boundary.
Content and tool bodies deliberately have no size or retention limit.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from .internal_models import FormatVersion, InternalModel


class SessionFormat(InternalModel):
    protocol: Literal["acp"]
    protocol_version: FormatVersion
    projection_version: FormatVersion


SESSION_FORMAT = SessionFormat(protocol="acp", protocol_version=1, projection_version=1)


class NativeMeta(InternalModel):
    native_item_id: str | None = None
    native_type: str | None = None
    phase: str | None = None
    marker: bool | None = None
    reasoning: Literal["summary", "text"] | None = None
    part_index: int | None = None


class TextContent(InternalModel):
    type: Literal["text"] = "text"
    text: str


class ImageContent(InternalModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str


class ResourceLink(InternalModel):
    type: Literal["resource_link"] = "resource_link"
    uri: str
    name: str


ContentBlock = Annotated[TextContent | ImageContent | ResourceLink, Field(discriminator="type")]


class MessageChunk(InternalModel):
    session_update: Literal["user_message_chunk", "agent_message_chunk", "agent_thought_chunk"]
    content: ContentBlock
    meta: NativeMeta | None = Field(default=None, alias="_meta")


class ToolContent(InternalModel):
    type: Literal["content"] = "content"
    content: ContentBlock


class ToolDiff(InternalModel):
    type: Literal["diff"] = "diff"
    path: str
    old_text: str | None = None
    new_text: str


class ToolLocation(InternalModel):
    path: str
    line: int | None = None


ToolKind = Literal["read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other"]
ToolStatus = Literal["pending", "in_progress", "completed", "failed"]


class ToolCallUpdate(InternalModel):
    session_update: Literal["tool_call_update"] = "tool_call_update"
    tool_call_id: str
    title: str | None = None
    kind: ToolKind | None = None
    status: ToolStatus | None = None
    raw_input: JsonValue = None
    raw_output: JsonValue = None
    content: list[Annotated[ToolContent | ToolDiff, Field(discriminator="type")]] | None = None
    locations: list[ToolLocation] | None = None


class ToolCall(ToolCallUpdate):
    session_update: Literal["tool_call"] = "tool_call"
    title: str


class PlanEntry(InternalModel):
    content: str
    status: Literal["pending", "in_progress", "completed"]
    priority: Literal["high", "medium", "low"]


class Plan(InternalModel):
    session_update: Literal["plan"] = "plan"
    entries: list[PlanEntry]


SessionUpdate = Annotated[MessageChunk | ToolCall | ToolCallUpdate | Plan, Field(discriminator="session_update")]


class SessionNotification(InternalModel):
    session_id: str
    update: SessionUpdate
