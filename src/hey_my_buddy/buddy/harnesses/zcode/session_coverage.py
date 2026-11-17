from ....protocol.session_coverage import Evidenced, SessionCoverage, Unverified

_NATIVE = Evidenced(
    version="ZCode app 0.17.0",
    source="docs/acceptance/adr024/session-view.md#补记：ZCode 的正文与思考（2026-10-27）; tests/probes/zcode_native.py; tests/python/buddy/harnesses/zcode/test_session_content.py",
)

COVERAGE: SessionCoverage = {
    "messages": _NATIVE,
    "reasoning": _NATIVE,
    "tool-input": _NATIVE,
    "tool-output": _NATIVE,
    "plan": Unverified(),
}
