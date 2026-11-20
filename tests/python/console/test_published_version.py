"""Published version reads use an isolated HTTP stub and the console access gate."""
import io
import json
import os
import socket
import unittest
from datetime import datetime, timedelta
from unittest import mock
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.response import addinfourl

from console.test_console import Browser
from console.test_console_version_info import _VersionConsoleTestCase
from hey_my_buddy.console import published_version


UNAVAILABLE = {"publishedVersion": None, "checkedAt": None}
PYPI_URL = "https://pypi.org/pypi/hey-my-buddy/json"


class PublishedVersionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.cache = published_version._Cache(clock=lambda: self.now)
        self.open_url = self.enterContext(mock.patch.object(published_version, "urlopen"))

    def response(self, raw, status=200):
        response = io.BytesIO(raw)
        response.status = status
        self.open_url.return_value = response

    def enable_mocked_fetches(self):
        self.enterContext(mock.patch.object(published_version, "_checks_disabled", return_value=False))

    def test_parses_version_and_caches_for_six_hours(self):
        self.enable_mocked_fetches()
        self.response(b'{"info":{"version":"0.72.0rc1"},"other":"ignored"}')
        found = self.cache.read()
        self.assertEqual(found["publishedVersion"], "0.72.0rc1")
        self.assertEqual(datetime.fromisoformat(found["checkedAt"]).utcoffset(), timedelta(0))
        self.assertEqual(self.open_url.call_count, 1)
        sent, = self.open_url.call_args.args
        self.assertEqual(self.open_url.call_args.kwargs, {"timeout": 3})
        self.assertEqual((sent.full_url, sent.get_method(), sent.get_header("Accept")),
                         (PYPI_URL, "GET", "application/json"))

        self.now += 21599
        self.assertEqual(self.cache.read(), found)
        self.assertEqual(self.open_url.call_count, 1)
        self.now += 1
        self.response(b'{"info":{"version":"0.72.0"}}')
        self.assertEqual(self.cache.read()["publishedVersion"], "0.72.0")
        self.assertEqual(self.open_url.call_count, 2)

    def test_timeout_and_offline_return_null_and_retry_after_cooldown(self):
        self.enable_mocked_fetches()
        for failure in (TimeoutError("timed out"), URLError(socket.gaierror("offline"))):
            with self.subTest(error=type(failure).__name__):
                cache = published_version._Cache(clock=lambda: self.now)
                self.open_url.reset_mock()
                self.open_url.side_effect = failure
                self.assertEqual(cache.read(), UNAVAILABLE)
                self.now += 299
                self.assertEqual(cache.read(), UNAVAILABLE)
                self.assertEqual(self.open_url.call_count, 1)

                self.now += 1
                self.open_url.side_effect = None
                self.response(b'{"info":{"version":"0.72.0"}}')
                self.assertEqual(cache.read()["publishedVersion"], "0.72.0")
                self.assertEqual(self.open_url.call_count, 2)

    def test_http_error_returns_null_and_closes_the_response(self):
        self.enable_mocked_fetches()
        body = io.BytesIO(b'{"info":{"version":"0.72.0"}}')
        self.open_url.side_effect = HTTPError(PYPI_URL, 503, "Unavailable", {}, body)
        self.assertEqual(self.cache.read(), UNAVAILABLE)
        self.assertTrue(body.closed)

    def test_rejects_non_success_oversized_and_invalid_responses(self):
        self.enable_mocked_fetches()
        cases = (
            (302, b'{"info":{"version":"0.72.0"}}'),
            (200, b'{' * (512 * 1024 + 1)),
            (200, b'{broken'),
            (200, b'{"info":{"version":""}}'),
            (200, b'{"info":{"version":72}}'),
            (200, b'{"info":{"version":" 0.72.0 "}}'),
        )
        for status, body in cases:
            with self.subTest(status=status, prefix=body[:45]):
                self.response(body, status)
                self.assertEqual(published_version._Cache().read(), UNAVAILABLE)

    def test_private_check_runner_does_not_query_pypi(self):
        self.assertIn("HMB_CHECKS_TMPDIR", os.environ)
        self.assertEqual(self.cache.read(), UNAVAILABLE)
        self.open_url.assert_not_called()

    def test_explicit_disable_does_not_query_pypi(self):
        with mock.patch.dict(os.environ, {"HMB_DISABLE_PYPI_CHECK": "1"}, clear=True):
            self.assertEqual(self.cache.read(), UNAVAILABLE)
        self.open_url.assert_not_called()


class PublishedVersionProxyTests(unittest.TestCase):
    def test_https_proxy_and_no_proxy_are_applied_by_the_default_opener(self):
        for no_proxy, host in (("", "proxy.invalid:3128"), ("pypi.org", "pypi.org")):
            with self.subTest(no_proxy=no_proxy), \
                 mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:3128", "NO_PROXY": no_proxy}, clear=True), \
                 mock.patch.object(request, "_opener", None), \
                 mock.patch.object(request.HTTPSHandler, "do_open") as transport, \
                 mock.patch.object(socket, "create_connection", side_effect=AssertionError("Unexpected network call")):
                response = addinfourl(io.BytesIO(b'{"info":{"version":"0.72.0"}}'), {}, PYPI_URL, code=200)
                response.msg = "OK"
                transport.return_value = response
                self.assertEqual(published_version._fetch_version(), "0.72.0")
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(transport.call_args.args[1].host, host)


class PublishedVersionHTTPTests(_VersionConsoleTestCase):
    def test_route_validates_session_and_origin_before_reading(self):
        stated, browser = self.open_console(self.board())
        stranger = Browser(stated["url"], redeem=False)
        route = "/api/published-version"
        with mock.patch.object(published_version, "published_version", wraps=published_version.published_version) as read:
            self.assertEqual(stranger.call("GET", route)[0], 401)
            self.assertEqual(browser.get(route, headers={"Origin": "https://foreign.invalid"})[0], 403)
            read.assert_not_called()

            status, headers, body = browser.get(route)
            self.assertEqual((status, json.loads(body)), (200, UNAVAILABLE))
            self.assertEqual(headers["cache-control"], "no-store")
            read.assert_called_once_with()
