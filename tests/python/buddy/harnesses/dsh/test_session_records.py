"""The session-record reader's own contract, over real rollout files.

The reader moved to :mod:`hey_my_buddy.buddy.harnesses.dsh.session_records`
unchanged for the fresh-session fold; these cases pin its two halves against
real files on disk — the plain and zstandard rollouts a fresh session
produces, and the frozen-boundary projection a resumed turn needs: extension
of the frozen prefix, the old attempts' records that sit beside the target's
in a reconstructed goal root being skipped — never folded, never making the
boundary unreliable — and the unprovable shapes (a vanished rollout, a
rewritten prefix, a shortened file, a replayed frozen line, an unreliable
freeze, a record no header can attribute) that must leave the projection
unknown instead of guessing by subtraction. The end-to-end resumed turns over
the real fake agent live in ``test_native_resume``; these are the reader's own
bounds.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.dsh import session_records
from hey_my_buddy.buddy.harnesses.dsh.session_records import (
    freeze_session_records,
    record_candidates,
    resumed_session_record_facts,
    session_record_facts,
)

SESSION = "sess-a"


def assistant(seq: int, text: str, usage) -> dict:
    return {"type": "assistant/message", "seq": seq, "data": {
        "turn": 1, "step": 1,
        "message": {"id": f"assistant-{seq}", "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                    "source": {"kind": "model", "provider": "fake", "model": "m1"}},
        "usage": usage}}


def turn_one() -> list[dict]:
    return [
        {"type": "session", "version": 3, "id": SESSION, "createdAt": 1,
         "cwd": "/tmp", "isSeeded": False, "origin": "acp", "delegationDepth": 0},
        {"type": "user/message", "seq": 1, "data": {
            "id": "user-1", "role": "user", "content": [{"type": "text", "text": "one"}],
            "source": {"kind": "user"}}},
        assistant(2, "first answer", {"inputTokens": 100, "outputTokens": 10,
                                      "totalTokens": 120, "cacheReadTokens": 10,
                                      "reasoningTokens": 1}),
        {"type": "turn/end", "seq": 3, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]


def turn_two_append() -> list[dict]:
    return [
        {"type": "user/message", "seq": 4, "data": {
            "id": "user-2", "role": "user", "content": [{"type": "text", "text": "two"}],
            "source": {"kind": "user"}}},
        assistant(5, "second answer", {"inputTokens": 40, "outputTokens": 4,
                                       "totalTokens": 48, "cacheReadTokens": 4,
                                       "reasoningTokens": 2}),
        {"type": "turn/end", "seq": 6, "data": {"turn": 2, "reason": {"kind": "completed"}}},
    ]


_FOREIGN_USAGE = {"inputTokens": 900000, "outputTokens": 90000,
                  "totalTokens": 999999, "cacheReadTokens": 50000,
                  "reasoningTokens": 9000}


def foreign_rollout(session_id: str) -> list[dict]:
    """Another session's record, carrying usage nobody may attribute."""
    return [
        {"type": "session", "version": 3, "id": session_id, "createdAt": 1,
         "cwd": "/tmp", "isSeeded": False, "origin": "acp", "delegationDepth": 0},
        assistant(2, "an old answer nobody may count", _FOREIGN_USAGE),
        {"type": "turn/end", "seq": 3, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]


class SessionRecordCase(unittest.TestCase):
    def setUp(self):
        container = tempfile.TemporaryDirectory(
            prefix="dsh-session-records-", dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(container.cleanup)
        self.root = Path(container.name).resolve()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir(mode=0o700)

    def write_rollout(self, events, *, name=None, compressed=True):
        if name is None:
            name = f"{SESSION}.v3.jsonl" + (".zstd" if compressed else "")
        path = self.sessions / name
        if compressed:
            import zstandard
            text = "".join(json.dumps(event) + "\n" for event in events)
            path.write_bytes(zstandard.ZstdCompressor().compress(text.encode()))
        else:
            path.write_text("".join(json.dumps(event) + "\n" for event in events))
        return path


class FreshFoldTests(SessionRecordCase):
    def test_a_plain_rollout_folds_like_a_compressed_one(self):
        self.write_rollout(turn_one(), compressed=False)
        self.write_rollout(turn_one(), name=f"{SESSION}-b.v3.jsonl.zstd")
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertEqual(facts["usage"]["inputTokens"], 200)
        self.assertEqual(facts["usage"]["nativeRecords"], 2)
        self.assertEqual(facts["model"], {"provider": "fake", "model": "m1"})
        self.assertEqual(facts["sessions"], [SESSION, SESSION])

    def test_a_foreign_header_is_never_attributed(self):
        self.write_rollout(turn_one())
        self.assertIsNone(session_record_facts(self.sessions, ["other-session"]))

    def test_a_missing_root_or_session_is_an_unknown_not_a_zero(self):
        self.assertIsNone(session_record_facts(self.root / "absent", [SESSION]))
        self.assertIsNone(session_record_facts(self.sessions, []))

    def test_the_scan_finds_nested_cwd_layouts_and_bounds_itself(self):
        nested = self.sessions / "--encoded-cwd--" / SESSION
        nested.mkdir(parents=True)
        self.write_rollout(turn_one(), name=str(nested / "session.v3.jsonl.zstd"))
        self.assertEqual(len(record_candidates(self.sessions)), 1)
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertEqual(facts["usage"]["inputTokens"], 100)


class ResumeBoundaryTests(SessionRecordCase):
    def freeze(self):
        return freeze_session_records(self.sessions, SESSION)

    def test_an_appended_segment_projects_only_the_new_turn(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        self.assertTrue(baseline.reliable)
        self.assertEqual(len(baseline.files), 1)
        self.assertEqual(len(baseline.files[0].digests), 4)
        # The resumed turn appends; the frozen prefix stays byte-identical.
        self.write_rollout([*turn_one(), *turn_two_append()])
        facts = resumed_session_record_facts(self.sessions, baseline)
        boundary = facts["resumeBoundary"]
        self.assertTrue(boundary["proven"])
        self.assertEqual(boundary["baselineLines"], 4)
        self.assertEqual(boundary["appendedLines"], 3)
        self.assertEqual(facts["usage"]["inputTokens"], 40, "only the appended turn counts")
        self.assertEqual(facts["usage"]["completeness"], "complete")
        self.assertEqual(facts["lastAssistant"], "second answer")
        self.assertEqual(facts["failure"], None)

    def test_an_empty_baseline_stays_unknown_when_a_rollout_appears(self):
        # The previous turn left no record at all; the file this turn's end
        # carries both turns' content and nothing marks the split, so the
        # boundary is unprovable rather than guessed.
        baseline = self.freeze()
        self.assertEqual(baseline.files, ())
        self.assertTrue(baseline.reliable)
        self.write_rollout(turn_one())
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "matching-record-set-changed")
        self.assertIsNone(facts["usage"])

    def test_a_vanished_rollout_is_unprovable(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        (self.sessions / f"{SESSION}.v3.jsonl.zstd").write_bytes(b"")
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "matching-record-set-changed")
        self.assertIsNone(facts["usage"])

    def test_a_rewritten_prefix_is_unprovable(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        rewritten = turn_one()
        rewritten[2]["data"]["usage"]["inputTokens"] = 555
        self.write_rollout([*rewritten, *turn_two_append()])
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "frozen-prefix-rewritten")
        self.assertIsNone(facts["usage"])

    def test_a_shortened_rollout_is_unprovable(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        self.write_rollout(turn_one()[:2])
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "record-shortened")
        self.assertIsNone(facts["usage"])

    def test_a_replayed_frozen_line_is_unprovable(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        original = (self.sessions / f"{SESSION}.v3.jsonl.zstd").read_bytes()
        import zstandard
        lines = zstandard.ZstdDecompressor().decompress(original)
        self.write_rollout([*turn_one(), *turn_two_append()])
        # The agent replays its frozen lines verbatim after the new turn.
        path = self.sessions / f"{SESSION}.v3.jsonl.zstd"
        path.write_bytes(zstandard.ZstdCompressor().compress(
            zstandard.ZstdDecompressor().decompress(path.read_bytes()) + lines))
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"],
                         "appended-line-replays-frozen-record")
        self.assertIsNone(facts["usage"])

    def test_an_extra_matching_rollout_is_unprovable(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        self.write_rollout([*turn_one(), *turn_two_append()])
        # A second file whose header claims the same session: a rotation or a
        # rewrite into a new name, and nothing proves its lines are new.
        self.write_rollout([turn_one()[0], *turn_two_append()],
                           name=f"{SESSION}-second.v3.jsonl.zstd")
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "matching-record-set-changed")
        self.assertIsNone(facts["usage"])

    def test_an_unreliable_freeze_stays_unknown_even_for_a_clean_extension(self):
        self.write_rollout(turn_one())
        baseline = session_records.RecordBaseline(
            session_id=SESSION,
            files=(session_records.FrozenRecord(
                path=str(self.sessions / f"{SESSION}.v3.jsonl.zstd"), digests=("0" * 64,)),),
            reliable=False)
        self.write_rollout([*turn_one(), *turn_two_append()])
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "baseline-unreadable")
        self.assertIsNone(facts["usage"])

    def test_a_truncated_final_read_keeps_the_definite_part_partial(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        blob = {"type": "fixture/oversized", "seq": 99, "data": {"blob": "x" * (4 * 1024 * 1024)}}
        self.write_rollout([*turn_one(), *turn_two_append()[:2], blob, *turn_two_append()[2:]])
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertTrue(facts["resumeBoundary"]["proven"])
        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["usage"]["completeness"], "partial")
        self.assertEqual(facts["usage"]["inputTokens"], 40)

    def test_foreign_session_records_never_pollute_the_frozen_baseline(self):
        # A goal-shaped root after a reconstruction: the old attempts' rollouts
        # sit beside the native session's own record, in both layouts.
        self.write_rollout(foreign_rollout("old-initial"),
                           name="old-initial.v3.jsonl", compressed=False)
        nested = self.sessions / "--encoded-cwd--" / "old-reconstructed"
        nested.mkdir(parents=True)
        self.write_rollout(foreign_rollout("old-reconstructed"),
                           name=str(nested / "session.v3.jsonl.zstd"))
        self.write_rollout(turn_one())
        baseline = self.freeze()
        self.assertTrue(baseline.reliable, "a confirmed foreign record is skipped, never unreliable")
        self.assertEqual([frozen.path for frozen in baseline.files],
                         [str(self.sessions / f"{SESSION}.v3.jsonl.zstd")])
        self.assertEqual(len(baseline.files[0].digests), 4)
        # The resumed turn appends to the target; the foreign files change too.
        self.write_rollout([*turn_one(), *turn_two_append()])
        self.write_rollout([*foreign_rollout("old-initial"), *turn_two_append()],
                           name="old-initial.v3.jsonl", compressed=False)
        facts = resumed_session_record_facts(self.sessions, baseline)
        boundary = facts["resumeBoundary"]
        self.assertTrue(boundary["proven"])
        self.assertEqual(boundary["baselineLines"], 4)
        self.assertEqual(boundary["appendedLines"], 3)
        self.assertEqual(facts["recordsRead"], 3)
        self.assertEqual(facts["usage"]["inputTokens"], 40,
                         "the foreign records' usage never mixes in")
        self.assertEqual(facts["usage"]["nativeRecords"], 1)
        self.assertEqual(facts["usage"]["completeness"], "complete")
        self.assertEqual(facts["lastAssistant"], "second answer")
        self.assertIsNone(facts["failure"])

    def test_a_root_of_only_foreign_records_is_a_valid_empty_native_baseline(self):
        # After a reconstruction and before the native session's first record:
        # every candidate names another session, and the target's boundary is
        # a reliable empty one, not an unreadable one.
        self.write_rollout(foreign_rollout("old-initial"),
                           name="old-initial.v3.jsonl", compressed=False)
        self.write_rollout(foreign_rollout("old-reconstructed"),
                           name="old-reconstructed.v3.jsonl.zstd")
        baseline = self.freeze()
        self.assertEqual(baseline.files, ())
        self.assertTrue(baseline.reliable)

    def test_an_unattributable_record_keeps_the_freeze_unreliable(self):
        # A candidate whose header cannot be read — garbage, or an empty file —
        # cannot be attributed to any session, so the boundary stays honestly
        # unknown instead of assuming the file absent or foreign.
        self.write_rollout(turn_one())
        (self.sessions / "junk.v3.jsonl").write_bytes(b"not a record header at all\n")
        (self.sessions / "empty.v3.jsonl.zstd").write_bytes(b"")
        baseline = self.freeze()
        self.assertFalse(baseline.reliable)
        self.write_rollout([*turn_one(), *turn_two_append()])
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "baseline-unreadable")
        self.assertIsNone(facts["usage"])


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
