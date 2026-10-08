"""Conditional console reads over real HTTP: 304, gzip, on-demand preflight.

R01–R03 of docs/acceptance/console-reads-and-checkouts.md: an unchanged board
answers 304 with an empty body and without re-running the projection, the
serialization or the macro-task summary; every injected change source returns
new content; authentication always precedes the 304 decision; the periodic
snapshot carries no full task rows and never walks evidence directories; gzip
and identity negotiate correctly. Removing the cache, the compression or the
preflight split must fail these tests, not just slow them down.
"""
from __future__ import annotations

import gzip
import json
import os
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from console.test_console import Browser, ConsoleTestCase, http_call
from blackboard.tasks.test_workflow import WorkflowTestCase, CONFIGURATION

from hey_my_buddy.blackboard.store import backup


class ConsoleReadCacheTests(ConsoleTestCase, WorkflowTestCase):
    """The default login-free console: one local session, marker-checked reads."""

    require_login = False

    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()

    def open(self, board, **kwargs):
        _stated, browser = self.open_console(board, **kwargs)
        return browser

    def polls(self, browser, path="/api/console", count=3):
        """One fresh read plus ``count`` conditional re-reads; returns (first, replays)."""
        status, headers, body = browser.get(path)
        assert status == 200, body[:300]
        etag = headers["etag"]
        replays = []
        for _ in range(count):
            status, replay_headers, replay_body = browser.get(path, headers={"If-None-Match": etag})
            replays.append((status, replay_headers, replay_body))
        return (status, headers, body), replays

    # -- R01: unchanged means 304 with no recompute --------------------------
    def test_unchanged_polls_answer_304_without_reprojection(self):
        board = self.board()
        browser = self.open(board)
        first, replays = self.polls(browser)
        counts = board.console.read_cache.calls.get("console", {"projections": 0})
        for status, headers, body in replays:
            self.assertEqual(status, 304)
            self.assertEqual(body, b"")
            self.assertNotIn("content-length", headers)
            self.assertEqual(headers["etag"], first[1]["etag"])
            self.assertEqual(headers["cache-control"], "private, max-age=0, must-revalidate")
            self.assertEqual(headers["vary"], "Accept-Encoding")
        self.assertEqual(board.console.read_cache.calls["console"]["projections"], counts["projections"])
        self.assertEqual(board.console.read_cache.calls["console"]["serializations"], 1)

    def test_every_injected_change_source_returns_new_content(self):
        board = self.board()
        browser = self.open(board)
        _first, replays = self.polls(browser, count=1)
        etag = replays[0][1]["etag"]
        projections = lambda: board.console.read_cache.calls["console"]["projections"]

        def changed(tag):
            status, headers, body = browser.get("/api/console", headers={"If-None-Match": etag})
            self.assertEqual(status, 200, f"{tag}: {body[:200]}")
            self.assertNotEqual(headers["etag"], etag, tag)
            return headers["etag"]

        def recomputed(tag):
            """A change the projection read but whose content stayed identical.

            The marker still invalidates and the projection runs again; because
            the validator is content-addressed, byte-identical output answers
            304. That is the honest answer: nothing to resend.
            """
            before = board.console.read_cache.calls["console"]["projections"]
            status, _headers, body = browser.get("/api/console", headers={"If-None-Match": etag})
            after = board.console.read_cache.calls["console"]["projections"]
            self.assertEqual(status, 304, f"{tag}: {body[:200]}")
            self.assertEqual(after, before + 1, tag)
            return etag

        # A raw SQL change with no business event and no visible content: the
        # marker must not miss it, so the projection is recomputed. (A plain
        # task submission no longer appears in the slim snapshot at all; the
        # paged /api/tasks read owns that change and is covered below.)
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO meta(key,value) VALUES('console-read-test','1')"
                               " ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        etag = recomputed("raw SQL")
        # A raw SQL change that does reach the content: the table revision.
        with board.store.db.write() as connection:
            connection.execute("UPDATE evaluation_state SET table_revision=table_revision+1 WHERE id=1")
        etag = changed("table revision")
        # A harness health observation revision.
        with board.store.db.write() as connection:
            connection.execute("UPDATE harness_health SET revision=revision+1, record_json='{}' WHERE adapter='dsh'")
        etag = changed("harness health")
        # A gate record: a queued writer changes the gate view.
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO evaluation_writers(writer_id,request_id,kind,state,generation,expected_revision,"
                "token_verifier,requested_at,expires_at) VALUES('w-read','req-read','maintenance','waiting',"
                "1,0,'v','2026-01-01T00:00:00.000Z','2030-01-01T00:00:00Z')")
        etag = changed("gate queue")
        # Console asset availability is part of the marker.
        (board.console.assets_dir).mkdir(parents=True, exist_ok=True)
        (board.console.assets_dir / "index.html").write_text("<!doctype html><div id=root></div>")
        changed("assets")
        self.assertGreater(projections(), 1)

    def test_authentication_precedes_the_304_decision(self):
        # In login-free mode every loopback request is the local session by
        # design; the 304-never-before-authentication boundary is exercised in
        # login mode by ConsoleReadSessionIsolationTests below.
        board = self.board()
        browser = self.open(board)
        first, _replays = self.polls(browser, count=1)
        status, _headers, body = browser.get("/api/console", headers={"If-None-Match": first[1]["etag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    # -- R02: slim snapshot, on-demand preflight ------------------------------
    def test_periodic_snapshot_is_slim_and_never_walks_evidence(self):
        board = self.board()
        (board.directory / "attempts" / "run-1" / "attempt-1").mkdir(parents=True)
        (board.directory / "attempts" / "run-1" / "attempt-1" / "evidence.json").write_text("{}")
        browser = self.open(board)
        with mock.patch.object(backup, "preflight", wraps=backup.preflight) as preflight:
            first, replays = self.polls(browser, count=2)
            self.assertEqual(preflight.call_count, 0)
        snapshot = json.loads(first[2])
        self.assertNotIn("backupPreflight", snapshot)
        self.assertNotIn("runs", snapshot["tasks"])
        self.assertEqual(snapshot["tasks"], {"pendingCount": 0})
        for _status, _headers, body in replays:
            self.assertEqual(body, b"")

    def test_backup_preflight_is_computed_on_demand_only(self):
        board = self.board()
        (board.directory / "attempts" / "run-1" / "attempt-1").mkdir(parents=True)
        (board.directory / "attempts" / "run-1" / "attempt-1" / "evidence.json").write_text("{}")
        expected = backup.preflight(board.directory)
        browser = self.open(board)
        with mock.patch.object(backup, "preflight", wraps=backup.preflight) as preflight:
            status, headers, body = browser.get("/api/backup-preflight")
            self.assertEqual(status, 200, body[:300])
            self.assertEqual(preflight.call_count, 1)
            self.assertEqual(json.loads(body), expected)
            etag = headers["etag"]
            status, replay_headers, replay_body = browser.get(
                "/api/backup-preflight", headers={"If-None-Match": etag}
            )
            self.assertEqual(status, 304)
            self.assertEqual(replay_body, b"")
            # The report is still recomputed per explicit read; only the
            # transfer is conditional.
            status, _headers, _body = browser.get("/api/backup-preflight")
            self.assertEqual(status, 200)
            self.assertEqual(preflight.call_count, 3)

    def test_pending_count_counts_distinct_roots_awaiting_their_host(self):
        board = self.board()
        self.register(board)
        # Root parked on its Host.
        root = self.submit(board, request_id="pending-root", cwd=str(self.workdir("pending-root")))
        first = self.claim(board, claim_request_id="pending-c1")
        self.finish_turn(board, first, disposition="assistance")
        # A second root still queued does not count.
        self.submit(board, request_id="pending-running", cwd=str(self.workdir("pending-running")))
        status, _headers, body = self.open(board).get("/api/console")
        self.assertEqual(json.loads(body)["tasks"], {"pendingCount": 1})
        view = board.call("workflow_get", {"runId": root["runId"]})
        self.assertTrue(view["awaitingHost"])

    # -- R03: negotiation ------------------------------------------------------
    def test_gzip_identity_and_304_representations(self):
        board = self.board()
        browser = self.open(board)
        status, identity_headers, identity_body = browser.get("/api/console")
        self.assertEqual(status, 200)
        self.assertNotIn("content-encoding", identity_headers)
        self.assertEqual(int(identity_headers["content-length"]), len(identity_body))
        status, gzip_headers, gzip_body = browser.get("/api/console", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(gzip_headers["content-encoding"], "gzip")
        self.assertEqual(int(gzip_headers["content-length"]), len(gzip_body))
        self.assertLess(len(gzip_body), len(identity_body))
        self.assertEqual(gzip.decompress(gzip_body), identity_body)
        self.assertEqual(gzip_headers["vary"], "Accept-Encoding")
        # q=0 withdraws gzip; an explicit identity-only list never gets gzip.
        status, headers, body = browser.get("/api/console", headers={"Accept-Encoding": "gzip;q=0"})
        self.assertEqual(status, 200)
        self.assertNotIn("content-encoding", headers)
        self.assertEqual(body, identity_body)
        # Identity stays acceptable by default even when only other codings are
        # named; withdrawing it explicitly while gzip is unavailable is a 406.
        status, headers, body = browser.get("/api/console", headers={"Accept-Encoding": "br"})
        self.assertEqual(status, 200, body[:200])
        self.assertNotIn("content-encoding", headers)
        self.assertEqual(body, identity_body)
        status, _headers, body = browser.get("/api/console", headers={"Accept-Encoding": "br, identity;q=0"})
        self.assertEqual(status, 406, body[:200])
        # The identity validator answers 304 only for the identity
        # representation: with gzip negotiated the conditional is compared
        # against the gzip representation's own validator, so a borrowed
        # validator returns the negotiated body, not 304.
        status, headers, body = browser.get(
            "/api/console", headers={"If-None-Match": identity_headers["etag"], "Accept-Encoding": "gzip"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-encoding"], "gzip")
        self.assertEqual(gzip.decompress(body), identity_body)
        status, _headers, body = browser.get(
            "/api/console", headers={"If-None-Match": gzip_headers["etag"], "Accept-Encoding": "gzip"}
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_each_content_coding_has_its_own_strong_validator(self):
        """RFC 9110 §8.8.3: strong entity-tags differ per content coding.

        Sharing one strong validator between identity and gzip would let a
        client treat a gzip body as byte-identical to identity (and would
        mis-borrow validators across codings in conditional requests). The
        previous implementation shared them; this is the corrected contract.
        """
        board = self.board()
        browser = self.open(board)
        _status, identity_headers, _body = browser.get("/api/console", headers={"Accept-Encoding": "identity"})
        _status, gzip_headers, _gzipped = browser.get("/api/console", headers={"Accept-Encoding": "gzip"})
        for etag in (identity_headers["etag"], gzip_headers["etag"]):
            self.assertTrue(etag.startswith('"'), etag)
            self.assertNotIn("W/", etag)
        self.assertNotEqual(identity_headers["etag"], gzip_headers["etag"])
        # Weak comparison still matches the representation's own validator.
        status, _headers, body = browser.get(
            "/api/console",
            headers={"If-None-Match": "W/" + gzip_headers["etag"], "Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        # The on-demand preflight follows the same per-coding rule.
        _status, preflight_identity, _body = browser.get(
            "/api/backup-preflight", headers={"Accept-Encoding": "identity"}
        )
        _status, preflight_gzip, _gzipped = browser.get(
            "/api/backup-preflight", headers={"Accept-Encoding": "gzip"}
        )
        self.assertNotEqual(preflight_identity["etag"], preflight_gzip["etag"])
        status, _headers, _body = browser.get(
            "/api/backup-preflight",
            headers={"If-None-Match": preflight_gzip["etag"], "Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 304)

    def test_idle_polling_keeps_borrowing_the_health_scan(self):
        """A cache hit must not starve the 180-second harness scan (R01).

        The borrowed scan used to fire only while generating the projection, so
        an unchanged board that kept answering from the cache could delay the
        scan up to the conservative one-hour cache lifetime. Crossing
        ``scanAfter`` re-enters the projection (and kicks the scan) while an
        ordinary short-window hit still does not recompute anything.
        """
        board = self.board()
        browser = self.open(board)
        now = [time.time()]
        board.console._clock = lambda: now[0]
        board.console.read_cache._clock = lambda: now[0]

        def stamp(delta):
            return datetime.fromtimestamp(now[0] + delta, timezone.utc).isoformat()

        health = [{"adapter": "codex", "status": "ready", "available": True,
                   "scanAfter": stamp(180), "expiresAt": stamp(3600)}]
        board.service.automatic_discovery = True
        with mock.patch.object(board.service.harnesses, "all", return_value=health), \
                mock.patch.object(board.service.harnesses, "kick") as kick:
            status, headers, _body = browser.get("/api/console")
            self.assertEqual(status, 200)
            self.assertEqual(kick.call_count, 1)
            projections = board.console.read_cache.calls["console"]["projections"]
            # An ordinary unchanged poll inside the scan window: no kick, no
            # reprojection, still no evidence walk.
            now[0] += 3
            status, _headers, body = browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
            self.assertEqual(status, 304)
            self.assertEqual(body, b"")
            self.assertEqual(kick.call_count, 1)
            self.assertEqual(board.console.read_cache.calls["console"]["projections"], projections)
            # The scan window elapses with no database change: the next poll
            # re-enters the projection and borrows the scan again.
            now[0] += 181
            browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
            self.assertGreater(kick.call_count, 1)
            self.assertEqual(
                board.console.read_cache.calls["console"]["projections"], projections + 1
            )

    def test_idle_polling_kicks_again_while_health_records_are_unknown(self):
        """The cold case: records without scanAfter still re-borrow the scan.

        A first-time unknown health record carries no ``scanAfter`` boundary,
        so while discovery is automatic the projection caps its own deadline at
        the scan interval; an idle console re-kicks at that cadence instead of
        waiting out the conservative maximum lifetime.
        """
        board = self.board()
        browser = self.open(board)
        now = [time.time()]
        board.console._clock = lambda: now[0]
        board.console.read_cache._clock = lambda: now[0]
        health = [{"adapter": "codex", "status": "unknown", "available": False,
                   "scanAfter": None, "expiresAt": None}]
        board.service.automatic_discovery = True
        with mock.patch.object(board.service.harnesses, "all", return_value=health), \
                mock.patch.object(board.service.harnesses, "kick") as kick:
            status, _headers, _body = browser.get("/api/console")
            self.assertEqual(status, 200)
            self.assertEqual(kick.call_count, 1)
            now[0] += 181
            browser.get("/api/console")
            self.assertGreater(kick.call_count, 1)
        # Without automatic discovery the projection never borrows the scan.
        board.console.read_cache.invalidate()
        board.service.automatic_discovery = False
        with mock.patch.object(board.service.harnesses, "kick") as kick:
            browser.get("/api/console")
            self.assertEqual(kick.call_count, 0)

    def test_a_database_replacement_invalidates_every_cached_route(self):
        """Round-5 reviewed defect over real HTTP: no route keeps a stale epoch.

        After a same-schema database replacement the reconnected marker may
        re-read the data_version other routes' entries were cached under; the
        connection epoch must invalidate them all. The snapshot visibly
        changes (tableRevision), while the tasks route re-enters its
        projection even though its content is byte-identical — an honest
        recompute-304, never a stale cache hit.
        """
        board = self.board()
        browser = self.open(board)
        _status, console_headers, console_body = browser.get("/api/console")
        self.assertEqual(json.loads(console_body)["tableRevision"], 0)
        _status, tasks_headers, _body = browser.get("/api/tasks?limit=5")
        import sqlite3
        from contextlib import closing

        replacement = board.store.directory / "replacement.sqlite3"
        with closing(sqlite3.connect(board.store.db.path)) as source, closing(sqlite3.connect(replacement)) as target:
            source.backup(target)
            target.execute("UPDATE evaluation_state SET table_revision=97 WHERE id=1")
            target.commit()
        os.replace(replacement, board.store.db.path)
        status, _headers, body = browser.get(
            "/api/console", headers={"If-None-Match": console_headers["etag"]}
        )
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(json.loads(body)["tableRevision"], 97)
        self.assertNotEqual(_headers["etag"], console_headers["etag"])
        projections = board.console.read_cache.calls["tasks"]["projections"]
        status, _headers, replay = browser.get(
            "/api/tasks?limit=5", headers={"If-None-Match": tasks_headers["etag"]}
        )
        self.assertEqual(
            board.console.read_cache.calls["tasks"]["projections"], projections + 1,
            "The tasks page belongs to the replaced epoch: the conditional read must re-enter "
            "the projection instead of serving the pre-replacement cache entry",
        )
        self.assertIn(status, (200, 304))

    def test_task_objective_and_timeline_reads_are_conditional(self):
        board = self.board()
        submitted = self.submit(
            board, request_id="cond-1", cwd=str(self.workdir("cond-1")), objective={"title": "Conditional"}
        )
        browser = self.open(board)
        for path in ("/api/tasks?limit=5", "/api/objectives?limit=5",
                     f"/api/objectives/{submitted['objectiveId']}/timeline"):
            with self.subTest(path=path):
                status, headers, body = browser.get(path)
                self.assertEqual(status, 200, body[:300])
                status, replay_headers, replay_body = browser.get(path, headers={"If-None-Match": headers["etag"]})
                self.assertEqual(status, 304, replay_body[:300])
                self.assertEqual(replay_body, b"")
                self.assertEqual(replay_headers["etag"], headers["etag"])
        # A new run changes the board: the conditional reads answer with content.
        self.submit(board, request_id="cond-2", cwd=str(self.workdir("cond-2")))
        for path in ("/api/tasks?limit=5", "/api/objectives?limit=5"):
            with self.subTest(path=path):
                status, _headers, _body = browser.get(path, headers={"If-None-Match": "nope"})
                self.assertEqual(status, 200)
        counts = board.console.read_cache.calls
        self.assertEqual(counts["tasks"]["projections"], 2)
        # One kind covers the objective routes: two list generations plus the
        # timeline's single generation.
        self.assertEqual(counts["objectives"]["projections"], 3)


class ConsoleReadSessionIsolationTests(ConsoleTestCase, WorkflowTestCase):
    """Login mode: cached bodies never cross sessions; revocation beats 304."""

    require_login = True

    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()

    def test_sessions_never_share_a_cached_response(self):
        board = self.board()
        _stated, first = self.open_console(board)
        # A second independent login gets its own entry and its own etag.
        second = Browser(board.console.start()["url"])
        status_a, headers_a, body_a = first.get("/api/console")
        status_b, headers_b, body_b = second.get("/api/console")
        self.assertEqual((status_a, status_b), (200, 200))
        self.assertNotEqual(headers_a["etag"], headers_b["etag"])
        self.assertNotEqual(json.loads(body_a)["csrfToken"], json.loads(body_b)["csrfToken"])
        # Session B presenting A's validator still gets its own body.
        status, _headers, body = second.get("/api/console", headers={"If-None-Match": headers_a["etag"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["csrfToken"], json.loads(body_b)["csrfToken"])
        self.assertEqual(board.console.read_cache.calls["console"]["projections"], 2)
        # Authentication precedes the 304 decision: without a cookie no read,
        # and a revoked session is refused before any 304 could leave.
        anonymous = Browser(second.origin + "/")
        status, _headers, body = anonymous.get("/api/console", headers={"If-None-Match": headers_b["etag"]})
        self.assertEqual(status, 401, body[:300])
        status, _headers, _body = first.command(
            "console_session_revoke", {"sessionId": json.loads(body_b)["consoleSession"]["id"]},
            csrf=json.loads(body_a)["csrfToken"],
        )
        self.assertEqual(status, 200, _body[:300])
        status, _headers, body = second.get("/api/console", headers={"If-None-Match": headers_b["etag"]})
        self.assertEqual(status, 401, body[:300])

    def test_a_cached_session_sees_another_session_login_without_a_database_change(self):
        """The sessions file is not SQL; the console marker carries it itself.

        ``ConsoleSessions.create``/``redeem`` write only the sessions JSON, so
        without the session-set fingerprint in the read marker a second login
        leaves the first session's cached snapshot answering 304 with a stale
        session list (the reviewed defect).
        """
        board = self.board()
        _entry, first = self.open_console(board)
        status, headers, body = first.get("/api/console")
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(len(json.loads(body)["consoleAccess"]["sessions"]), 1)
        second = Browser(board.console.start()["url"])
        status, _headers, _body = second.get("/api/console")
        self.assertEqual(status, 200, _body[:300])
        status, _headers, body = first.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(len(json.loads(body)["consoleAccess"]["sessions"]), 2)

    def test_cross_session_revoke_and_logout_refresh_a_cached_snapshot(self):
        board = self.board()
        _entry, first = self.open_console(board)
        # Fixed activity stamp: only set changes (not minute crossings) move
        # the fingerprint, so the final stable-state 304 cannot flake.
        fixed = time.time()
        board.console._sessions.clock = lambda: fixed
        status, headers, body = first.get("/api/console")
        self.assertEqual(status, 200)
        csrf = json.loads(body)["csrfToken"]
        second = Browser(board.console.start()["url"])
        status, _headers, _body = second.get("/api/console")
        self.assertEqual(status, 200, _body[:300])
        status, refreshed, _body = first.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(_body)["consoleAccess"]["sessions"]), 2)
        # A revokes B: A's cached two-session snapshot must not answer 304.
        status, _headers, _body = first.command(
            "console_session_revoke", {"sessionId": json.loads(second.get("/api/console")[2])
                                       ["consoleSession"]["id"]},
            csrf=csrf,
        )
        self.assertEqual(status, 200, _body[:300])
        status, _headers, body = first.get("/api/console", headers={"If-None-Match": refreshed["etag"]})
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(len(json.loads(body)["consoleAccess"]["sessions"]), 1)
        stable = _headers["etag"]
        # B logs back in and out through the normal entry: the login must
        # again invalidate A's cached single-session body, and the logout
        # again returns the display to one session (a fresh body each time;
        # only a state that recurs byte-identically may honestly 304).
        second = Browser(board.console.start()["url"])
        status, _headers, _body = second.get("/api/console")
        self.assertEqual(status, 200, _body[:300])
        second_csrf = json.loads(_body)["csrfToken"]
        status, two_sessions_headers, _body = first.get("/api/console", headers={"If-None-Match": stable})
        self.assertEqual(status, 200, _body[:300])
        self.assertEqual(len(json.loads(_body)["consoleAccess"]["sessions"]), 2)
        status, _headers, _body = second.command("console_logout", {}, csrf=second_csrf)
        self.assertEqual(status, 200, _body[:300])
        status, _headers, body = first.get(
            "/api/console", headers={"If-None-Match": two_sessions_headers["etag"]}
        )
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(len(json.loads(body)["consoleAccess"]["sessions"]), 1)
        # The set is stable again: the same validator now answers 304.
        status, _headers, body = first.get("/api/console", headers={"If-None-Match": _headers["etag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_activity_minute_change_invalidates_a_cached_snapshot(self):
        """A session's own activity minute is displayed; it is a marker source.

        Polls inside one display minute must stay cache hits — a session must
        not reproject every 3-second poll because its own authentication
        touches ``last_seen`` — while activity landing in the next minute
        refreshes every cached snapshot that displays it.
        """
        board = self.board()
        _entry, browser = self.open_console(board)
        now = [time.time()]
        board.console._clock = lambda: now[0]
        board.console.read_cache._clock = lambda: now[0]
        board.console._sessions.clock = lambda: now[0]
        status, headers, body = browser.get("/api/console")
        self.assertEqual(status, 200, body[:300])
        displayed = json.loads(body)["consoleAccess"]["sessions"][0]["lastSeen"]
        self.assertEqual(displayed, int(now[0] // 60) * 60)
        # Same-minute re-poll: still a cache hit, no recomputation.
        status, _headers, replay = browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 304)
        self.assertEqual(replay, b"")
        serializations = board.console.read_cache.calls["console"]["serializations"]
        # The session's own next activity lands in the next display minute.
        now[0] += 61
        status, _headers, body = browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(
            json.loads(body)["consoleAccess"]["sessions"][0]["lastSeen"], int(now[0] // 60) * 60
        )
        self.assertEqual(board.console.read_cache.calls["console"]["serializations"], serializations + 1)

    def test_minute_boundary_bounds_a_login_snapshot_without_activity(self):
        """The next display minute is a deadline even with no activity.

        A login-mode entry never outlives its own display grain: crossing the
        minute boundary re-enters the projection once. With no session activity
        the content is unchanged, so the conditional read honestly answers 304
        — the recomputation, not the status, is the pinned behavior.
        """
        board = self.board()
        _entry, browser = self.open_console(board)
        now = [time.time()]
        board.console._clock = lambda: now[0]
        board.console.read_cache._clock = lambda: now[0]
        # A fixed activity stamp behind the console clock: no fingerprint
        # change during the test, so only the minute deadline can fire.
        fixed_stamp = now[0] - 120
        board.console._sessions.clock = lambda: fixed_stamp
        status, headers, _body = browser.get("/api/console")
        self.assertEqual(status, 200)
        projections = board.console.read_cache.calls["console"]["projections"]
        now[0] += 61
        status, replay_headers, body = browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 304, body[:300])
        self.assertEqual(body, b"")
        self.assertEqual(board.console.read_cache.calls["console"]["projections"], projections + 1)
        status, _headers, body = browser.get("/api/console", headers={"If-None-Match": replay_headers["etag"]})
        self.assertEqual(status, 304, body[:300])

    def test_access_setting_changes_invalidate_the_snapshot(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        status, headers, body = browser.get("/api/console")
        self.assertEqual(status, 200)
        csrf = json.loads(body)["csrfToken"]
        status, _headers, _body = browser.command(
            "console_access_set", {"requireLogin": False, "expectedRevision": 0}, csrf=csrf
        )
        self.assertEqual(status, 200, _body[:300])
        # Login-free mode reads through the local session; the access revision
        # changed, so the old validator must not answer 304.
        status, _headers, replay = browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 200, replay[:300])
        snapshot = json.loads(replay)
        self.assertFalse(snapshot["consoleAccess"]["requireLogin"])
        self.assertNotEqual(headers["etag"], _headers["etag"])

    def test_access_revision_alone_refreshes_the_same_cached_session(self):
        """The access revision is a marker component on its own (R15).

        The settings test above flips ``requireLogin``, so deleting
        ``access_revision`` from the marker would still pass it: the login-mode
        switch and its session replacement move the marker themselves. Here one
        valid logged-in session keeps its identity while the clocks, the
        database, the assets and the session set all stay frozen, and only the
        console access revision moves — a controlled revision fact injected
        under this private fixture, not a real access-settings change. The old
        validator must then answer 200 with the new revision, and only the new
        validator may answer 304.
        """
        board = self.board()
        _entry, browser = self.open_console(board)
        now = [time.time()]
        board.console._clock = lambda: now[0]
        board.console.read_cache._clock = lambda: now[0]
        board.console._sessions.clock = lambda: now[0]
        status, headers, body = browser.get("/api/console")
        self.assertEqual(status, 200, body[:300])
        revision = json.loads(body)["consoleAccess"]["revision"]
        # With every invalidation source frozen, the cached representation is
        # an honest cache hit.
        status, _headers, replay = browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 304, replay[:300])
        self.assertEqual(replay, b"")
        # The injected revision alone must move the marker.
        with board.console._lock:
            board.console.access_revision = revision + 1
        status, refreshed, body = browser.get("/api/console", headers={"If-None-Match": headers["etag"]})
        self.assertEqual(status, 200, body[:300])
        self.assertNotEqual(refreshed["etag"], headers["etag"])
        self.assertEqual(json.loads(body)["consoleAccess"]["revision"], revision + 1)
        status, _headers, replay = browser.get("/api/console", headers={"If-None-Match": refreshed["etag"]})
        self.assertEqual(status, 304, replay[:300])
        self.assertEqual(replay, b"")


if __name__ == "__main__":
    unittest.main()
