"""dsh native harness declarations."""
from ..discovery_spec import AppEntry, DiscoverySpec


DISCOVERY = DiscoverySpec(
    command="dsh",
    apps={
        "darwin": AppEntry("bundle", "com.deepseek.dsh", "Contents/Resources/runtime/cli/bin/dsh"),
        "win32": AppEntry("uninstall", "1bf39983-50d0-5fe0-9ef4-cece76f67c5e", "resources/runtime/cli/bin/dsh.cmd"),
        "linux": None,
    },
)
