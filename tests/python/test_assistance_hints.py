"""Bounded assistance hints in both coding-harness turn prompts.

ADR-010 requires every Worker prompt to state the trigger conditions for ending a
turn with assistance/attention, to ask the Host for an authorized helper/reviewer
with the required artifacts and acceptance, to never create a peer Buddy task, and
to keep internal subagents available. These tests pin the shared wording on the
Python side (ZCode prompt and MCP tool text) and verify the DSH system-prompt
section states the same triggers, without duplicating provider credentials or
unbounded text.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from buddy.adapters import turn_io
from buddy.adapters.zcode_mcp import FINISH_DESCRIPTION, REPLY_DESCRIPTION
from buddy.adapters.zcode_runner import governed_prompt

ROOT = Path(__file__).resolve().parents[2]
TURN_RESULT = ROOT / "harnesses/dsh/plugins/turn-result.mjs"
REPLY_TOOL = "mcp__srv123__buddy_inquiry_reply"
TURN_TOOL = "mcp__srv123__buddy_finish_turn"


class AssistanceHintTests(unittest.TestCase):
    def test_shared_hints_state_triggers_request_shape_and_no_peer_dispatch(self):
        text = "\n".join(turn_io.ASSISTANCE_HINTS)
        for phrase in ("outside the authorized scope", "no further evidence", "capability clearly does not fit",
                       "review condition", "attempted", "neededWork", "acceptance", "expectedArtifacts",
                       "peer job", "internal subagents", "Host"):
            self.assertIn(phrase, text, phrase)
        self.assertLessEqual(len(text.encode()), 4096)

    def test_zcode_prompt_embeds_the_bounded_hints_verbatim(self):
        prompt = governed_prompt("task text", {"resumeMode": "initial"}, TURN_TOOL, REPLY_TOOL)
        for hint in turn_io.ASSISTANCE_HINTS:
            self.assertIn(hint, prompt)
        self.assertIn(TURN_TOOL, prompt)
        self.assertIn(REPLY_TOOL, prompt)
        self.assertIn("internal subagents", prompt)
        self.assertNotIn("startNow", prompt)
        self.assertLessEqual(len(prompt.encode()), 64 * 1024)

    def test_mcp_tool_text_is_bounded_and_never_offers_peer_dispatch(self):
        for text in (FINISH_DESCRIPTION, REPLY_DESCRIPTION):
            self.assertLessEqual(len(text.encode()), 1024)
            self.assertIn("does not dispatch other tasks", text)

    def test_dsh_prompt_section_states_the_same_triggers(self):
        source = TURN_RESULT.read_text()
        for phrase in ("neededWork", "acceptance", "expectedArtifacts", "assistance or attention",
                       "peer job", "internal subagents", "outside the authorized scope",
                       "review condition", "Host decides"):
            self.assertIn(phrase, source, phrase)
        section = source.split("systemPrompt.section(", 1)[1].split("});", 1)[0]
        self.assertLessEqual(len(section.encode()), 8192, "the governed prompt section must stay bounded")
        # The Host decides help; neither prompt may tell the Worker to create another task.
        for directive in ("create a Buddy task", "dispatch a Buddy", "create a peer"):
            self.assertNotIn(directive, source)


if __name__ == "__main__":
    unittest.main()
