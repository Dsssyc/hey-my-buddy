"""No established display omissions; plans and part events remain unverified.

The installed ZCode app 0.17.0 was recorded end to end against a loopback
scripted model (the session-content probe, 2026-10-27): one live turn and one
resumed native-session turn streamed body text as ``model.streaming``
``text_delta`` frames, reasoning as ``reasoning_delta`` frames, tool input in
the ``model.streaming`` ``tool_call`` frame and tool output in ``tool.updated``
``result`` frames. No plan content appeared in either stream or among the part
rows the app itself wrote to its ``sessions.sqlite`` for those turns, and the
0.17.0 message-part union has no plan part, so plan coverage stays unverified
and plans are not projected. ``message.upserted`` and ``part.*`` events never
appeared either: a future sample that shows them carrying content needs the
projector extended from that sample, never assumed absent. Nothing here shows
ZCode leaves a kind out, so no omission is listed.
"""

OMITTED = ()
