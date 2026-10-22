"""codex native harness declarations."""
from ..discovery_spec import AppEntry, DiscoverySpec


DISCOVERY = DiscoverySpec(
    command="codex",
    apps={
        "darwin": AppEntry("bundle", "com.openai.codex", "Contents/Resources/codex-cli/bin/codex"),
        "win32": AppEntry("msix", "OpenAI.Codex_2p2nqsd0c76g0", "app/resources/codex.exe"),
        "linux": None,
    },
)
