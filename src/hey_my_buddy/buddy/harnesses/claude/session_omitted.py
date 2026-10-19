"""No display omissions established by the retained Claude Code CLI samples.

The 2.1.284 schema-derived, redacted native-usage-claude.json sample supplies
complete assistant text blocks, not reasoning or tool results. The existing
2.1.282-shaped offline tool fixture exercises tool_result content; it does not
prove native coverage. Neither establishes that thinking or plan content is
unavailable. Synthetic thinking tests verify projection only, not CLI coverage.
"""

OMITTED = ()
