"""Package-owned CLI names and registered desktop app entry locations."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AppEntry:
    kind: Literal["bundle", "uninstall", "msix"]
    identity: str
    cli_path: str


@dataclass(frozen=True)
class DiscoverySpec:
    command: str
    apps: dict[str, AppEntry | None]
