"""DSH ACP client with owned processes and private protocol evidence.

Native HOME and DSH_HOME are inherited. Session storage is selected by the
run module's native launch patch; this client handles the ACP session surface
and permission callbacks, leaving tool scopes and role judgment to the driver.
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
