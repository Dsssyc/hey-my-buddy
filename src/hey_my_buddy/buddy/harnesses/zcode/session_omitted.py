"""No omissions established by the available offline evidence.

ZCode app-server 0.16.9: fixtures/native-usage-zcode.json documents text and
reasoning parts in session/messages, not complete streamed reasoning. The
session projector uses only session/event, never that history RPC. Synthetic
event fixtures verify projection rules, not native coverage of reasoning,
tool arguments or plans. A versioned real event sample is needed before
claiming any particular content is absent (or all reasoning is supplied).
"""

OMITTED = ()
