"""MCP tool facade. Service requests and completion events use C-Two IPC."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from .native import caller_thread, native_call, NativeUnavailable
from .receiver import register_notification
from .transport import call_service

RUN = {"runId": {"type": "string", "format": "uuid"}}
DEFINITIONS = {
    "start": ("Delegate a bounded task to dsh through C-Two. Wait with buddy_wait, or register an official App heartbeat before ending the turn for background work. Native notify:true is experimental and disabled by default. Reuse identical requestId for uncertain starts.", {
        "requestId": {"type": "string", "minLength": 1, "maxLength": 128}, "task": {"type": "string", "minLength": 1, "maxLength": 1000000}, "cwd": {"type": "string", "minLength": 1},
        **{key: {"type": "string", "minLength": 1} for key in ["model", "provider", "effort"]},
        "timeoutSeconds": {"type": "integer", "minimum": 10, "maximum": 86400}, "workspace": {"type": "boolean"}, "notify": {"type": "boolean"}}, ["requestId", "task", "cwd"]),
    "status": ("Read compact persisted state and separate notification delivery state.", RUN, ["runId"]),
    "wait": ("Wait up to 30 seconds for a run to change; cancellation of this wait does not stop dsh.", {**RUN, "afterRevision": {"type": "integer", "minimum": 0}, "timeoutMs": {"type": "integer", "minimum": 0, "maximum": 30000}}, ["runId"]),
    "result": ("Read existing runner output and paths. Independently inspect real artifacts before accepting.", RUN, ["runId"]),
    "list": ("Recover existing run IDs without relaunching work.", {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "offset": {"type": "integer", "minimum": 0}}, []),
    "cancel": ("Cancel only the named Buddy-owned run; unrelated dsh sessions are unaffected.", RUN, ["runId"]),
    "acknowledge": ("Record acceptance after independently verifying the run's actual outcome.", {**RUN, "note": {"type": "string", "minLength": 1, "maxLength": 4000}}, ["runId", "note"]),
    "dashboard": ("Get the private read-only local task panel URL.", {}, []),
    "health": ("Check C-Two service health and whether this MCP process can access the App notification channel.", {}, []),
}

async def serve():
    server = Server("hey-my-buddy", version="0.3.0")
    verified: set[str] = set()

    @server.list_tools()
    async def list_tools():
        return [Tool(name=f"buddy_{name}", description=description, inputSchema={"type":"object", "properties":properties, "required":required, "additionalProperties":False}, annotations=ToolAnnotations(readOnlyHint=name in {"health","status","wait","result","list","dashboard"}, destructiveHint=name=="cancel", idempotentHint=True, openWorldHint=name=="start")) for name,(description,properties,required) in DEFINITIONS.items()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        method = name.removeprefix("buddy_")
        if method not in DEFINITIONS:
            raise ValueError("Unknown Buddy tool")
        try:
            params = dict(arguments)
            thread_id = caller_thread(server.request_context.meta)
            if method == "start" and params.pop("notify", False):
                if not thread_id:
                    raise NativeUnavailable("No App caller metadata; use notify:false and keep the current turn waiting")
                if thread_id not in verified:
                    check = await native_call("read_thread", {"threadId": thread_id, "turnLimit": 1, "includeOutputs": False}, thread_id)
                    if check.get("thread", {}).get("id") != thread_id:
                        raise NativeUnavailable("App could not verify the caller task")
                    verified.add(thread_id)
                params["_notify"] = await asyncio.to_thread(register_notification, params["requestId"], thread_id)
            result = await asyncio.to_thread(call_service, method, params)
            if method == "health":
                try:
                    from .native import native_programs
                    native_programs()
                    result["nativeNotification"] = {"configured": bool(thread_id), "verified": thread_id in verified, "callerBound": bool(thread_id)}
                except NativeUnavailable as error:
                    result["nativeNotification"] = {"configured": False, "reason": str(error)}
            return CallToolResult(content=[TextContent(type="text",text=json.dumps(result,ensure_ascii=False))], structuredContent=result)
        except Exception as error:
            result = {"error":{"code":getattr(error,"code", "NATIVE_UNAVAILABLE" if isinstance(error,NativeUnavailable) else "SERVICE_ERROR"), "message":str(error)}}
            return CallToolResult(isError=True,content=[TextContent(type="text",text=json.dumps(result,ensure_ascii=False))],structuredContent=result)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    asyncio.run(serve())

if __name__ == "__main__":
    main()
