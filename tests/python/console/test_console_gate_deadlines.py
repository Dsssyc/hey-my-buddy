"""Gate lease expiries refresh a cached console snapshot over real HTTP.

B12 (BG-GATE/B5) of docs/acceptance/console-ui-and-board-fixes.md: the snapshot
cache may answer 304 only while every gate lease it counted is still alive. The
projection itself carries the active writer's ``expiresAt`` boundary, so a
running grant's expiry can never be frozen; a queued writer's queue lease and
an admitted reader's lease appear in the projection only as counts, and their
expiries are pinned by exactly one line — the ``gate_lease_deadline``
collection in ``console/reads.py``. Each scenario below holds the database,
the harness health scan and the console session state frozen and moves one
controlled clock shared by the store's lease stamps and the cache, so the
entry's own deadline is the only thing that can flip: just before the lease
boundary the old validator still answers 304 without a reprojection, just
after it the same validator must be answered with a fresh 200 whose gate
counts and phase show the expired lease gone, and only the refreshed validator
answers 304 again. With the lease collection removed in-process
(``gate_lease_deadline`` answering ``None``, the equivalent of dropping the
``reads.py`` line) each scenario must fail on the stale 304 instead of
erroring: the cache keeps serving, which is exactly the defect.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from console.test_console import ConsoleTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase
from support import FakeClock

from hey_my_buddy.console.read_cache import parse_boundary

#: The one instant both clocks start at: the store clock stamps the leases,
#: the console clock judges the cache deadlines, and they must never disagree.
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()


class GateLeaseSnapshotTests(ConsoleTestCase, WorkflowTestCase):
    """One controlled clock shared by the store's leases and the console cache."""

    require_login = False

    def setUp(self) -> None:
        super().setUp()
        self.fake = FakeClock()
        self.now = [EPOCH]

    def earliest_open_lease(self, board) -> float:
        """The earliest expiry over every open gate lease, read back from SQL.

        The check deliberately re-derives the boundary from the stored stamps
        instead of calling :func:`gate_lease_deadline`: the in-process mutation
        that must produce the stale-304 failure below replaces that one
        function, and a fixture guard that depends on it would fail as an
        error before the pinned behavior is ever exercised.
        """
        with board.store.db.read() as connection:
            stamps = [
                row["expires_at"]
                for row in connection.execute(
                    "SELECT expires_at FROM evaluation_writers WHERE state IN ('active','waiting')"
                    " UNION ALL SELECT expires_at FROM evaluation_readers WHERE released_at IS NULL"
                )
            ]
        moments = [parse_boundary(stamp) for stamp in stamps]
        self.assertTrue(moments and all(moment is not None for moment in moments), stamps)
        return min(moments)

    def aligned_board(self, **options):
        """A private board whose lease stamps and cache clock share one instant."""
        board = self.board(clock=self.fake, **options)
        board.console._clock = lambda: self.now[0]
        board.console.read_cache._clock = lambda: self.now[0]
        return board

    def advance(self, seconds: float) -> None:
        """Move the console cache clock and the store's lease clock together."""
        self.now[0] += seconds
        self.fake.advance(seconds)

    def read_snapshot(self, browser, *, if_none_match=None):
        headers = {"If-None-Match": if_none_match} if if_none_match is not None else None
        status, headers, body = browser.get("/api/console", headers=headers)
        return status, headers, body

    def projections(self, board) -> int:
        return board.console.read_cache.calls.get("console", {}).get("projections", 0)


class QueuedWriterLeaseExpiryTests(GateLeaseSnapshotTests):
    """A queued writer's queue lease expires while an older grant stays active."""

    def test_crossing_the_queue_lease_refreshes_the_cached_snapshot(self):
        board = self.aligned_board(writer_lease_seconds=600, writer_queue_seconds=120)
        revision = board.call("console_snapshot", {})["tableRevision"]
        active = board.call(
            "evaluation_write_begin",
            {"requestId": "gate-active", "expectedRevision": revision, "kind": "maintenance"},
        )
        queued = board.call(
            "evaluation_write_begin",
            {"requestId": "gate-queued", "expectedRevision": revision, "kind": "maintenance"},
        )
        self.assertEqual(active["state"], "active")
        self.assertEqual(active["expiresAt"], "2026-01-01T00:10:00.000Z")
        self.assertEqual(queued["state"], "waiting")
        self.assertEqual(queued["expiresAt"], "2026-01-01T00:02:00.000Z")
        _stated, browser = self.open_console(board)
        status, headers, body = self.read_snapshot(browser)
        self.assertEqual(status, 200, body[:300])
        gate = json.loads(body)["gate"]
        self.assertEqual(gate["phase"], "writing")
        self.assertEqual(gate["readers"], 0)
        self.assertEqual(gate["waitingWriters"], 1)
        self.assertEqual(gate["writer"]["writerId"], active["writerId"])
        # The only boundary the projection itself carries is the active
        # writer's grant — 479 seconds past the flip point below.
        self.assertAlmostEqual(parse_boundary(gate["writer"]["expiresAt"]), EPOCH + 600, places=6)
        # The earliest lease anywhere is exactly the queued writer's, so no
        # other deadline can fire before it.
        self.assertAlmostEqual(self.earliest_open_lease(board), EPOCH + 120, places=6)
        first = headers["etag"]
        projections = self.projections(board)
        # Inside the queue window the cache still serves the entry: 304 with no
        # reprojection and no database commit.
        self.advance(119)
        status, _headers, body = self.read_snapshot(browser, if_none_match=first)
        self.assertEqual(status, 304, body[:300])
        self.assertEqual(body, b"")
        self.assertEqual(self.projections(board), projections)
        # Crossing the queued writer's queue lease alone — the active grant is
        # still 479 seconds away and nothing was committed — must answer the
        # old validator with the refreshed gate: the expired queued writer no
        # longer counts.
        self.advance(2)
        status, headers, body = self.read_snapshot(browser, if_none_match=first)
        self.assertEqual(
            status,
            200,
            f"crossing the queued writer's queue lease must refresh the cached snapshot with the"
            f" updated gate counts, got {status}: the stale entry (waitingWriters=1, phase"
            f" 'writing' with the queued writer still counted) was served instead, so the gate"
            f" lease deadline was not collected",
        )
        gate = json.loads(body)["gate"]
        self.assertEqual(gate["phase"], "writing")
        self.assertEqual(gate["readers"], 0)
        self.assertEqual(gate["waitingWriters"], 0)
        self.assertEqual(gate["writer"]["writerId"], active["writerId"])
        self.assertAlmostEqual(parse_boundary(gate["writer"]["expiresAt"]), EPOCH + 600, places=6)
        self.assertNotEqual(headers["etag"], first)
        self.assertEqual(self.projections(board), projections + 1)
        # The refreshed entry is itself cacheable: with the board still
        # unchanged, only its own new validator answers 304.
        self.advance(9)
        status, _headers, body = self.read_snapshot(browser, if_none_match=headers["etag"])
        self.assertEqual(status, 304, body[:300])
        self.assertEqual(body, b"")
        self.assertEqual(self.projections(board), projections + 1)


class ReaderLeaseExpiryTests(GateLeaseSnapshotTests):
    """An admitted selection reader's lease expires with no writer anywhere."""

    def test_crossing_the_reader_lease_refreshes_the_cached_snapshot(self):
        board = self.aligned_board(reader_lease_seconds=120)
        reader = board.call("evaluation_reader_begin", {"kind": "selection"})
        self.assertEqual(reader["expiresAt"], "2026-01-01T00:02:00.000Z")
        _stated, browser = self.open_console(board)
        status, headers, body = self.read_snapshot(browser)
        self.assertEqual(status, 200, body[:300])
        gate = json.loads(body)["gate"]
        self.assertEqual(gate["phase"], "open")
        self.assertEqual(gate["readers"], 1)
        self.assertEqual(gate["waitingWriters"], 0)
        self.assertIsNone(gate["writer"])
        # The reader lease is the only lease and the only deadline: the
        # projection carries no writer boundary, so nothing else can fire.
        self.assertAlmostEqual(self.earliest_open_lease(board), EPOCH + 120, places=6)
        first = headers["etag"]
        projections = self.projections(board)
        self.advance(119)
        status, _headers, body = self.read_snapshot(browser, if_none_match=first)
        self.assertEqual(status, 304, body[:300])
        self.assertEqual(body, b"")
        self.assertEqual(self.projections(board), projections)
        self.advance(2)
        status, headers, body = self.read_snapshot(browser, if_none_match=first)
        self.assertEqual(
            status,
            200,
            f"crossing the admitted reader's lease must refresh the cached snapshot with the"
            f" updated gate counts, got {status}: the stale entry (readers=1) was served"
            f" instead, so the gate lease deadline was not collected",
        )
        gate = json.loads(body)["gate"]
        self.assertEqual(gate["phase"], "open")
        self.assertEqual(gate["readers"], 0)
        self.assertEqual(gate["waitingWriters"], 0)
        self.assertIsNone(gate["writer"])
        self.assertNotEqual(headers["etag"], first)
        self.assertEqual(self.projections(board), projections + 1)
        self.advance(9)
        status, _headers, body = self.read_snapshot(browser, if_none_match=headers["etag"])
        self.assertEqual(status, 304, body[:300])
        self.assertEqual(body, b"")
        self.assertEqual(self.projections(board), projections + 1)


if __name__ == "__main__":
    unittest.main()
