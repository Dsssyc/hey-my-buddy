from ....protocol.session_coverage import Evidenced, SessionCoverage, Unverified

_NATIVE = Evidenced(
    version="Claude Code CLI 2.1.299",
    source="tests/probes/claude_session_content.py; tests/python/buddy/harnesses/claude/fixtures/session-content-claude.json",
)

COVERAGE: SessionCoverage = {
    "messages": _NATIVE,
    "reasoning": _NATIVE,
    "tool-input": _NATIVE,
    "tool-output": _NATIVE,
    "plan": Unverified(),
}
