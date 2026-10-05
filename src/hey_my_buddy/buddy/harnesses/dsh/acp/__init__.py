"""Standalone Python ACP stdio client for DSH (ADR-025 plan 4-A, public-wiring slice excluded).

This package speaks newline-delimited JSON-RPC 2.0 to a native ``dsh --profile acp``
process over stdio. It owns the launched process through ``owned_popen`` and
``ProcessHandle`` without changing them, forces this run's private ``DSH_HOME``
on every launch and keeps a caller-provided private ``HOME`` when one is given,
and exposes the native session path
(initialize, session/new, set_config_option, list, resume, close) plus the
prompt and cancel native operations for a later driver. Permission requests are
answered only through an upgrade-request callback policy; tool-scope simulation,
tool-event classification, budgets, results and board judgment live elsewhere.

Not yet wired to the public run interface: the RunRequest/RunResult seam and the
role controllers connect in a later step of ADR-025.
"""
from .client import AcpClient, PermissionPolicy
from .connection import AcpConnectionClosed, AcpRequestError, AcpTimeout
from .launch import LaunchOwnershipError, LaunchRejected, launch

__all__ = [
    "AcpClient",
    "AcpConnectionClosed",
    "AcpRequestError",
    "AcpTimeout",
    "LaunchOwnershipError",
    "LaunchRejected",
    "PermissionPolicy",
    "launch",
]
