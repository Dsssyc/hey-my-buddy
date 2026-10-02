"""Host regressions for actual command selection and error stream retention."""
import io
import os
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from buddy.adapters.zcode import ZcodeAdapter, _NATIVE_CONTRACT_CACHE
from buddy.adapters.zcode_runner import _drain_structured
from buddy.adapters.zcode_protocol import NativeError
from test_zcode_read_only_protocol import GOOD_BUNDLE


class HostContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='private-zcode-host-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _NATIVE_CONTRACT_CACHE.clear()
        self.addCleanup(_NATIVE_CONTRACT_CACHE.clear)

    def test_ambient_dev_flag_cannot_endorse_another_controller_command(self):
        good, bad = self.root / 'good.cjs', self.root / 'bad.cjs'
        good.write_text(GOOD_BUNDLE)
        bad.write_text(GOOD_BUNDLE.replace('"plan",', ''))
        record = {'status': 'ready', 'command': ['node', str(bad)]}
        with patch.dict(os.environ, {'BUDDY_DEV_SOURCE': '1'}), patch('buddy.harness_runtime.selected', return_value=record):
            result = ZcodeAdapter()._native_contract_check({'BUDDY_DEV_SOURCE': '0', 'BUDDY_ZCODE_CLI': str(good)})
        self.assertIn('does not offer plan', result)
        with patch.dict(os.environ, {'BUDDY_DEV_SOURCE': '0'}), patch('buddy.harness_runtime.selected', return_value=record):
            self.assertIsNone(ZcodeAdapter()._native_contract_check({'BUDDY_DEV_SOURCE': '1', 'BUDDY_ZCODE_CLI': str(good)}))

    def test_growth_after_stat_is_still_a_bounded_read(self):
        path = self.root / 'growing.cjs'
        path.write_bytes(b'small')
        original = Path.open
        sizes = []

        class Wrapped:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def fileno(self): return self.stream.fileno()
            def read(self, size):
                sizes.append(size)
                return self.stream.read(size)

        def growing(target, mode='r', *args, **kwargs):
            if target == path and mode == 'rb':
                with original(target, 'wb') as stream: stream.write(b'x' * 50)
                return Wrapped(original(target, mode, *args, **kwargs))
            return original(target, mode, *args, **kwargs)

        with patch('buddy.adapters.zcode._MAX_PUBLIC_BUNDLE_BYTES', 10), patch.object(Path, 'open', growing), \
                patch('buddy.adapters.zcode_read_only.native_contract_problem') as checker:
            result = ZcodeAdapter()._native_contract_check({'BUDDY_DEV_SOURCE': '1', 'BUDDY_ZCODE_CLI': str(path)})
        self.assertIn('exceeds the 32 MiB', result)
        self.assertEqual(sizes, [11])
        checker.assert_not_called()
        self.assertFalse(_NATIVE_CONTRACT_CACHE)

    def test_changed_file_identity_is_not_published_or_cached(self):
        path = self.root / 'changing.cjs'
        path.write_text(GOOD_BUNDLE)
        original = Path.open

        class Changing:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def fileno(self): return self.stream.fileno()
            def read(self, size):
                content = self.stream.read(size)
                with original(path, 'ab') as writer: writer.write(b'//changed')
                return content

        def changed(target, mode='r', *args, **kwargs):
            stream = original(target, mode, *args, **kwargs)
            return Changing(stream) if target == path and mode == 'rb' else stream

        with patch.object(Path, 'open', changed), patch('buddy.adapters.zcode_read_only.native_contract_problem') as checker:
            result = ZcodeAdapter()._native_contract_check({'BUDDY_DEV_SOURCE': '1', 'BUDDY_ZCODE_CLI': str(path)})
        self.assertIn('changed during', result)
        checker.assert_not_called()
        self.assertFalse(_NATIVE_CONTRACT_CACHE)


class ErrorDrainTests(unittest.TestCase):
    def drain(self, status, messages, *, observer_error=False):
        observed = []
        def observe(message, ordinal):
            observed.append(message)
            if observer_error: raise NativeError('late-tool', 'late fact')
        stream = queue.Queue()
        for message in messages: stream.put(message)
        result = {'status': status, 'code': 'readonly-budget-exhausted' if status != 'ok' else None}
        complete = _drain_structured(SimpleNamespace(messages=stream, observe=observe), result,
                                     SimpleNamespace(violation=False), 'read-only')
        return complete, observed, result, stream

    def test_an_earlier_failure_does_not_hide_remaining_tool_frames(self):
        frames = [{'method': 'tool.updated', 'params': {'fixture': index}} for index in range(3)]
        complete, observed, result, stream = self.drain('error', [*frames, None], observer_error=True)
        self.assertFalse(complete)
        self.assertEqual(observed, frames)
        self.assertEqual(result['code'], 'readonly-budget-exhausted')
        self.assertTrue(stream.empty())

    def test_native_error_before_more_facts_keeps_the_first_failure_and_drains_eof(self):
        frame = {'method': 'tool.updated', 'params': {'fixture': 'read'}}
        complete, observed, result, stream = self.drain('ok', [NativeError('invalid-protocol', 'bad'), frame, None])
        self.assertFalse(complete)
        self.assertEqual(observed, [frame])
        self.assertEqual(result['code'], 'invalid-protocol')
        self.assertTrue(stream.empty())

    def test_clean_eof_is_complete_even_if_the_model_previously_failed(self):
        self.assertTrue(self.drain('error', [None])[0])


if __name__ == '__main__':
    unittest.main()
