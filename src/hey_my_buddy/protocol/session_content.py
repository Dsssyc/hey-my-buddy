"""The ACP v1 subset written to an attempt's SQLite session record.

These are display facts, independent of activity, settlement and stop evidence.
Decode JSON with the shared strict decoder before validating at a read boundary.
Content and tool bodies deliberately have no size or retention limit.

projectionVersion 1 appends message and tool text within each
``(_meta.nativeItemId, _meta.contentSegment)`` key. Segments include the
native turn identity so successive turns of an attempt remain distinct.
``_meta.replace: true`` creates or replaces the block at that key with this
complete snapshot (diffs, revised markers or a non-extending completed item).
Tool ``content`` therefore appends here, deliberately differing from ACP v1's
whole-content replacement; only the explicit metadata requests replacement.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from .internal_models import InternalModel


class NativeMeta(InternalModel):
    native_item_id: str = Field(min_length=1)
    content_segment: str = Field(min_length=1)
    replace: bool | None = None
    native_type: str | None = None
    phase: str | None = None
    marker: bool | None = None
    reasoning: Literal["summary", "text"] | None = None
    part_index: int | None = None


class TextContent(InternalModel):
    type: Literal["text"] = "text"
    text: str


class MessageChunk(InternalModel):
    session_update: Literal["user_message_chunk", "agent_message_chunk", "agent_thought_chunk"]
    content: TextContent
    meta: NativeMeta | None = Field(default=None, alias="_meta")


class ToolContent(InternalModel):
    type: Literal["content"] = "content"
    content: TextContent


class ToolLocation(InternalModel):
    path: str


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
    content: list[ToolContent] | None = None
    locations: list[ToolLocation] | None = None
    meta: NativeMeta | None = Field(default=None, alias="_meta")


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
