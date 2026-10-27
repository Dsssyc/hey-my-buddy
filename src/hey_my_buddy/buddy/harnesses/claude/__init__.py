"""claude native harness declarations."""
from ..discovery_spec import DiscoverySpec


DISCOVERY = DiscoverySpec(
    command="claude",
    apps={
        "darwin": None,
        "win32": None,
        "linux": None,
    },
)
