"""Change markers and the console read cache: the honesty rules of a 304.

These tests pin the invariants the console's conditional reads rely on: one
living marker connection that sees this process's own committed writes, the
double-read rule that never binds an old body to a new marker, time-derived
deadlines, negotiation and per-session isolation. Removing any of those
behaviors must fail here before it can silently freeze a console.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from support import BoardTestCase

from hey_my_buddy.console.read_cache import (
    MAX_ENTRIES,
    ReadCache,
    collect_deadline,
    if_none_match_matches,
    parse_boundary,
    select_encoding,
)


def stable_projection(value: dict = None):
    value = value if value is not None else {"items": [1, 2, 3]}

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
    def test_parse_boundary_accepts_board_timestamps_and_absent_values(self):
        moment = parse_boundary("2026-10-07T12:00:00.000Z")
        self.assertEqual(parse_boundary("2026-10-07T12:00:00.000+00:00"), moment)
        self.assertIsNone(parse_boundary(None))
        with self.assertRaises(ValueError):
            parse_boundary("not a time")
        for malformed in ("", "2026-10-07T12:00:00.000", "2026-10-07T12:00:00.000z"):
            with self.subTest(timestamp=malformed), self.assertRaises(ValueError):
                parse_boundary(malformed)

    def test_collect_deadline_uses_only_known_service_producers(self):
        after = parse_boundary("2026-10-07T12:00:00.000Z")
        projection = {
            "buddies": [{"providerCooling": {"coolingUntil": "2026-10-07T12:30:00.000Z"}}],
            "harnesses": [{"providers": [{"coolingUntil": "2026-10-07T12:20:00.000Z"}],
                           "expiresAt": "2026-10-07T12:10:00.000Z",
                           "scanAfter": "2026-10-07T12:02:00.000Z"}],
            "routingHealth": {"routers": [{"skipUntil": "2026-10-07T12:05:00.000Z",
                                          "retryAt": "2026-10-07T12:05:00.000Z"}]},
        }
        self.assertEqual(collect_deadline(projection, after=after), parse_boundary("2026-10-07T12:02:00.000Z"))
        del projection["harnesses"][0]["scanAfter"]
        self.assertEqual(collect_deadline(projection, after=after), parse_boundary("2026-10-07T12:05:00.000Z"))
        projection["routingHealth"]["routers"] = []
        self.assertEqual(collect_deadline(projection, after=after), parse_boundary("2026-10-07T12:10:00.000Z"))
        del projection["harnesses"][0]["expiresAt"]
        self.assertEqual(collect_deadline(projection, after=after), parse_boundary("2026-10-07T12:20:00.000Z"))
        projection["harnesses"][0]["providers"] = []
        self.assertEqual(collect_deadline(projection, after=after), parse_boundary("2026-10-07T12:30:00.000Z"))
        projection["buddies"][0]["providerCooling"] = None
        self.assertEqual(collect_deadline(projection, after=after), float("inf"))

    def test_collect_deadline_ignores_foreign_nested_fields(self):
        projection = {
            "buddies": [{"providerCooling": None, "description": {"retryAt": False}}],
            "harnesses": [{"providers": [], "expiresAt": None, "scanAfter": None,
                           "discovery": {"expiresAt": "tomorrow", "coolingUntil": 123,
                                         "skipUntil": {}, "retryAt": [], "scanAfter": "2026-10-07T12:00:01Z"}}],
            "routingHealth": {"routers": [], "reason": {"params": {"retryAt": "foreign"}}},
            "arbitrary": {"expiresAt": "2026-10-07T12:00:01.000Z"},
        }
        after = parse_boundary("2026-10-07T12:00:00.000Z")
        self.assertEqual(collect_deadline(projection, after=after), float("inf"))
        projection["harnesses"][0]["scanAfter"] = "2026-10-07T12:02:00.000Z"
        self.assertEqual(collect_deadline(projection, after=after), parse_boundary("2026-10-07T12:02:00.000Z"))


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
        generate = Mock(side_effect=stable_projection())
        first = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate)
        second = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate)
        self.assertEqual(first.entry.body, second.entry.body)
        self.assertEqual(generate.call_count, 1)
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
        generate = Mock(side_effect=stable_projection())
        first = cache.serve(
            kind="console", key="k", session="s1", extra_marker=(), generate=generate, coding="identity"
        )
        gzipped = cache.serve(
            kind="console", key="k", session="s1", extra_marker=(), generate=generate, coding="gzip"
        )
        self.assertEqual(gzipped.entry, first.entry)  # one cached state, no regeneration
        self.assertEqual(generate.call_count, 1)
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
            # board write leaves this generation uncached.
            if attempts["n"] == 1:
                with board.store.db.write() as connection:
                    connection.execute(
                        "INSERT INTO meta(key,value) VALUES('race','1')"
                        " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                    )
            return {"attempt": attempts["n"]}, None

        outcome = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=racing_generate)
        self.assertEqual(attempts["n"], 1)
        self.assertEqual(json.loads(outcome.entry.body)["attempt"], 1)
        regenerated = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=racing_generate)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(json.loads(regenerated.entry.body)["attempt"], 2)
        cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=racing_generate)
        self.assertEqual(attempts["n"], 2)

    def test_entries_are_private_per_session_and_bounded(self):
        board = self.board()
        cache = ReadCache(board.store.db, max_entries=2)
        self.addCleanup(cache.close)
        generate = Mock(side_effect=stable_projection({"v": 1}))
        first = cache.serve(kind="console", key="k", session="s1", extra_marker=(), generate=generate)
        other = cache.serve(kind="console", key="k", session="s2", extra_marker=(), generate=generate)
        self.assertIsNot(first.entry, other.entry)
        self.assertEqual(generate.call_count, 2)
        # The bound evicts the least recently used entry, not other sessions'.
        cache.serve(kind="console", key="a", session="s1", extra_marker=(), generate=generate)
        cache.serve(kind="console", key="b", session="s1", extra_marker=(), generate=generate)
        self.assertLessEqual(len(cache._entries), 2)
        self.assertLessEqual(MAX_ENTRIES, 64)


if __name__ == "__main__":
    unittest.main()
