from ....protocol.session_coverage import Evidenced, SessionCoverage, Unverified

COVERAGE: SessionCoverage = {
    "messages": Evidenced(
        version="Claude Code CLI 2.1.284",
        source="tests/python/buddy/harnesses/claude/fixtures/native-usage-claude.json:1-34 (installed stream-json schemas, redacted text blocks)",
    ),
    "reasoning": Unverified(),
    "tool-input": Unverified(),
    "tool-output": Unverified(),
    "plan": Unverified(),
}
