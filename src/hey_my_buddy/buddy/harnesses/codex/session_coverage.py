from ....protocol.session_coverage import Evidenced, SessionCoverage, Unverified

_RECORDED = Evidenced(
    version="Codex CLI 0.164.0",
    source="tests/probes/codex_session_content.py; tests/python/buddy/harnesses/codex/fixtures/session-content-codex-0.164.0.json",
)

COVERAGE: SessionCoverage = {
    "messages": _RECORDED,
    # The recorded readable reasoning is a summary, labelled as such by the
    # projector's metadata. Codex's internal chain of thought is not supplied.
    "reasoning": _RECORDED,
    "tool-input": _RECORDED,
    "tool-output": _RECORDED,
    "plan": Unverified(),
}
