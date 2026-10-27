"""No omissions established by the available offline evidence.

ZCode app-server 0.16.9: fixtures/native-usage-zcode.json documents text and
reasoning parts in session/messages, not complete streamed reasoning. The
session projector uses only session/event, never that history RPC. Tool input
comes from input in model.streaming (kind tool_call), or from input in
tool.updated (kind scheduled) when it was not streamed; after streaming,
scheduled omits input and carries inputOmitted and inputByteLength instead.
These input shapes were checked against the locally installed ZCode 0.16.9
app-server package. Synthetic event fixtures verify projection rules, not
native coverage of reasoning or plans. A versioned real event sample is needed before
claiming any particular content is absent (or all reasoning is supplied).
"""

OMITTED = ()
