from ....protocol.reasons import reason
from ....protocol.session_coverage import Evidenced, SessionCoverage, Unavailable, Unverified

COVERAGE: SessionCoverage = {
    "messages": Unverified(),
    "reasoning": Unavailable(reason("session.codex-internal-reasoning-omitted")),
    "tool-input": Unverified(),
    "tool-output": Evidenced(
        version="Codex CLI 0.162.1",
        source="tests/python/buddy/harnesses/codex/fixtures/protocol_items.py:1-6 (offline native protocol sample: functionCallOutput.output)",
    ),
    "plan": Unverified(),
}
