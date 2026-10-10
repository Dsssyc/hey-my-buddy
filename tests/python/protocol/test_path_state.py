"""PATH-01..05: public canonical roots, owner setup and private IPC refusal.

Real cases reuse the catalog daemon and the native ping peer. Each subprocess
owns one state/runtime domain; retained receipts include every wait and cleanup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from support import _child_environment, write_catalog_fixture, shutdown_private_rpc
from protocol.test_public_state import DAEMON_COMMAND, prepare_daemon_spawn, cleanup_owned_case
from protocol import test_transport_attach as attach_fixture
from protocol.test_transport_attach import MutationRecorder, snapshot
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import rpc_config, transport


def cli_health(selected, environment):
    command = [sys.executable, '-m', 'hey_my_buddy.cli.main', 'health']
    child = subprocess.Popen(command, env=dict(environment, BUDDY_STATE_DIR=str(selected)),
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = child.communicate(timeout=30)
    return child, {'pid': child.pid, 'command': command, 'waitExit': child.returncode,
                   'stdout': stdout, 'stderr': stderr}


def run_path_case(name, work):
    state = work / 'state'
    state.mkdir(mode=0o700)
    write_catalog_fixture(state)
    environment = dict(os.environ, BUDDY_STATE_DIR=str(state),
                       BUDDY_MODEL_CATALOG_FILE=str(state / 'model-catalog.json'),
                       BUDDY_MODEL_FACTS_FILE=str(state / 'model-facts-fixture.json'),
                       BUDDY_RUNTIME_ROOT=str(work / 'runtime'))
    evidence = {'case': name, 'state': str(state), 'processes': [], 'requests': []}
    handles = []
    modes = {state: 0o700}
    log = (work / 'daemon.log').open('ab')
    spawn = subprocess.Popen

    def collect(command, **kwargs):
        if '-m' in command and command[-1] == 'hey_my_buddy.blackboard.service.daemon':
            command = prepare_daemon_spawn(command, kwargs, state)
        child = spawn(command, **kwargs)
        handles.append(child)
        evidence['processes'].append({'pid': child.pid, 'command': command, 'waitExit': None})
        return child

    def ready(child):
        deadline = time.monotonic() + 20
        while True:
            endpoint = transport._read_endpoint(state)
            if endpoint:
                reply = transport._request(endpoint, 'health', {}, state_dir=state)
                assert endpoint['pid'] == child.pid
                return endpoint, reply
            if child.poll() is not None or time.monotonic() > deadline:
                raise AssertionError('owned daemon not ready: ' + (work / 'daemon.log').read_text())
            time.sleep(.05)

    try:
        with patch.dict(os.environ, environment, clear=True):
            if name == 'cold-755':
                state.chmod(0o755)
                preflight = transport._cold_start_preflight

                def observe(selected):
                    preflight(selected)
                    evidence['preflightMode'] = stat.S_IMODE(state.stat().st_mode)
                    assert evidence['preflightMode'] == 0o700, 'preflight did not repair owner mode'
                with patch.object(transport, '_cold_start_preflight', side_effect=observe), \
                        patch.object(transport.subprocess, 'Popen', side_effect=collect):
                    reply = transport.call_service('health', state_dir=state)
                assert handles
                endpoint = transport._read_endpoint(state)
                assert endpoint['pid'] == handles[0].pid
            else:
                if name == 'daemon-755':
                    state.chmod(0o755)
                child = collect(DAEMON_COMMAND, env=environment, stdout=log, stderr=log)
                endpoint, reply = ready(child)
            assert stat.S_IMODE(state.stat().st_mode) == 0o700, 'Daemon.run did not repair owner mode'
            evidence['serviceId'] = reply['serviceId']
            evidence['stateDir'] = reply['stateDir']
            assert reply['stateDir'] == str(state)
            if name == 'linked-cli':
                ancestor = work / 'linked-parent'
                ancestor.symlink_to(work, target_is_directory=True)
                alias = work / 'state-alias'
                alias.symlink_to(state, target_is_directory=True)
                for selected in (ancestor / 'state', alias):
                    assert transport.get_state_dir(selected) == state
                    cli, receipt = cli_health(selected, environment)
                    handles.append(cli)
                    evidence['processes'].append(receipt)
                    assert receipt['waitExit'] == 0, receipt
                    cli_reply = json.loads(receipt['stdout'])
                    assert cli_reply['serviceId'] == reply['serviceId']
                    assert cli_reply['stateDir'] == str(state), cli_reply
                    request = transport._request
                    with patch.object(transport, '_request', wraps=request) as observed:
                        assert transport.call_service('health', state_dir=selected)['serviceId'] == reply['serviceId']
                    assert all(call.kwargs['state_dir'] == state for call in observed.call_args_list)
                    evidence['requests'].extend({'state': str(call.kwargs['state_dir']),
                                                  'operation': call.args[1]} for call in observed.call_args_list)
            evidence['nativeRoot'] = str(transport.cc.local_endpoint_context().root)
            assert evidence['nativeRoot'] == str(state / 'ipc')
            assert not (work / 'native-invoked').exists()
    except BaseException as error:
        evidence['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        failures = cleanup_owned_case(state, modes, handles, evidence)
        log.close()
        evidence['cleanupFailures'] = failures
        (work / 'evidence.json').write_text(json.dumps(evidence, indent=2))
        if failures and 'failure' not in evidence:
            raise AssertionError('owned cleanup failed: ' + json.dumps(failures))


class PathStateTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='path-state-')).resolve()
        self.state = self.work / 'state'
        self.addCleanup(shutdown_private_rpc)

    def test_path01_public_canonical_selection_and_precedence(self):
        self.state.mkdir(mode=0o700)
        alias = self.work / 'alias'
        alias.symlink_to(self.state, target_is_directory=True)
        with patch.dict(os.environ, {'BUDDY_STATE_DIR': str(alias)}):
            self.assertEqual(transport.get_state_dir(), self.state)
            self.assertEqual(transport.get_state_dir(self.work / 'explicit'), self.work / 'explicit')
        with patch.dict(os.environ, {}, clear=True), patch.object(transport.home, 'default_state_dir', return_value=alias):
            self.assertEqual(transport.get_state_dir(), self.state)

    def test_path02_private_ipc_and_inner_links_and_dotdot_have_paths(self):
        self.state.mkdir(mode=0o700)
        target = self.work / 'outside'
        target.mkdir(mode=0o700)
        (self.state / 'ipc').symlink_to(target, target_is_directory=True)
        with self.assertRaises(BoardError) as caught:
            rpc_config.configure_client(self.state, create=False)
        self.assertEqual(caught.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(caught.exception.message, 'IPC path contains a linked or unsafe component')
        self.assertEqual(caught.exception.details.get('path'), str(self.state / 'ipc'))
        with self.assertRaises(BoardError) as caught:
            rpc_config._validate_path(self.state / 'ipc' / 'inner', boundary=self.state)
        self.assertEqual(caught.exception.details.get('path'), str(self.state / 'ipc'))
        nested_state = self.work / 'nested-state'
        (nested_state / 'ipc').mkdir(mode=0o700, parents=True)
        (target / 'inner').mkdir(mode=0o700)
        inner_link = nested_state / 'ipc' / 'linked'
        inner_link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BoardError) as caught:
            rpc_config._validate_path(inner_link / 'inner', boundary=nested_state)
        self.assertEqual(caught.exception.message, 'IPC path contains a linked or unsafe component')
        self.assertEqual(caught.exception.details.get('path'), str(inner_link))
        with self.assertRaises(BoardError) as caught:
            rpc_config.configure_client(self.state / 'missing' / '..')
        self.assertEqual(caught.exception.details.get('path'), str(self.state / 'missing' / '..'))

    def test_path03_preflight_repairs_755_without_test_environment(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        before = set(self.state.iterdir())
        # Supplemental local preflight check: sockets alone are substitutes.
        # Real cold startup is separately exercised below.
        sockets = Mock()
        sockets.return_value.accept.return_value = (Mock(), None)
        with patch.dict(os.environ, {}, clear=True), patch.object(transport.socket, 'socket', sockets), \
                patch.object(transport.tempfile, 'mkdtemp', wraps=tempfile.mkdtemp) as temporary:
            try:
                transport._cold_start_preflight(self.state)
            except BoardError as error:
                self.fail('owner preflight refused its 0755 state: ' + json.dumps(error.payload()))
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(set(self.state.iterdir()), before | {self.state / 'ipc'})
        temporary.assert_called_once_with(prefix='buddy-ipc-')

    def test_path03_daemon_run_repairs_before_rpc_configuration(self):
        from hey_my_buddy.blackboard.service.daemon import Daemon
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        daemon = Daemon(self.state)
        stop = RuntimeError('stop before local registration')
        with patch.object(rpc_config, 'configure_server', side_effect=stop):
            with self.assertRaises(RuntimeError) as caught:
                daemon.run()
        self.assertIs(caught.exception, stop)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)

    def test_path03_both_owner_entries_create_before_rpc_configuration(self):
        from hey_my_buddy.blackboard.service.daemon import Daemon
        for owner in ('preflight', 'daemon'):
            with self.subTest(owner=owner):
                selected = self.work / owner
                stop = RuntimeError('stop before local registration')
                operation = (lambda: transport._cold_start_preflight(selected)) if owner == 'preflight' else Daemon(selected).run
                boundary = 'configure_local_endpoint' if owner == 'preflight' else 'configure_server'
                with patch.object(rpc_config, boundary, side_effect=stop):
                    with self.assertRaises(Exception) as caught:
                        operation()
                self.assertIs(caught.exception, stop)
                self.assertTrue(selected.is_dir())
                self.assertEqual(stat.S_IMODE(selected.stat().st_mode), 0o700)

    def test_path04_preflight_preserves_structural_error_object(self):
        self.state.mkdir(mode=0o700)
        error = BoardError('PRIVATE_PATH_UNSAFE', 'specific structural reason', path=str(self.state / 'ipc'))
        with patch.object(rpc_config, 'configure_local_endpoint', side_effect=error):
            with self.assertRaises(BoardError) as caught:
                transport._cold_start_preflight(self.state)
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.payload(), error.payload())

    def test_path04_os_write_and_ipc_denial_only_map_access_denied(self):
        self.state.mkdir(mode=0o700)
        for boundary in ('write', 'ipc'):
            with self.subTest(boundary=boundary):
                sockets = Mock()
                sockets.return_value.bind.side_effect = PermissionError('injected local IPC denial')
                patcher = (patch.object(transport.os, 'open', side_effect=PermissionError('injected state write denial'))
                           if boundary == 'write' else patch.object(transport.socket, 'socket', sockets))
                with patcher, self.assertRaises(transport.ServiceError) as caught:
                    transport._cold_start_preflight(self.state)
                self.assertEqual(caught.exception.code, 'LAUNCH_ACCESS_DENIED')
                self.assertIsInstance(caught.exception.__cause__, PermissionError)

    def test_path04_actual_cli_reports_structure_code_message_path(self):
        self.state.mkdir(mode=0o700)
        target = self.work / 'target'
        target.mkdir(mode=0o700)
        (self.state / 'ipc').symlink_to(target, target_is_directory=True)
        with self.assertRaises(BoardError) as caught:
            transport._cold_start_preflight(self.state)
        self.assertEqual(caught.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(caught.exception.message, 'IPC path contains a linked or unsafe component')
        self.assertEqual(caught.exception.details.get('path'), str(self.state / 'ipc'))
        child, receipt = cli_health(self.state, _child_environment(self.work))
        (self.work / 'cli-process.json').write_text(json.dumps(receipt, indent=2))
        self.assertEqual(child.returncode, 1, receipt)
        self.assertEqual(json.loads(receipt['stdout'])['error'], caught.exception.payload())


class RealPathStateTests(unittest.TestCase):
    def case(self, name):
        work = Path(tempfile.mkdtemp(prefix='path-real-')).resolve()
        environment = _child_environment(work)
        sentinel = work / 'native-forbidden'
        sentinel.write_text("#!/bin/sh\nprintf unexpected >> '" + str(work / 'native-invoked') + "'\nexit 99\n")
        sentinel.chmod(0o700)
        environment.update({'BUDDY_' + name + '_CLI': str(sentinel) for name in ('CLAUDE', 'CODEX', 'DSH', 'ZCODE')})
        environment.update(BUDDY_MAX_CONCURRENT='1', BUDDY_CHECKS_TMPDIR=os.environ['BUDDY_CHECKS_TMPDIR'])
        command = [sys.executable, str(Path(__file__).resolve()), '--case', name, str(work)]
        child = subprocess.Popen(command, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = child.communicate(timeout=100)
        (work / 'case-process.json').write_text(json.dumps({'pid': child.pid, 'command': command,
                                                          'waitExit': child.returncode}, indent=2))
        (work / 'case.log').write_text(stdout + stderr)
        self.assertEqual(child.returncode, 0, f'{name}: {work}\n{stdout}\n{stderr}')

    def test_path01_linked_cli_health_and_canonical_native_root(self):
        self.case('linked-cli')

    def test_path03_real_cold_start_repairs_755(self):
        self.case('cold-755')

    def test_path03_real_direct_daemon_repairs_755(self):
        self.case('daemon-755')


class ReadonlyPathStateTests(unittest.TestCase):
    def test_path03_500_state_real_ping_peer_has_no_mutations(self):
        # Reuse the existing tiny native peer, without SQLite permission effects.
        fixture = attach_fixture.RpcReadOnlySetupTests('test_explicit_state_reaches_rpc_setup')
        fixture.setUp()
        evidence = {'layer': 'native ping transport, no SQLite', 'state': str(fixture.directory)}
        try:
            from protocol import test_rpc_config as probe_fixture
            catalog = write_catalog_fixture(fixture.directory)
            sentinel = fixture.directory / 'native-forbidden'
            invoked = fixture.directory / 'native-invoked'
            sentinel.write_text("#!/bin/sh\nprintf unexpected >> '" + str(invoked) + "'\nexit 99\n")
            sentinel.chmod(0o700)
            original_environment = probe_fixture.private_environment

            def offline_environment(root, **overrides):
                environment = original_environment(root, **overrides)
                environment['BUDDY_MODEL_CATALOG_FILE'] = str(catalog)
                environment.update({'BUDDY_' + harness + '_CLI': str(sentinel)
                                    for harness in ('CLAUDE', 'CODEX', 'DSH', 'ZCODE')})
                return environment

            with patch.object(probe_fixture, 'private_environment', side_effect=offline_environment):
                endpoint, received = fixture.native_ping_peer()
            # Establish the client role before observing attach-only I/O.
            self.assertEqual(transport.call_service('ping', state_dir=fixture.directory), {'token': endpoint['token']})
            received_before = len(received.read_text().splitlines())
            fixture.directory.chmod(0o500)
            # The peer appends to its trace file through its existing file
            # permissions. That write is evidence of real RPC, separate from
            # client I/O; every other entry must stay unchanged.
            before = {key: value for key, value in snapshot(fixture.directory).items() if key != received.name}
            with MutationRecorder() as recorder:
                self.assertEqual(transport.call_service('ping', state_dir=fixture.directory), {'token': endpoint['token']})
            recorder.assert_no_mutation()
            self.assertEqual({key: value for key, value in snapshot(fixture.directory).items() if key != received.name}, before)
            self.assertEqual(stat.S_IMODE(fixture.directory.stat().st_mode), 0o500)
            self.assertEqual(len(received.read_text().splitlines()), received_before + 2)
            self.assertFalse(invoked.exists())
            evidence.update(pid=endpoint['pid'], nativeRoot=str(transport.cc.local_endpoint_context().root),
                            mode=0o500, snapshotExceptPeerTracePreserved=True)
        finally:
            fixture.directory.chmod(0o700)
            # unittest executes all registered peer stop, wait, shutdown and
            # TemporaryDirectory cleanup callbacks even if an earlier one fails.
            clean = fixture.doCleanups()
            evidence['cleanupCompleted'] = clean
            evidence['peerReceipt'] = getattr(fixture, 'peer_receipt', {})
            Path(os.environ['BUDDY_CHECKS_TMPDIR'], 'readonly-path-evidence.json').write_text(json.dumps(evidence, indent=2))
            self.assertTrue(clean, 'native peer cleanup failed')
            self.assertEqual(evidence['peerReceipt'].get('waitExit'), 0)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--case':
        run_path_case(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
