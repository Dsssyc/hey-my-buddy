"""The session-record reader's own contract, over real rollout files.

The reader moved to :mod:`hey_my_buddy.buddy.harnesses.dsh.session_records`
unchanged for the fresh-session fold; these cases pin its two halves against
real files on disk — the plain and zstandard rollouts a fresh session
produces in the session's two-level directory, generation 3 beside generation
4 and the larger one alone counting, and the frozen-boundary projection a
resumed turn needs: extension of the frozen prefix, the old attempts' records
that sit beside the target's in a reconstructed micro task root being skipped
— never folded, never making the boundary unreliable — and the unprovable
shapes (a vanished rollout, a rewritten prefix, a shortened file, a replayed
frozen line, an unreliable freeze, a record no header can attribute) that
must leave the projection unknown instead of guessing by subtraction. The
end-to-end resumed turns over the real fake agent live in ``test_native_resume``;
these are the reader's own bounds.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.dsh import session_records
from hey_my_buddy import private_dirs
from hey_my_buddy.paths import native_path
from hey_my_buddy.private_files import publish_bytes
from hey_my_buddy.buddy.harnesses.dsh.session_records import (
    freeze_session_records,
    record_candidates,
    resumed_session_record_facts,
    session_record_facts,
)

SESSION = "sess-a"
WORK = "--encoded-cwd--"
CURRENT = "session.v4.jsonl.zstd"


def assistant(seq: int, text: str, usage) -> dict:
    return {"type": "assistant/message", "seq": seq, "data": {
        "turn": 1, "step": 1,
        "message": {"id": f"assistant-{seq}", "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                    "source": {"kind": "model", "provider": "fake", "model": "m1"}},
        "usage": usage}}


def turn_one(version: int = 4) -> list[dict]:
    header, *rest = [
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
    return [dict(header, version=version), *rest]


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


def foreign_rollout(session_id: str, version: int = 4) -> list[dict]:
    """Another session's record, carrying usage nobody may attribute."""
    return [
        {"type": "session", "version": version, "id": session_id, "createdAt": 1,
         "cwd": "/tmp", "isSeeded": False, "origin": "acp", "delegationDepth": 0},
        assistant(2, "an old answer nobody may count", _FOREIGN_USAGE),
        {"type": "turn/end", "seq": 3, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]


def rollout_bytes(events, *, compressed=True) -> bytes:
    text = "".join(json.dumps(event) + "\n" for event in events)
    if not compressed:
        return text.encode()
    import zstandard
    return zstandard.ZstdCompressor().compress(text.encode())


class SessionRecordCase(unittest.TestCase):
    def setUp(self):
        container = tempfile.TemporaryDirectory(
            prefix="dsh-session-records-", dir=os.environ.get("HMB_CHECKS_TMPDIR", tempfile.gettempdir()))
        self.addCleanup(container.cleanup)
        self.root = Path(container.name).resolve()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir(mode=0o700)

    def rollout_path(self, name=CURRENT, *, work=WORK, session=SESSION) -> Path:
        """A record file's place in the two-level layout under the root."""
        return self.sessions / work / session / name

    def write_rollout(self, events, *, name=CURRENT, work=WORK, session=SESSION):
        path = self.rollout_path(name, work=work, session=session)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rollout_bytes(events, compressed=name.endswith(".zstd")))
        return path


class FreshFoldTests(SessionRecordCase):
    def test_the_current_generation_folds_usage_model_and_last_assistant(self):
        self.write_rollout(turn_one())
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertEqual(facts["usage"]["inputTokens"], 100)
        self.assertEqual(facts["usage"]["outputTokens"], 10)
        self.assertEqual(facts["usage"]["cachedInputTokens"], 10)
        self.assertEqual(facts["usage"]["completeness"], "complete")
        self.assertEqual(facts["model"], {"provider": "fake", "model": "m1"})
        self.assertEqual(facts["lastAssistant"], "first answer")
        self.assertTrue(facts["turnEndSeen"])
        self.assertTrue(facts["turnEndCompleted"])
        self.assertIsNone(facts["failure"])

    def test_generation_three_still_folds(self):
        path = self.write_rollout(turn_one(3), name="session.v3.jsonl.zstd")
        self.assertEqual(record_candidates(self.sessions, SESSION), [path])
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertEqual(facts["usage"]["inputTokens"], 100)
        self.assertEqual(facts["usage"]["completeness"], "complete")

    def test_generations_three_and_four_coexisting_count_only_the_fourth(self):
        # Migration leaves the old generation beside the new one; both are
        # this session's own records, and folding both would double every
        # counter. Only the largest generation is a record.
        self.write_rollout(turn_one(3), name="session.v3.jsonl.zstd")
        self.write_rollout(turn_one())
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertEqual(facts["usage"]["inputTokens"], 100)
        self.assertEqual(facts["usage"]["nativeRecords"], 1)
        self.assertEqual(facts["recordsRead"], 1)

    def test_an_unknown_larger_generation_leaves_no_record(self):
        # DSH moved on to a generation the reader does not know; the older
        # generation still on disk is not fallen back to.
        self.write_rollout(turn_one(3), name="session.v3.jsonl.zstd")
        self.write_rollout(turn_one(5), name="session.v5.jsonl.zstd")
        self.assertEqual(record_candidates(self.sessions, SESSION), [])
        self.assertIsNone(session_record_facts(self.sessions, [SESSION]))

    def test_a_header_version_that_disagrees_with_the_name_is_not_folded(self):
        self.write_rollout(turn_one(3))
        self.assertIsNone(session_record_facts(self.sessions, [SESSION]))
        baseline = freeze_session_records(self.sessions, SESSION)
        self.assertFalse(baseline.reliable)

    def test_a_migration_temporary_file_is_not_taken_for_a_record(self):
        path = self.write_rollout(turn_one())
        (self.sessions / WORK / SESSION / "session.migration.abc123.jsonl.zstd.tmp") \
            .write_bytes(b"half-written migration output\n")
        self.assertEqual(record_candidates(self.sessions, SESSION), [path])
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertEqual(facts["usage"]["inputTokens"], 100)
        self.assertEqual(facts["recordsRead"], 1)

    def test_a_flat_root_level_rollout_is_no_longer_read(self):
        # No known DSH version writes a record file directly under the root;
        # the lookup does not look there.
        (self.sessions / f"{SESSION}.v3.jsonl").write_bytes(rollout_bytes(turn_one(3), compressed=False))
        (self.sessions / f"{SESSION}.v4.jsonl.zstd").write_bytes(rollout_bytes(turn_one()))
        self.assertEqual(record_candidates(self.sessions, SESSION), [])
        self.assertIsNone(session_record_facts(self.sessions, [SESSION]))

    def test_long_nested_record_retains_ordinary_resume_boundary(self):
        container = self.root / 'long-records'
        self.addCleanup(private_dirs.remove_tree, container)
        self.sessions = container / 'sessions'
        nested = self.sessions
        while len(str(nested)) < 310:
            nested /= 'encoded-cwd-' + 'a' * 35
        # The native layout has one encoded cwd component beneath sessions.
        self.sessions = nested.parent
        path = private_dirs.ensure_private_dir(nested / SESSION) / 'session.v3.jsonl'
        publish_bytes(path, ''.join(json.dumps(event) + '\n' for event in turn_one(3)).encode())
        self.assertEqual(record_candidates(self.sessions, SESSION), [path])
        self.assertEqual(session_record_facts(self.sessions, [SESSION])['usage']['inputTokens'], 100)
        baseline = freeze_session_records(self.sessions, SESSION)
        self.assertEqual(baseline.files[0].path, str(path))
        self.assertNotIn('\\\\?\\', baseline.files[0].path)
        with native_path(path).open('ab') as stream:
            stream.write(''.join(json.dumps(event) + '\n' for event in turn_two_append()).encode())
        self.assertEqual(resumed_session_record_facts(self.sessions, baseline)['usage']['inputTokens'], 40)

    def test_a_later_turn_clears_the_previous_provider_rejection_and_activity(self):
        first = turn_one()
        first[-1] = {"type": "turn/end", "seq": 3, "data": {
            "turn": 1, "reason": {"kind": "error", "error": {"code": "QUOTA"}}}}
        next_turn = [{"type": "user/message", "seq": 4, "data": {}},
                     {"type": "turn/start", "seq": 5, "data": {"turn": 2}},
                     {"type": "turn/end", "seq": 6, "data": {
                         "turn": 2, "reason": {"kind": "completed"}}}]
        self.write_rollout([*first, *next_turn])
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertIsNone(facts["failure"])
        self.assertFalse(facts["modelActivity"])

    def test_a_plain_rollout_folds_like_a_compressed_one(self):
        self.write_rollout(turn_one(), name="session.v4.jsonl")
        self.write_rollout(turn_one(), work="--other-cwd--")
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

    def test_old_sessions_do_not_hide_the_target_after_sixty_four_directories(self):
        for index in range(70):
            self.write_rollout(foreign_rollout(f"old-{index}"),
                               work=f"--old-{index}--", session=f"old-{index}")
        self.write_rollout(turn_one())
        facts = session_record_facts(self.sessions, [SESSION])
        self.assertEqual(facts["recordsRead"], 1)
        self.assertEqual(facts["usage"]["inputTokens"], 100)

    def test_session_id_cannot_select_a_file_outside_the_pinned_root(self):
        outside = self.root / "escape.v3.jsonl"
        outside.write_text(json.dumps(turn_one()[0]) + "\n", encoding="utf-8")
        self.assertEqual(record_candidates(self.sessions, "../escape"), [])

    def test_symlinked_matching_record_and_parent_are_not_selected(self):
        # The only real copy of the record lives outside the pinned root; every
        # path the root offers towards it goes through a symlink.
        outside = self.root / "outside-work" / SESSION / "session.v4.jsonl.zstd"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(rollout_bytes(turn_one()))
        try:
            linked_file = self.rollout_path(work="--file-link--")
            linked_file.parent.mkdir(parents=True)
            linked_file.symlink_to(outside)
            (self.sessions / "--session-link--").mkdir()
            (self.sessions / "--session-link--" / SESSION).symlink_to(
                outside.parent, target_is_directory=True)
            (self.sessions / "--work-link--").symlink_to(
                self.root / "outside-work", target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        self.assertEqual(record_candidates(self.sessions, SESSION), [])
        self.assertIsNone(session_record_facts(self.sessions, SESSION))


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

    def test_a_newer_generation_after_the_freeze_is_unprovable(self):
        # The resumed launch migrated the frozen generation 3 into a new
        # generation 4 beside it; the file set that matches the session is no
        # longer the frozen one, and the new file's lines are not provably
        # this turn's.
        self.write_rollout(turn_one(3), name="session.v3.jsonl.zstd")
        baseline = self.freeze()
        self.assertTrue(baseline.reliable)
        self.write_rollout([*turn_one(), *turn_two_append()])
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "matching-record-set-changed")
        self.assertIsNone(facts["usage"])

    def test_a_vanished_rollout_is_unprovable(self):
        self.write_rollout(turn_one())
        baseline = self.freeze()
        self.rollout_path().write_bytes(b"")
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
        original = self.rollout_path().read_bytes()
        import zstandard
        lines = zstandard.ZstdDecompressor().decompress(original)
        self.write_rollout([*turn_one(), *turn_two_append()])
        # The agent replays its frozen lines verbatim after the new turn.
        path = self.rollout_path()
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
        # A second session directory whose selected record claims the same
        # session: a rotation or a rewrite into a new place, and nothing
        # proves its lines are new.
        self.write_rollout([turn_one()[0], *turn_two_append()], work="--rotation--")
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "matching-record-set-changed")
        self.assertIsNone(facts["usage"])

    def test_an_unreliable_freeze_stays_unknown_even_for_a_clean_extension(self):
        self.write_rollout(turn_one())
        baseline = session_records.RecordBaseline(
            session_id=SESSION,
            files=(session_records.FrozenRecord(
                path=str(self.rollout_path()), digests=("0" * 64,)),),
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
        # A micro-task-shaped root after a reconstruction: the old attempts'
        # rollouts sit in session directories of their own beside the native
        # session's, in both generations.
        self.write_rollout(foreign_rollout("old-initial", 3),
                           name="session.v3.jsonl", work="--old-attempts--",
                           session="old-initial")
        self.write_rollout(foreign_rollout("old-reconstructed"),
                           name="session.v4.jsonl.zstd", work="--old-attempts--",
                           session="old-reconstructed")
        self.write_rollout(turn_one())
        baseline = self.freeze()
        self.assertTrue(baseline.reliable, "a confirmed foreign record is skipped, never unreliable")
        self.assertEqual([frozen.path for frozen in baseline.files],
                         [str(self.rollout_path())])
        self.assertEqual(len(baseline.files[0].digests), 4)
        # The resumed turn appends to the target; the foreign files change too.
        self.write_rollout([*turn_one(), *turn_two_append()])
        self.write_rollout([*foreign_rollout("old-initial", 3), *turn_two_append()],
                           name="session.v3.jsonl", work="--old-attempts--",
                           session="old-initial")
        facts = resumed_session_record_facts(self.sessions, baseline)
        boundary = facts["resumeBoundary"]
        self.assertTrue(boundary["proven"])
        self.assertEqual(boundary["baselineLines"], 4)
        self.assertEqual(boundary["appendedLines"], 3)
        self.assertEqual(facts["recordsRead"], 1)
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
        self.write_rollout(foreign_rollout("old-initial", 3),
                           name="session.v3.jsonl", work="--old-attempts--",
                           session="old-initial")
        self.write_rollout(foreign_rollout("old-reconstructed"),
                           name="session.v4.jsonl.zstd", work="--old-attempts--",
                           session="old-reconstructed")
        baseline = self.freeze()
        self.assertEqual(baseline.files, ())
        self.assertTrue(baseline.reliable)

    def test_an_unattributable_record_keeps_the_freeze_unreliable(self):
        # A candidate whose header cannot be read — garbage, or an empty file —
        # cannot be attributed to any session, so the boundary stays honestly
        # unknown instead of assuming the file absent or foreign.
        self.write_rollout(turn_one())
        garbage = self.rollout_path(work="--garbage--")
        garbage.parent.mkdir(parents=True)
        garbage.write_bytes(b"not a record header at all\n")
        baseline = self.freeze()
        self.assertFalse(baseline.reliable)
        self.write_rollout([*turn_one(), *turn_two_append()])
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertEqual(facts["resumeBoundary"]["reason"], "baseline-unreadable")
        self.assertIsNone(facts["usage"])

    def test_a_non_session_first_line_keeps_the_resume_usage_unknown(self):
        # A later session header cannot prove where the target session's
        # existing records began when the file's first line is not that header.
        # The id and version deliberately match: only the header's type check
        # can reject this otherwise plausible first record.
        prefix = {"type": "user/message", "version": 4, "id": SESSION, "seq": 0,
                  "data": {"id": "orphan"}}
        self.write_rollout([prefix, *turn_one()], name="session.v4.jsonl")

        baseline = self.freeze()
        self.assertFalse(baseline.reliable)
        self.write_rollout([prefix, *turn_one(), *turn_two_append()], name="session.v4.jsonl")
        facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertFalse(facts["resumeBoundary"]["proven"])
        self.assertEqual(facts["resumeBoundary"]["reason"], "baseline-unreadable")
        self.assertIsNone(facts["usage"])

    def test_a_freeze_read_stopped_at_the_size_bound_keeps_usage_unknown(self):
        # Keep each line below the line cap while the real stream exceeds its
        # total-byte cap, so freeze_session_records itself encounters the bound.
        path = self.rollout_path("session.v4.jsonl")
        path.parent.mkdir(parents=True)
        payload = "x" * (1024 * 1024)
        with path.open("w", encoding="utf-8", newline="\n") as rollout:
            rollout.write(json.dumps(turn_one()[0]) + "\n")
            for seq in range(66):
                rollout.write(json.dumps({"type": "fixture/padding", "seq": seq,
                                          "data": {"blob": payload}}))
                rollout.write("\n")
        self.assertGreater(path.stat().st_size, session_records._MAX_RECORD_TOTAL_BYTES)

        baseline = self.freeze()
        self.assertFalse(baseline.reliable)
        with path.open("a", encoding="utf-8", newline="\n") as rollout:
            for event in turn_two_append():
                rollout.write(json.dumps(event) + "\n")
        # Let the final read see the entire file: its own truncation and replay
        # checks must not mask a missing reliability check in the earlier freeze.
        with patch.object(session_records, "_MAX_RECORD_TOTAL_BYTES", path.stat().st_size + 1):
            facts = resumed_session_record_facts(self.sessions, baseline)
        self.assertFalse(facts["resumeBoundary"]["proven"])
        self.assertEqual(facts["resumeBoundary"]["reason"], "baseline-unreadable")
        self.assertIsNone(facts["usage"])


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
