from ....protocol.session_coverage import Evidenced, SessionCoverage

_SOURCE = ("tests/probes/dsh_session_content.py;"
           "tests/python/buddy/harnesses/dsh/fixtures/session-content-dsh-cli-0.1.5-rc.1.json")
_RECORDED = Evidenced(version="DSH CLI 0.1.5-rc.1", source=_SOURCE)

COVERAGE: SessionCoverage = {
    "messages": _RECORDED,
    "reasoning": _RECORDED,
    "tool-input": _RECORDED,
    "tool-output": _RECORDED,
    # Native todo/write is retained in the todo_write tool's structured input;
    # this CLI does not emit a separate ACP plan notification for it.
    "plan": _RECORDED,
}
