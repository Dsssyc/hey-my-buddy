"""zcode native harness declarations."""
from ..discovery_spec import AppEntry, DiscoverySpec


DISCOVERY = DiscoverySpec(
    command="zcode",
    apps={
        "darwin": AppEntry("bundle", "dev.zcode.app", "Contents/Resources/glm/zcode.cjs"),
        "win32": None,
        "linux": None,
    },
)
