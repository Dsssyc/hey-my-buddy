from ....protocol.session_coverage import Evidenced, SessionCoverage, Unverified

_NATIVE = Evidenced(
    version="ZCode app 0.17.0",
    source="docs/acceptance/adr024/session-view.md:124-128; tests/probes/zcode_native.py:119-169 (recorded live and resumed loopback-model native events and sessions.sqlite)",
)

COVERAGE: SessionCoverage = {
    "messages": _NATIVE,
    "reasoning": _NATIVE,
    "tool-input": _NATIVE,
    "tool-output": _NATIVE,
    "plan": Unverified(),
}
