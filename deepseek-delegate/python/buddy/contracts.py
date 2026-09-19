"""Private, Python-to-Python C-Two contracts for Buddy control.

Only the control/results CRM remains. The completion-inbox and notification-receiver
CRMs were the transport for the MCP-only native App notification path and were
removed with the MCP server; results are read through this same control endpoint
with ``result``/``status``/``await``.
"""
import c_two as cc


@cc.crm(namespace="hey.my.buddy", version="0.3.0")
class BuddyControl:
    def dispatch(self, request_json: str) -> str:
        ...
