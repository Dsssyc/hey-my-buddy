"""Change markers and the console read cache: the honesty rules of a 304.

These tests pin the invariants the console's conditional reads rely on: one
living marker connection that sees this process's own committed writes, the
double-read rule that never binds an old body to a new marker, time-derived
deadlines, negotiation and per-session isolation. Removing any of those
behaviors must fail here before it can silently freeze a console.
"""
from __future__ import annotations

import json
import os
import time
import unittest

from support import BoardTestCase

from hey_my_buddy.console.read_cache import (
    MAX_ENTRIES,
    ReadCache,
    collect_deadline,
    gate_lease_deadline,
    if_none_match_matches,
    parse_boundary,
    select_encoding,
)


def stable_projection(value: dict = None):
    value = value if value is not None else {"tableRevision": 0, "items": [1, 2, 3]}

    def generate():
        return dict(value), None

    return generate


class SelectEncodingTests(unittest.TestCase):
    def test_negotiation_matrix(self):
        cases = [
            (None, "identity"),
            ([""], "identity"),
            (["identity"], "identity"),
            (["gzip"], "gzip"),
            (["gzip", "deflate"], "gzip"),
            (["gzip;q=0"], "identity"),
            (["gzip;q=0.001"], "gzip"),
            (["br"], "identity"),
            (["br", "gzip;q=0"], "identity"),
            (["*"], "gzip"),
            (["*;q=0"], " unacceptable"),
            (["identity;q=0"], " unacceptable"),
            (["identity;q=0", "gzip;q=0"], " unacceptable"),
            (["gzip;q=0, identity;q=0"], " unacceptable"),
        ]
        for header, expected in cases:
            with self.subTest(header=header):
                self.assertEqual(select_encoding(header), expected)

    def test_if_none_match_membership(self):
        self.assertTrue(if_none_match_matches('"abc"', '"abc"'))
        self.assertTrue(if_none_match_matches('"x", "abc"', '"abc"'))
        self.assertTrue(if_none_match_matches('W/"abc"', '"abc"'))
        self.assertTrue(if_none_match_matches('*', '"anything"'))
        self.assertFalse(if_none_match_matches('"abcd"', '"abc"'))
        self.assertFalse(if_none_match_matches(None, '"abc"'))
        self.assertFalse(if_none_match_matches('', '"abc"'))


class BoundaryTests(BoardTestCase):
    def test_parse_boundary_accepts_board_and_iso_shapes(self):
        moment = parse_boundary("2026-10-07T12:00:00.000Z")
        self.assertEqual(parse_boundary("2026-10-07T12:00:00.000+00:00"), moment)
        self.assertIsNone(parse_boundary("not a time"))
        self.assertIsNone(parse_boundary(None))
        self.assertIsNone(parse_boundary(""))

    def test_collect_deadline_takes_earliest_future_boundary(self):
        after = parse_boundary("2026-10-07T12:00:00Z")
        projection = {
            "gate": {"writer": {"expiresAt": "2026-10-07T13:00:00Z"}},
            "profiles": [
                {"quotaRetry": {"eligibleAt": "2026-10-07T12:30:00Z"}},
                {"quotaExhausted": {"resetsAt": "2027-01-01T00:00:00Z"}},
            ],
            "harnesses": [{"quota": {"observedAt": "2026-10-07T11:30:00Z"}, "scanAfter": "2026-10-07T12:02:00Z"}],
            "routingHealth": {"routers": [{"skipUntil": "2026-10-07T12:05:00Z"}]},
            "past": {"expiresAt": "2026-10-07T11:00:00Z"},
        }
        deadline = collect_deadline(projection, after=after)
        # The borrowed harness scan is due first: once scanAfter passes, the
        # next periodic read must re-enter the projection and kick the scan.
        self.assertEqual(deadline, parse_boundary("2026-10-07T12:02:00Z"))
        del projection["harnesses"][0]["scanAfter"]
        without_scan = collect_deadline(projection, after=after)
        # The observation ages stale one hour later; the skip window ends first
        # once no scan boundary exists.
        self.assertEqual(without_scan, parse_boundary("2026-10-07T12:05:00Z"))
        stale_only = collect_deadline({"quota": {"observedAt": "2026-10-07T11:30:00Z"}}, after=after)
        self.assertEqual(stale_only, parse_boundary("2026-10-07T12:30:00Z"))
        self.assertEqual(collect_deadline({"x": 1}, after=after), float("inf"))

    def test_gate_lease_deadline_follows_the_earliest_unexpired_lease(self):
        board = self.board()
        now = time.time()
        self.assertIsNone(gate_lease_deadline(board.store.db, now))
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO evaluation_writers(writer_id,request_id,kind,state,generation,expected_revision,"
                "token_verifier,requested_at,granted_at,expires_at)"
                " VALUES('w1','marker-test-request','maintenance','active',1,0,'v','2026-01-01T00:00:00.000Z',"
                "'2026-01-01T00:00:00.000Z','2030-01-01T00:00:00Z')"
            )
        moment = gate_lease_deadline(board.store.db, now)
        self.assertEqual(moment, parse_boundary("2030-01-01T00:00:00Z"))
        # An already-expired lease is not a boundary; the view has already flipped.
        self.assertIsNone(gate_lease_deadline(board.store.db, parse_boundary("2030-06-01T00:00:00Z")))


class MarkerTests(BoardTestCase):
    def marker_board(self):
        board = self.board()
        return board

    def test_marker_sees_this_process_own_committed_writes(self):
        """The trap PRAGMA data_version sets: same-connection writes never bump.

        The cache's marker lives on its own connection and never writes, so
        every board write — including this process's own, which always travels
        through separate short-lived connections — must move it.
        """
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        first = cache.marker()
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('marker-test','1')"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
        second = cache.marker()
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        # A read-only board leaves the marker exactly where it was.
        self.assertEqual(cache.marker(), second)

    def test_serve_returns_cached_entry_without_regenerating(self):
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        generate = stable_projection()
        first = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate)
        second = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate)
        self.assertEqual(first.entry.body, second.entry.body)
        self.assertEqual(cache.calls["console"]["projections"], 1)
        self.assertEqual(cache.calls["console"]["serializations"], 1)
        self.assertFalse(second.not_modified)
        conditional = cache.serve(
            kind="console", key="k", session="s1", extra_marker=(), generate=generate,
            if_none_match=first.entry.validator("identity"),
        )
        self.assertTrue(conditional.not_modified)

    def test_each_content_coding_has_its_own_strong_validator(self):
        """RFC 9110 §8.8.3: identity and gzip never share a strong validator.

        A validator borrowed across codings must not answer 304: the
        conditional is evaluated against the negotiated representation only.
        """
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        generate = stable_projection()
        first = cache.serve(
            kind="console", key="k", session="s1", extra_marker=(), generate=generate, coding="identity"
        )
        gzipped = cache.serve(
            kind="console", key="k", session="s1", extra_marker=(), generate=generate, coding="gzip"
        )
        self.assertEqual(gzipped.entry, first.entry)  # one cached state, no regeneration
        self.assertEqual(cache.calls["console"]["projections"], 1)
        identity_etag, gzip_etag = first.entry.validator("identity"), first.entry.validator("gzip")
        self.assertNotEqual(identity_etag, gzip_etag)
        self.assertTrue(identity_etag.startswith('"'))
        self.assertTrue(gzip_etag.startswith('"'))
        import gzip as gzip_module

        self.assertEqual(gzip_module.decompress(first.entry.representation("gzip")), first.entry.body)
        # Each coding's validator answers 304 only for that coding.
        self.assertTrue(
            cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate,
                        if_none_match=identity_etag, coding="identity").not_modified
        )
        self.assertTrue(
            cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate,
                        if_none_match=gzip_etag, coding="gzip").not_modified
        )
        self.assertFalse(
            cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate,
                        if_none_match=identity_etag, coding="gzip").not_modified
        )
        self.assertFalse(
            cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate,
                        if_none_match=gzip_etag, coding="identity").not_modified
        )
        # An unknown coding is a programming error, never silently identity.
        with self.assertRaises(ValueError):
            cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate, coding="br")

    def test_replaced_database_file_invalidates_the_marker(self):
        """An open marker connection must not observe a replaced database forever.

        An upgrade rollback or restore swaps the database file atomically; the
        old connection would keep reading the old inode's frozen data_version
        and answer "unchanged" forever. The marker connection detects the path
        naming a different file and reports it as changed.
        """
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        first = cache.marker()
        self.assertIsNotNone(first)
        replacement = board.store.directory / "replacement.sqlite3"
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(replacement)) as connection:
            connection.execute("CREATE TABLE swapped(value)")
            connection.execute("INSERT INTO swapped(value) VALUES('different file')")
            connection.commit()
        os.replace(replacement, board.store.db.path)
        self.assertIsNone(cache.marker())
        # The next check reconnects to whatever the path now names.
        self.assertIsNotNone(cache.marker())

    def _replacement_at_revision_97(self, board):
        """A same-schema replacement database with table_revision=97."""
        import sqlite3
        from contextlib import closing

        replacement = board.store.directory / "replacement.sqlite3"
        with closing(sqlite3.connect(board.store.db.path)) as source, closing(sqlite3.connect(replacement)) as target:
            source.backup(target)
            target.execute("UPDATE evaluation_state SET table_revision=97 WHERE id=1")
            target.commit()
        os.replace(replacement, board.store.db.path)

    def _revision_projection(self, board):
        def generate():
            with board.store.db.read() as connection:
                revision = connection.execute(
                    "SELECT table_revision FROM evaluation_state WHERE id=1"
                ).fetchone()[0]
            return {"revision": revision}, None

        return generate

    def test_a_replaced_database_invalidates_every_cached_key_and_session(self):
        """Round-5 reviewed defect: a replaced database restarts data_version.

        After the swap the first reprojected key observes the change through
        the file identity, but the reconnected marker can report the SAME
        data_version the old entries were cached under. Without the
        connection epoch in the marker, every other query and session keeps
        answering 304 with pre-replacement bodies.
        """
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        generate = self._revision_projection(board)

        def read(key, session, validator=None):
            return cache.serve(
                kind="review", key=key, session=session, extra_marker=(), generate=generate,
                if_none_match=validator,
            )

        old_a = read("a", "s1")
        old_b = read("b", "s2")
        self.assertEqual(json.loads(old_a.entry.body)["revision"], 0)
        self._replacement_at_revision_97(board)
        new_a = read("a", "s1", old_a.entry.validator("identity"))
        self.assertFalse(new_a.not_modified)
        self.assertEqual(json.loads(new_a.entry.body)["revision"], 97)
        new_b = read("b", "s2", old_b.entry.validator("identity"))
        self.assertFalse(
            new_b.not_modified,
            "A new SQLite connection may restart data_version at the old value; reprojecting "
            "one cache key must invalidate all remaining query/session entries",
        )
        self.assertEqual(json.loads(new_b.entry.body)["revision"], 97)

    def test_a_reopened_connection_with_a_repeated_data_version_invalidates_the_cache(self):
        """Same file, transient error: the reopened marker re-reads the same value.

        A connection error closes the marker connection; the reconnect
        re-reads an identical data_version from the same file. Only the
        connection epoch keeps that repeated value from revalidating every
        cached entry.
        """
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        generate = stable_projection()
        first = cache.serve(kind="console", key="k", session="s", extra_marker=(), generate=generate)
        marker_before = cache.marker()
        self.assertIsNotNone(marker_before)
        # A transient failure of the live connection (anything SQLite or the
        # OS reports) must not leave the cache frozen on the old marker.
        cache._marker_connection._connection.close()
        self.assertIsNone(cache.marker())
        projections = cache.calls["console"]["projections"]
        after = cache.serve(
            kind="console", key="k", session="s", extra_marker=(), generate=generate,
            if_none_match=first.entry.validator("identity"),
        )
        # The repeated data_version must not revalidate the old entry: the
        # projection runs again (an unchanged body may then honestly 304).
        self.assertEqual(cache.calls["console"]["projections"], projections + 1)
        marker_after = cache.marker()
        self.assertIsNotNone(marker_after)
        self.assertNotEqual(marker_after[0], marker_before[0])  # the epoch moved
        self.assertEqual(marker_after[1], marker_before[1])     # the value repeats

    def test_a_reconnect_during_generation_never_binds_the_old_body_to_the_new_epoch(self):
        """A replacement between the two marker reads forces a regeneration.

        The before-marker belongs to the old epoch, so the body generated
        across the swap is never cached; the retried generation is bound to
        the reconnected marker and servable unchanged afterwards.
        """
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        attempts = {"n": 0}

        def racing_generate():
            attempts["n"] += 1
            if attempts["n"] == 1:
                self._replacement_at_revision_97(board)
            with board.store.db.read() as connection:
                revision = connection.execute(
                    "SELECT table_revision FROM evaluation_state WHERE id=1"
                ).fetchone()[0]
            return {"revision": revision, "attempt": attempts["n"]}, None

        outcome = cache.serve(
            kind="console", key="k", session="s", extra_marker=(), generate=racing_generate
        )
        self.assertGreaterEqual(attempts["n"], 2)
        body = json.loads(outcome.entry.body)
        self.assertEqual(body["revision"], 97)
        self.assertEqual(body["attempt"], attempts["n"])
        again = cache.serve(
            kind="console", key="k", session="s", extra_marker=(), generate=racing_generate,
            if_none_match=outcome.entry.validator("identity"),
        )
        self.assertTrue(again.not_modified)

    def test_serve_regenerates_on_marker_extra_and_deadline(self):
        board = self.board()
        cache = ReadCache(board.store.db, clock=lambda: 1000.0)
        self.addCleanup(cache.close)
        calls = {"n": 0}

        def generate():
            calls["n"] += 1
            return {"n": calls["n"]}, None

        cache.serve(kind="console", key="k", session="s1", extra_marker=(False,), generate=generate)
        # A different marker component (for example the assets flag flipping)
        # invalidates even though the database did not change.
        outcome = cache.serve(kind="console", key="k", session="s1", extra_marker=(True,), generate=generate)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(json.loads(outcome.entry.body)["n"], 2)
        # An entry whose deadline has passed regenerates under the same marker:
        # a zero maximum lifetime pins the stored deadline to the fixed clock.
        cache.serve(kind="console", key="dead", session="s1", extra_marker=(), generate=generate, max_age=0.0)
        expired = cache.serve(
            kind="console", key="dead", session="s1", extra_marker=(), generate=generate, max_age=0.0
        )
        self.assertEqual(calls["n"], 4)
        self.assertEqual(json.loads(expired.entry.body)["n"], 4)
        # A generation hint tighter than max_age wins.
        hinted = cache.serve(
            kind="console", key="hint", session="s1", extra_marker=(), generate=lambda: ({"n": 0}, 1005.0)
        )
        self.assertTrue(hinted.entry.deadline <= 1005.0)

    def test_serve_never_binds_a_body_generated_across_a_write(self):
        board = self.board()
        cache = ReadCache(board.store.db)
        self.addCleanup(cache.close)
        attempts = {"n": 0}

        def racing_generate():
            attempts["n"] += 1
            # The projection runs between the two marker reads; a concurrent
            # board write must force a retry instead of caching under the old
            # marker.
            if attempts["n"] == 1:
                with board.store.db.write() as connection:
                    connection.execute(
                        "INSERT INTO meta(key,value) VALUES('race','1')"
                        " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                    )
            return {"attempt": attempts["n"]}, None

        outcome = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=racing_generate)
        self.assertGreaterEqual(attempts["n"], 2)
        self.assertEqual(json.loads(outcome.entry.body)["attempt"], attempts["n"])

    def test_entries_are_private_per_session_and_bounded(self):
        board = self.board()
        cache = ReadCache(board.store.db, max_entries=2)
        self.addCleanup(cache.close)
        generate = stable_projection({"v": 1})
        first = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate)
        other = cache.serve(kind="console", key="k", session="s2", extra_marker=(), generate=generate)
        self.assertIsNot(first.entry, other.entry)
        self.assertEqual(cache.calls["console"]["projections"], 2)
        # The bound evicts the least recently used entry, not other sessions'.
        cache.serve(kind="console", key="a", session="s1", extra_marker=(), generate=generate)
        cache.serve(kind="console", key="b", session="s1", extra_marker=(), generate=generate)
        self.assertLessEqual(len(cache._entries), 2)
        self.assertLessEqual(MAX_ENTRIES, 64)


if __name__ == "__main__":
    unittest.main()
