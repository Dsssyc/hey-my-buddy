"""CLI-only console entry: strict local options, browser launch and fenced waits.

Every case drives the real ``hey_my_buddy.cli.main`` module and the real ``hey_my_buddy.cli.console_cli``
module with a fake in-process RPC, a fake non-autostart board client and a mocked
browser. No daemon is started, no daily state is touched and no real browser is opened.
"""
from __future__ import annotations

import contextlib
import io
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hey_my_buddy.cli.main as cli
import hey_my_buddy.cli.console_cli as console_cli
import hey_my_buddy.protocol.transport as transport
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.errors import BoardError

CONSOLE_ID = "0123456789abcdef01234567"
REPLACEMENT_ID = "fedcba9876543210fedcba98"
TICKET = "A-_b9" * 8 + "xyZ"  # exactly 43 URL-safe characters
ENTRY_URL = f"http://127.0.0.1:8123/launch/{TICKET}"
OPEN_REPLY = {
    "url": ENTRY_URL,
    "consoleId": CONSOLE_ID,
    "expiresAt": "2026-09-26T07:30:00Z",
    "alreadyRunning": False,
    "assetsBuilt": True,
    "running": True,
}
RUNNING = {"running": True, "consoleId": CONSOLE_ID}
CLOSED = {"running": False, "consoleId": None}


def open_reply(**overrides):
    """One lifecycle open reply with the exact contract shape, plus explicit overrides."""
    return {**OPEN_REPLY, **overrides}


def without(reply: dict, *keys: str) -> dict:
    return {key: value for key, value in reply.items() if key not in keys}


class FakeRpc:
    """One recorded fake in-process RPC surface for the opening call."""

    def __init__(self, reply: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.reply = dict(OPEN_REPLY if reply is None else reply)

    def __call__(self, method: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        return dict(self.reply)


class FakeClient:
    """A non-autostart BoardClient stand-in for the status/close/wait calls."""

    def __init__(
        self,
        statuses: list | None = None,
        error: BoardError | None = None,
        close_reply: dict | None = None,
        close_error: BoardError | None = None,
    ):
        self.calls: list[tuple[str, dict]] = []
        self.statuses = list(statuses or [])
        self.error = error
        self.close_reply = dict({"closed": True} if close_reply is None else close_reply)
        self.close_error = close_error

    def call(self, operation: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        self.calls.append((operation, params))
        if params.get("action") == "close":
            if self.close_error is not None:
                raise self.close_error
            return dict(self.close_reply)
        if self.error is not None:
            raise self.error
        if not self.statuses:
            raise AssertionError("the wait polled more often than the fixture allows")
        return self.statuses.pop(0)


class FakeSleep:
    """A deterministic sleep that can interrupt the wait like Ctrl-C does."""

    def __init__(self, interrupt_after: int | None = None):
        self.intervals: list[float] = []
        self.interrupt_after = interrupt_after

    def __call__(self, seconds: float) -> None:
        self.intervals.append(seconds)
        if self.interrupt_after is not None and len(self.intervals) == self.interrupt_after:
            raise KeyboardInterrupt


class ConsoleCliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # This process runs inside a Buddy attempt, so clear both scoped-credential
        # variables for every case and restore them afterwards.
        names = ("BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE")
        previous = {name: os.environ.get(name) for name in names}
        for name in names:
            os.environ.pop(name, None)

        def restore() -> None:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.addCleanup(restore)

    def run_cli(self, *arguments: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(list(arguments))
        text = stdout.getvalue().strip()
        return code, (json.loads(text) if text else {}), stderr.getvalue()


class LocalOptionTests(ConsoleCliTestCase):
    """Strict local parsing: wrong types, unknown fields and misplaced options."""

    def test_default_fixed_loopback_url_opens_without_a_ticket(self):
        url = "http://127.0.0.1:8123/"
        opener = mock.Mock(return_value=True)
        result = console_cli.run({}, call_service=FakeRpc(open_reply(url=url, expiresAt=None)), browser_open=opener)
        self.assertEqual(result["url"], url)
        opener.assert_called_once_with(url, new=2)
        for invalid in (url + "other", url + "?x=1", url + "#x", url.replace("127.0.0.1", "localhost")):
            self.assertFalse(console_cli.is_launch_url(invalid))

    def test_open_keeps_only_the_lifecycle_action_and_defaults_the_local_booleans(self):
        for params in ({"action": "open"}, {}):
            with self.subTest(params=params):
                self.assertEqual(
                    console_cli.parse_request(params),
                    console_cli.Request("open", {"action": "open"}, True, False),
                )

    def test_status_and_close_forward_only_their_own_validated_fields(self):
        self.assertEqual(
            console_cli.parse_request({"action": "status"}),
            console_cli.Request("status", {"action": "status"}, True, False),
        )
        self.assertEqual(
            console_cli.parse_request({"action": "close"}),
            console_cli.Request("close", {"action": "close"}, True, False),
        )
        self.assertEqual(
            console_cli.parse_request({"action": "close", "expectedConsoleId": CONSOLE_ID}),
            console_cli.Request("close", {"action": "close", "expectedConsoleId": CONSOLE_ID}, True, False),
        )

    def test_wrong_types_unknown_fields_and_misplaced_local_options_are_refused(self):
        cases = {
            "action": {"action": "bogus"},
            "action type": {"action": 3},
            "params type": [],
            "browser string": {"action": "open", "browser": "yes"},
            "browser integer": {"action": "open", "browser": 1},
            "browser null": {"action": "open", "browser": None},
            "wait string": {"action": "open", "wait": "false"},
            "wait integer": {"action": "open", "wait": 0},
            "wait null": {"action": "open", "wait": None},
            "unknown open field": {"action": "open", "unexpected": True},
            "unknown status field": {"action": "status", "foo": 1},
            "open expectedConsoleId": {"action": "open", "expectedConsoleId": CONSOLE_ID},
            "status expectedConsoleId": {"action": "status", "expectedConsoleId": CONSOLE_ID},
            "status browser": {"action": "status", "browser": False},
            "status wait": {"action": "status", "wait": True},
            "close browser": {"action": "close", "browser": False},
            "close wait": {"action": "close", "wait": True},
        }
        for name, params in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(BoardError) as refused:
                    console_cli.parse_request(params)
                self.assertEqual(refused.exception.code, "INVALID_ARGUMENT")

    def test_expected_console_id_is_exactly_24_lowercase_hex_without_normalization(self):
        cases = {
            "explicit null": None,
            "true": True,
            "false": False,
            "empty": "",
            "blank": "   ",
            "padded": f" {CONSOLE_ID} ",
            "trailing space": f"{CONSOLE_ID} ",
            "short by one": CONSOLE_ID[:-1],
            "long by one": CONSOLE_ID + "0",
            "uppercase": CONSOLE_ID.upper(),
            "non hex": "0123456789abcdef0123456g",
            "dashed uuid": "01234567-89ab-cdef-0123-456789abcdef",
            "0x prefix": "0x" + CONSOLE_ID[2:],
            "integer": 3,
            "list": [],
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(BoardError) as refused:
                    console_cli.parse_request({"action": "close", "expectedConsoleId": value})
                self.assertEqual(refused.exception.code, "INVALID_ARGUMENT")
        # A valid id is forwarded byte-identically: no trimming, case folding or decoding.
        self.assertEqual(
            console_cli.parse_request({"action": "close", "expectedConsoleId": CONSOLE_ID}).rpc_params,
            {"action": "close", "expectedConsoleId": CONSOLE_ID},
        )

    def test_invalid_arguments_are_refused_before_any_rpc_browser_or_client(self):
        rpc = FakeRpc()
        browser = mock.Mock(side_effect=AssertionError("no browser may be launched"))
        client = mock.Mock(side_effect=AssertionError("no board client may be built"))
        params_cases = (
            {"action": "open", "browser": "yes"},
            {"action": "open", "unknown": 1},
            {"action": "status", "wait": True},
            {"action": "close", "browser": False},
            {"action": "close", "expectedConsoleId": None},
            {"action": "close", "expectedConsoleId": "not-a-console-id"},
        )
        for params in params_cases:
            with self.subTest(params=params):
                with mock.patch.object(transport, "call_service", rpc), mock.patch.object(
                    console_cli.webbrowser, "open", browser
                ), mock.patch("hey_my_buddy.protocol.client.BoardClient", client):
                    code, result, _stderr = self.run_cli("console", json.dumps(params))
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(rpc.calls, [])
        self.assertEqual(browser.call_args_list, [])
        self.assertEqual(client.call_args_list, [])


class CredentialTests(ConsoleCliTestCase):
    """An attempt-scoped credential never reaches a local action or an RPC."""

    def test_environment_credential_is_refused_before_anything_happens(self):
        rpc = FakeRpc()
        browser = mock.Mock(side_effect=AssertionError("no browser may be launched"))
        with mock.patch.dict(os.environ, {"BUDDY_AGENT_CREDENTIAL": "scoped-token"}):
            with mock.patch.object(transport, "call_service", rpc), mock.patch.object(
                console_cli.webbrowser, "open", browser
            ):
                code, result, stderr = self.run_cli("console", '{"action":"open","wait":true}')
            # The credential is never stripped or borrowed: it stays in the environment.
            self.assertEqual(os.environ["BUDDY_AGENT_CREDENTIAL"], "scoped-token")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "FORBIDDEN")
        self.assertNotIn("scoped-token", json.dumps(result))
        self.assertEqual(stderr, "")
        self.assertEqual(rpc.calls, [])
        self.assertEqual(browser.call_args_list, [])

    def test_credential_file_is_refused_the_same_way(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credential.json"
            path.write_text(json.dumps({"token": "file-token"}))
            os.chmod(path, 0o600)
            rpc = FakeRpc()
            with mock.patch.dict(os.environ, {"BUDDY_AGENT_CREDENTIAL_FILE": str(path)}):
                with mock.patch.object(transport, "call_service", rpc):
                    code, result, _stderr = self.run_cli("console", '{"action":"status"}')
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "FORBIDDEN")
        self.assertEqual(rpc.calls, [])

    def test_malformed_credential_pins_keep_the_existing_unauthorized_semantics(self):
        rpc = FakeRpc()
        environments = (
            {"BUDDY_AGENT_CREDENTIAL": " "},
            {"BUDDY_AGENT_CREDENTIAL_FILE": "/absent/credential.json"},
        )
        for environment in environments:
            with self.subTest(environment=sorted(environment)):
                with mock.patch.dict(os.environ, environment):
                    with mock.patch.object(transport, "call_service", rpc):
                        code, result, _stderr = self.run_cli("console", '{"action":"status"}')
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(rpc.calls, [])


class OpenTests(ConsoleCliTestCase):
    def run_open(self, params, *, reply=None, browser_result=True, browser_error=None):
        rpc = FakeRpc(reply)
        if browser_error is not None:
            browser = mock.Mock(side_effect=browser_error)
        else:
            browser = mock.Mock(return_value=browser_result)
        stderr = io.StringIO()
        result = console_cli.run(params, call_service=rpc, browser_open=browser, stderr=stderr)
        return rpc, browser, stderr, result

    def test_open_launches_the_default_browser_once_and_keeps_the_lifecycle_reply(self):
        rpc, browser, stderr, result = self.run_open({"action": "open"})
        self.assertEqual(rpc.calls, [("console", {"action": "open"})])
        self.assertEqual(browser.call_args_list, [mock.call(ENTRY_URL, new=2)])
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(result, {**OPEN_REPLY, "browserOpened": True})
        self.assertNotIn("wait", result)

    def test_browser_false_never_calls_webbrowser_and_still_reports_the_entry_url(self):
        rpc, browser, _stderr, result = self.run_open({"action": "open", "browser": False})
        self.assertEqual(rpc.calls, [("console", {"action": "open"})])
        self.assertEqual(browser.call_args_list, [])
        self.assertIs(result["browserOpened"], False)
        self.assertEqual(result["url"], ENTRY_URL)

    def test_a_failed_launch_returns_the_one_time_url_without_retrying(self):
        _rpc, browser, _stderr, result = self.run_open({"action": "open"}, browser_result=False)
        self.assertEqual(browser.call_count, 1)
        self.assertIs(result["browserOpened"], False)
        self.assertEqual(result["url"], ENTRY_URL)

    def test_a_launch_exception_is_bounded_and_never_leaks_raw_text(self):
        _rpc, browser, _stderr, result = self.run_open(
            {"action": "open"}, browser_error=RuntimeError("secret launch failure")
        )
        self.assertEqual(browser.call_count, 1)
        self.assertIs(result["browserOpened"], False)
        self.assertEqual(result["url"], ENTRY_URL)
        self.assertNotIn("secret launch failure", json.dumps(result))

    def test_the_default_browser_opener_is_python_webbrowser_with_new_2(self):
        rpc = FakeRpc()
        with mock.patch.object(console_cli.webbrowser, "open", return_value=True) as browser:
            result = console_cli.run({"action": "open"}, call_service=rpc)
        self.assertEqual(browser.call_args_list, [mock.call(ENTRY_URL, new=2)])
        self.assertTrue(result["browserOpened"])
        self.assertEqual(rpc.calls, [("console", {"action": "open"})])

    def test_malformed_launch_urls_are_invalid_responses_that_launch_nothing(self):
        cases = {
            "missing": None,
            "not a string": 8123,
            "blank": "",
            "https scheme": ENTRY_URL.replace("http://", "https://"),
            "uppercase scheme": ENTRY_URL.replace("http://", "HTTP://"),
            "localhost host": ENTRY_URL.replace("127.0.0.1", "localhost"),
            "all interfaces": ENTRY_URL.replace("127.0.0.1", "0.0.0.0"),
            "ipv6 loopback": ENTRY_URL.replace("127.0.0.1", "[::1]"),
            "userinfo": ENTRY_URL.replace("http://", "http://user:pass@"),
            "no port": ENTRY_URL.replace(":8123", ""),
            "port zero": ENTRY_URL.replace(":8123", ":0"),
            "port above range": ENTRY_URL.replace(":8123", ":65536"),
            "port not numeric": ENTRY_URL.replace(":8123", ":8o8"),
            "query": ENTRY_URL + "?ticket=1",
            "fragment": ENTRY_URL + "#frag",
            "wrong path": f"http://127.0.0.1:8123/console/{CONSOLE_ID}/{REPLACEMENT_ID}/",
            "ticket too short": ENTRY_URL[:-1],
            "ticket too long": ENTRY_URL + "x",
            "ticket plus": ENTRY_URL[:-1] + "+",
            "ticket slash": ENTRY_URL[:-1] + "/",
            "ticket padding": ENTRY_URL[:-1] + "=",
            "trailing slash": ENTRY_URL + "/",
        }
        for name, url in cases.items():
            for choice in ({"action": "open"}, {"action": "open", "browser": False}):
                with self.subTest(case=name, browser=choice.get("browser", True)):
                    browser = mock.Mock(side_effect=AssertionError("no browser may be launched"))
                    with self.assertRaises(BoardError) as refused:
                        console_cli.run(choice, call_service=FakeRpc(open_reply(url=url)), browser_open=browser)
                    self.assertEqual(refused.exception.code, "INVALID_RESPONSE")
                    self.assertEqual(browser.call_args_list, [])

    def test_malformed_open_identity_is_an_invalid_response_that_launches_nothing(self):
        cases = {
            "missing console id": without(OPEN_REPLY, "consoleId"),
            "null console id": open_reply(consoleId=None),
            "short console id": open_reply(consoleId=CONSOLE_ID[:-1]),
            "long console id": open_reply(consoleId=CONSOLE_ID + "0"),
            "uppercase console id": open_reply(consoleId=CONSOLE_ID.upper()),
            "non hex console id": open_reply(consoleId="0123456789abcdef0123456g"),
            "dashed console id": open_reply(consoleId="01234567-89ab-cdef-0123-456789abcdef"),
            "integer console id": open_reply(consoleId=3),
            "missing running": without(OPEN_REPLY, "running"),
            "null running": open_reply(running=None),
            "false running": open_reply(running=False),
            "integer running": open_reply(running=1),
            "string running": open_reply(running="true"),
        }
        for name, reply in cases.items():
            for choice in ({"action": "open"}, {"action": "open", "browser": False}):
                with self.subTest(case=name, browser=choice.get("browser", True)):
                    browser = mock.Mock(side_effect=AssertionError("no browser may be launched"))
                    with self.assertRaises(BoardError) as refused:
                        console_cli.run(choice, call_service=FakeRpc(reply), browser_open=browser)
                    self.assertEqual(refused.exception.code, "INVALID_RESPONSE")
                    self.assertEqual(browser.call_args_list, [])

    def test_end_to_end_cli_open_browser_false_prints_one_json_object(self):
        rpc = FakeRpc()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(transport, "call_service", rpc), contextlib.redirect_stdout(
            stdout
        ), contextlib.redirect_stderr(stderr):
            code = cli.main(["console", '{"action":"open","browser":false}'])
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {**OPEN_REPLY, "browserOpened": False, 'contractVersion': CONTRACT_VERSION},
        )
        self.assertEqual(rpc.calls, [("console", {"action": "open"})])
        self.assertEqual(stderr.getvalue(), "")

    def test_a_malformed_reply_is_refused_before_the_wait_can_start(self):
        rpc = FakeRpc(open_reply(consoleId=None))
        browser = mock.Mock(return_value=True)
        sleep = FakeSleep()
        factory = mock.Mock(side_effect=AssertionError("no client may be built"))
        with self.assertRaises(BoardError) as refused:
            console_cli.run(
                {"action": "open", "wait": True},
                call_service=rpc,
                browser_open=browser,
                sleep=sleep,
                client_factory=factory,
            )
        self.assertEqual(refused.exception.code, "INVALID_RESPONSE")
        self.assertEqual(browser.call_args_list, [])
        self.assertEqual(factory.call_args_list, [])
        self.assertEqual(sleep.intervals, [])


class ObserveAndCloseTests(ConsoleCliTestCase):
    """Status and close attach read-only: neither may cold-start a service."""

    def test_status_uses_a_read_only_client_and_passes_the_lifecycle_reply_through(self):
        client = FakeClient(statuses=[RUNNING])
        factory = mock.Mock(return_value=client)
        cold = mock.Mock(side_effect=AssertionError("status must not use the cold-starting call surface"))
        browser = mock.Mock(side_effect=AssertionError("status never launches a browser"))
        with mock.patch.object(transport, "call_service", cold), mock.patch.object(
            console_cli.webbrowser, "open", browser
        ):
            result = console_cli.run({"action": "status"}, client_factory=factory)
        self.assertEqual(cold.call_args_list, [])
        self.assertEqual(browser.call_args_list, [])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(client.calls, [("console", {"action": "status"})])
        self.assertEqual(result, RUNNING)

    def test_close_uses_a_read_only_client_and_forwards_the_fenced_replacement_answer(self):
        client = FakeClient(close_reply={"closed": False, "reason": "replaced"})
        factory = mock.Mock(return_value=client)
        cold = mock.Mock(side_effect=AssertionError("close must not use the cold-starting call surface"))
        with mock.patch.object(transport, "call_service", cold):
            result = console_cli.run(
                {"action": "close", "expectedConsoleId": REPLACEMENT_ID}, client_factory=factory
            )
        self.assertEqual(cold.call_args_list, [])
        self.assertEqual(client.calls, [("console", {"action": "close", "expectedConsoleId": REPLACEMENT_ID})])
        self.assertEqual(result, {"closed": False, "reason": "replaced"})

    def test_a_real_missing_service_is_reported_as_unavailable_and_starts_nothing(self):
        for params in ({"action": "status"}, {"action": "close", "expectedConsoleId": CONSOLE_ID}):
            with self.subTest(params=params):
                with tempfile.TemporaryDirectory() as directory:
                    cold = mock.Mock(side_effect=AssertionError("a missing service must never be cold-started"))
                    with mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": directory}), mock.patch.object(
                        transport, "call_service", cold
                    ):
                        code, result, _stderr = self.run_cli("console", json.dumps(params))
                    self.assertEqual(cold.call_args_list, [])
                    self.assertEqual(code, 1)
                    self.assertEqual(result["error"]["code"], "SERVICE_UNAVAILABLE")
                    self.assertNotIn("running", result)
                    self.assertNotIn("closed", result)
                    # Read-only attachment never creates a state directory, lock, log or daemon.
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_a_transport_failure_is_never_reported_as_a_stopped_console(self):
        client = FakeClient(
            error=BoardError("SERVICE_UNAVAILABLE", "The board service did not answer this operation")
        )
        with mock.patch("hey_my_buddy.protocol.client.BoardClient", return_value=client) as built:
            code, result, _stderr = self.run_cli("console", '{"action":"status"}')
        self.assertEqual(built.call_args_list, [mock.call(autostart=False)])
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertNotIn("running", result)
        self.assertEqual(client.calls, [("console", {"action": "status"})])


class WaitTests(ConsoleCliTestCase):
    def run_wait(
        self,
        statuses,
        *,
        close_reply=None,
        close_error=None,
        error=None,
        browser_result=False,
        interrupt_after=None,
    ):
        rpc = FakeRpc()
        client = FakeClient(statuses=statuses, close_reply=close_reply, close_error=close_error, error=error)
        factory = mock.Mock(return_value=client)
        sleep = FakeSleep(interrupt_after=interrupt_after)
        stderr = io.StringIO()
        result = console_cli.run(
            {"action": "open", "wait": True},
            call_service=rpc,
            client_factory=factory,
            browser_open=mock.Mock(return_value=browser_result),
            sleep=sleep,
            stderr=stderr,
        )
        return rpc, client, factory, sleep, stderr, result

    def test_wait_ends_when_the_console_closes_and_keeps_the_original_identity(self):
        rpc, client, factory, sleep, _stderr, result = self.run_wait([RUNNING, RUNNING, CLOSED])
        self.assertEqual(rpc.calls, [("console", {"action": "open"})])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(
            client.calls,
            [("console", {"action": "status"}), ("console", {"action": "status"}), ("console", {"action": "status"})],
        )
        self.assertEqual(sleep.intervals, [console_cli.POLL_SECONDS, console_cli.POLL_SECONDS])
        self.assertEqual(result["wait"], {"status": "closed"})
        self.assertEqual(result["consoleId"], CONSOLE_ID)
        self.assertEqual(result["url"], ENTRY_URL)
        self.assertEqual(result["expiresAt"], OPEN_REPLY["expiresAt"])

    def test_a_replacement_instance_ends_the_wait_with_honest_identity(self):
        _rpc, client, _factory, _sleep, _stderr, result = self.run_wait(
            [RUNNING, {"running": True, "consoleId": REPLACEMENT_ID}]
        )
        self.assertEqual(client.calls, [("console", {"action": "status"}), ("console", {"action": "status"})])
        self.assertEqual(result["wait"], {"status": "replaced", "consoleId": REPLACEMENT_ID})
        # The response keeps the identity this CLI opened, not the replacement's.
        self.assertEqual(result["consoleId"], CONSOLE_ID)
        self.assertEqual(result["url"], ENTRY_URL)

    def test_ctrl_c_closes_only_this_console_with_expected_console_id(self):
        _rpc, client, _factory, sleep, _stderr, result = self.run_wait([RUNNING], interrupt_after=1)
        self.assertEqual(sleep.intervals, [console_cli.POLL_SECONDS])
        self.assertEqual(
            client.calls,
            [("console", {"action": "status"}), ("console", {"action": "close", "expectedConsoleId": CONSOLE_ID})],
        )
        for operation, params in client.calls:
            self.assertEqual(operation, "console")
            self.assertIn(params["action"], ("status", "close"))
        self.assertEqual(result["wait"], {"status": "interrupted", "close": {"closed": True}})
        self.assertEqual(result["consoleId"], CONSOLE_ID)
        self.assertEqual(result["url"], ENTRY_URL)

    def test_ctrl_c_reports_a_fenced_close_that_did_not_close_this_instance(self):
        _rpc, client, _factory, _sleep, _stderr, result = self.run_wait(
            [RUNNING], interrupt_after=1, close_reply={"closed": False, "reason": "replaced"}
        )
        self.assertEqual(
            client.calls,
            [("console", {"action": "status"}), ("console", {"action": "close", "expectedConsoleId": CONSOLE_ID})],
        )
        self.assertEqual(result["wait"], {"status": "interrupted", "close": {"closed": False, "reason": "replaced"}})
        self.assertNotIn("error", result["wait"]["close"])

    def test_malformed_ctrl_c_close_replies_never_imply_success(self):
        cases = {
            "empty object": {},
            "non boolean closed": {"closed": "yes"},
            "null closed": {"closed": None},
            "missing closed": {"reason": "replaced"},
            "false without a reason": {"closed": False},
            "false with a blank reason": {"closed": False, "reason": "   "},
            "false with a non-string reason": {"closed": False, "reason": 3},
        }
        for name, reply in cases.items():
            with self.subTest(case=name):
                _rpc, client, _factory, _sleep, _stderr, result = self.run_wait(
                    [RUNNING], interrupt_after=1, close_reply=reply
                )
                self.assertEqual(result["wait"]["status"], "interrupted")
                self.assertIs(result["wait"]["close"]["closed"], False)
                self.assertEqual(result["wait"]["close"]["error"]["code"], "INVALID_RESPONSE")
                self.assertEqual(
                    client.calls,
                    [
                        ("console", {"action": "status"}),
                        ("console", {"action": "close", "expectedConsoleId": CONSOLE_ID}),
                    ],
                )
                # The response still carries the identity this CLI opened.
                self.assertEqual(result["consoleId"], CONSOLE_ID)

    def test_ctrl_c_close_transport_failure_is_reported_as_unclosed(self):
        error = BoardError("SERVICE_UNAVAILABLE", "The board service did not answer this operation")
        _rpc, _client, _factory, _sleep, _stderr, result = self.run_wait(
            [RUNNING], interrupt_after=1, close_error=error
        )
        self.assertEqual(result["wait"]["status"], "interrupted")
        self.assertEqual(result["wait"]["close"]["closed"], False)
        self.assertEqual(result["wait"]["close"]["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertEqual(result["consoleId"], CONSOLE_ID)

    def test_unavailable_service_ends_the_wait_honestly(self):
        error = BoardError("SERVICE_UNAVAILABLE", "No board service is running in this state directory")
        _rpc, client, _factory, _sleep, _stderr, result = self.run_wait([], error=error)
        self.assertEqual(client.calls, [("console", {"action": "status"})])
        self.assertEqual(
            result["wait"],
            {"status": "unavailable", "error": {"code": "SERVICE_UNAVAILABLE", "message": str(error)}},
        )
        self.assertEqual(result["consoleId"], CONSOLE_ID)

    def test_an_unknown_transport_failure_is_never_reported_as_closed(self):
        error = BoardError("TRANSPORT_LOST", "The connection to the board service was lost")
        _rpc, _client, _factory, _sleep, _stderr, result = self.run_wait([], error=error)
        self.assertEqual(result["wait"], {"status": "unavailable", "error": {"code": "TRANSPORT_LOST", "message": str(error)}})
        self.assertNotEqual(result["wait"]["status"], "closed")

    def test_unavailable_daemon_uses_the_non_autostart_board_client(self):
        client = console_cli._non_autostart_client()
        self.assertIsInstance(client, BoardClient)
        self.assertIs(client.autostart, False)

    def test_a_real_missing_daemon_ends_the_wait_without_starting_one(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": directory}):
                result = console_cli.run(
                    {"action": "open", "browser": False, "wait": True},
                    call_service=FakeRpc(),
                    sleep=FakeSleep(),
                    stderr=io.StringIO(),
                )
            self.assertEqual(result["wait"]["status"], "unavailable")
            self.assertEqual(result["wait"]["error"]["code"], "SERVICE_UNAVAILABLE")
            self.assertEqual(result["consoleId"], CONSOLE_ID)
            # read-only attachment never creates a state directory, lock, log or daemon.
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_malformed_status_responses_end_the_wait_honestly(self):
        for name, status in (
            ("not an object", "running"),
            ("missing running", {"consoleId": CONSOLE_ID}),
            ("running is not boolean", {"running": "true", "consoleId": CONSOLE_ID}),
            ("missing console id", {"running": True}),
            ("null console id", {"running": True, "consoleId": None}),
            ("uppercase console id", {"running": True, "consoleId": CONSOLE_ID.upper()}),
            ("short console id", {"running": True, "consoleId": CONSOLE_ID[:-1]}),
        ):
            with self.subTest(case=name):
                _rpc, _client, _factory, _sleep, _stderr, result = self.run_wait([status])
                self.assertEqual(result["wait"]["status"], "unavailable")
                self.assertEqual(result["wait"]["error"]["code"], "INVALID_RESPONSE")

    def test_the_fallback_entry_url_goes_to_stderr_while_the_wait_blocks_stdout(self):
        _rpc, _client, _factory, _sleep, stderr, result = self.run_wait([CLOSED], browser_result=False)
        self.assertEqual(result["wait"], {"status": "closed"})
        self.assertEqual(stderr.getvalue(), f"{console_cli.FALLBACK_NOTICE}{ENTRY_URL}\n")
        # The same one-time URL is in the single result object, not duplicated there.
        self.assertEqual(result["url"], ENTRY_URL)
        self.assertIs(result["browserOpened"], False)

    def test_no_stderr_notice_when_the_browser_was_opened(self):
        _rpc, _client, _factory, _sleep, stderr, result = self.run_wait([CLOSED], browser_result=True)
        self.assertTrue(result["browserOpened"])
        self.assertEqual(stderr.getvalue(), "")

    def test_end_to_end_cli_wait_starts_no_daemon_and_prints_one_json_object(self):
        rpc = FakeRpc()
        client = FakeClient(statuses=[CLOSED])
        constructed: dict = {}

        def build(*args, **kwargs):
            constructed["args"] = args
            constructed["kwargs"] = kwargs
            return client

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(transport, "call_service", rpc), mock.patch(
            "hey_my_buddy.protocol.client.BoardClient", side_effect=build
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["console", '{"wait":true,"browser":false}'])
        self.assertEqual(code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["wait"], {"status": "closed"})
        self.assertIs(result["browserOpened"], False)
        self.assertEqual(constructed["kwargs"], {"autostart": False})
        self.assertEqual(rpc.calls, [("console", {"action": "open"})])
        self.assertEqual(client.calls, [("console", {"action": "status"})])
        self.assertEqual(stderr.getvalue(), f"{console_cli.FALLBACK_NOTICE}{ENTRY_URL}\n")


if __name__ == "__main__":
    unittest.main()
