"""The shared fragments of the four native run bodies, as behavioral contracts.

ADR-025 step five's opening cleanup moved the fragments the harness run
bodies had already spelled alike into one module. These tests pin only the
contracts that had no direct coverage before — the cancel flag's two sources
and bounded wait, the package-shape guard's drop-alone rule, the native
identity dropper and the owned-group halt's close/wait/terminate order. The
fragments the per-harness trees already cover through their re-exported names
(the deadline rule, the configuration spec, the timestamp) stay covered there,
and the four harness trees remain the integration coverage of how the shared
fragments are consumed. Synthetic fakes only: no harness, process or model is
started.
"""
import threading
import time
import unittest

from hey_my_buddy.buddy.harnesses.native_support import (
    CancelFlag,
    halt_owned_group,
    identity_or_none,
    shape_guard,
)
from hey_my_buddy.buddy.harnesses.run_contract import NativeIdentity, UsagePackage


class CancelFlagTests(unittest.TestCase):
    def test_either_source_sets_the_flag(self):
        self.assertFalse(CancelFlag(lambda: False).is_set())
        self.assertTrue(CancelFlag(lambda: True).is_set())
        flag = CancelFlag(lambda: False)
        flag.set()
        self.assertTrue(flag.is_set())

    def test_set_survives_a_false_callable(self):
        flag = CancelFlag(lambda: False)
        flag.set()
        for _ in range(3):
            self.assertTrue(flag.is_set())

    def test_wait_returns_at_the_deadline_when_never_set(self):
        flag = CancelFlag(lambda: False)
        started = time.monotonic()
        flag.wait(0.12)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.12)
        self.assertLess(elapsed, 0.12 + 0.5)

    def test_wait_returns_once_set(self):
        flag = CancelFlag(lambda: False)

        def setter():
            time.sleep(0.05)
            flag.set()

        thread = threading.Thread(target=setter)
        thread.start()
        started = time.monotonic()
        flag.wait(5)
        thread.join()
        self.assertLess(time.monotonic() - started, 2)


class ShapeGuardTests(unittest.TestCase):
    def setUp(self):
        self.usage = shape_guard(UsagePackage)

    def test_none_stays_none(self):
        self.assertIsNone(self.usage(None))

    def test_a_usable_canonical_value_passes(self):
        guarded = self.usage({"inputTokens": 3, "outputTokens": 1})
        self.assertIsNotNone(guarded)
        self.assertEqual(guarded.value["inputTokens"], 3)
        self.assertEqual(guarded.value["scope"], "attempt")

    def test_a_refused_projection_is_dropped_alone(self):
        # A session-cumulative record is not this attempt's usage: the package's
        # own projection refuses it, and the guard drops it to None.
        self.assertIsNone(self.usage({"scope": "session", "inputTokens": 3, "outputTokens": 1}))
        self.assertIsNone(self.usage("not-an-object"))


class IdentityOrNoneTests(unittest.TestCase):
    def test_valid_fields_build_the_identity(self):
        identity = identity_or_none({"session_id": "s-1", "turn_id": "t-1"})
        self.assertEqual(identity, NativeIdentity(session_id="s-1", turn_id="t-1"))

    def test_a_refused_identity_is_none(self):
        # The identity payload must carry at least one native identifier field.
        self.assertIsNone(identity_or_none({}))


class _FakeStdin:
    def __init__(self, calls: list):
        self._calls = calls

    def close(self):
        self._calls.append("stdin.close")


class _FakeProcess:
    def __init__(self, calls: list):
        self.stdin = _FakeStdin(calls)


class _FakeHandle:
    def __init__(self, calls: list, confirmed_after_wait: bool, confirmed_after_terminate: bool):
        self._calls = calls
        self._after_wait = confirmed_after_wait
        self._after_terminate = confirmed_after_terminate
        self.terminated = 0

    def wait(self, timeout):
        self._calls.append(f"handle.wait({timeout:.1f})")

    def shutdown_confirmed(self, settle_seconds):
        return self._after_wait if self.terminated == 0 else self._after_terminate

    def terminate(self, grace_seconds):
        self.terminated += 1
        self._calls.append(f"handle.terminate({grace_seconds:.1f})")


class HaltOwnedGroupTests(unittest.TestCase):
    def halt(self, handle: _FakeHandle, calls: list):
        return halt_owned_group(_FakeProcess(calls), handle, time.monotonic() + 30.0)

    def test_a_confirmed_group_stops_without_a_signal(self):
        calls: list = []
        handle = _FakeHandle(calls, confirmed_after_wait=True, confirmed_after_terminate=True)
        self.assertEqual(self.halt(handle, calls), (True, False))
        self.assertEqual(handle.terminated, 0)
        self.assertEqual(calls, ["stdin.close", "handle.wait(3.0)"])

    def test_an_unconfirmed_group_is_terminated_once_and_reported(self):
        calls: list = []
        handle = _FakeHandle(calls, confirmed_after_wait=False, confirmed_after_terminate=True)
        self.assertEqual(self.halt(handle, calls), (True, True))
        self.assertEqual(handle.terminated, 1)
        self.assertEqual(calls, ["stdin.close", "handle.wait(3.0)", "handle.terminate(1.0)"])

    def test_a_never_confirmed_group_stays_unconfirmed_and_signalled(self):
        calls: list = []
        handle = _FakeHandle(calls, confirmed_after_wait=False, confirmed_after_terminate=False)
        self.assertEqual(self.halt(handle, calls), (False, True))
        self.assertEqual(handle.terminated, 1)

    def test_the_input_closes_before_the_wait(self):
        calls: list = []
        handle = _FakeHandle(calls, confirmed_after_wait=True, confirmed_after_terminate=True)
        self.halt(handle, calls)
        self.assertLess(calls.index("stdin.close"), calls.index("handle.wait(3.0)"))


if __name__ == "__main__":
    unittest.main()
