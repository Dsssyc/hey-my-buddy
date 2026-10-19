"""Observed display omissions: Codex CLI 0.162.1 offline protocol samples.

Only supplied reasoning summaries or native text are projected. Internal
reasoning is not provided; no claim is made about Codex App coverage.
"""
from ....protocol.reasons import reason

OMITTED = (reason('session.codex-internal-reasoning-omitted'),)
