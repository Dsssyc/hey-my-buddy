from ....protocol.session_coverage import SessionCoverage, Unverified

COVERAGE: SessionCoverage = {
    "messages": Unverified(),
    "reasoning": Unverified(),
    "tool-input": Unverified(),
    "tool-output": Unverified(),
    "plan": Unverified(),
}
