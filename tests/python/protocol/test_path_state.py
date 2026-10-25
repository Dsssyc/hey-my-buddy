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
from protocol.test_public_state import DAEMON_COMMAND, prepare_daemon_spawn, cleanup_owned_case, endpoint_process, native_forbidden_executable
from protocol import test_transport_attach as attach_fixture
from protocol.test_transport_attach import MutationRecorder, snapshot
from private_acl import assert_inherited_private_acl
from hey_my_buddy.errors import BoardError
from hey_my_buddy import state_root, private_dirs
from hey_my_buddy.protocol import rpc_config, transport


def cli_health(selected, environment):
    command = [sys.executable, '-m', 'hey_my_buddy.cli.main', 'health']
    child = subprocess.Popen(command, env=dict(environment, HMB_STATE_DIR=str(selected)),
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    stdout, stderr = child.communicate(timeout=30)
    return child, {'pid': child.pid, 'command': command, 'waitExit': child.returncode,
                   'stdout': stdout, 'stderr': stderr}


def run_path_case(name, work):
    state = work / 'state'
    state.mkdir(mode=private_dirs.directory_mode())
    write_catalog_fixture(state)
    environment = dict(os.environ, HMB_STATE_DIR=str(state),
                       HMB_MODEL_CATALOG_FILE=str(state / 'model-catalog.json'),
                       HMB_RUNTIME_ROOT=str(work / 'runtime'))
    evidence = {'case': name, 'state': str(state), 'processes': [], 'requests': []}
    handles = []
    modes = {state: 0o700} if os.name != 'nt' else {}
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
                evidence['endpointProcess'] = endpoint_process(child, endpoint['pid'])
                return endpoint, reply
            if child.poll() is not None or time.monotonic() > deadline:
                raise AssertionError('owned daemon not ready: ' + (work / 'daemon.log').read_text(encoding="utf-8"))
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
                evidence['endpointProcess'] = endpoint_process(handles[0], endpoint['pid'])
            else:
                if name == 'daemon-755':
                    state.chmod(0o755)
                child = collect(DAEMON_COMMAND, env=environment, stdout=log, stderr=log)
                endpoint, reply = ready(child)
            if os.name == 'nt':
                assert_inherited_private_acl(unittest.TestCase(), state)
                assert not (state / 'ipc').exists(), 'Windows must use its Named Pipe domain'
            else:
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
                    assert state_root.get_state_dir(selected) == state
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
            native_root = transport.cc.local_endpoint_context().root
            evidence['nativeRoot'] = None if native_root is None else str(native_root)
            assert evidence['nativeRoot'] == (None if os.name == 'nt' else str(state / 'ipc'))
            assert not (work / 'native-invoked').exists()
    except BaseException as error:
        evidence['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        failures = cleanup_owned_case(state, modes, handles, evidence)
        log.close()
        evidence['cleanupFailures'] = failures
        (work / 'evidence.json').write_text(json.dumps(evidence, indent=2), encoding="utf-8", newline="\n")
        if failures and 'failure' not in evidence:
            raise AssertionError('owned cleanup failed: ' + json.dumps(failures))


class PathStateTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='path-state-')).resolve()
        self.state = self.work / 'state'
        self.addCleanup(shutdown_private_rpc)

    @unittest.skipIf(os.name == 'nt', 'Windows preserves the selected path spelling')
    def test_path01_public_canonical_selection_and_precedence(self):
        self.state.mkdir(mode=private_dirs.directory_mode())
        alias = self.work / 'alias'
        alias.symlink_to(self.state, target_is_directory=True)
        with patch.dict(os.environ, {'HMB_STATE_DIR': str(alias)}):
            self.assertEqual(state_root.get_state_dir(), self.state)
            self.assertEqual(state_root.get_state_dir(self.work / 'explicit'), self.work / 'explicit')
        with patch.dict(os.environ, {}, clear=True), patch.object(state_root.home, 'default_state_dir', return_value=alias):
            self.assertEqual(state_root.get_state_dir(), self.state)

    @unittest.skipIf(os.name == 'nt', 'POSIX IPC directory path')
    def test_path02_private_ipc_and_inner_links_and_dotdot_have_paths(self):
        self.state.mkdir(mode=private_dirs.directory_mode())
        target = self.work / 'outside'
        target.mkdir(mode=private_dirs.directory_mode())
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
        (nested_state / 'ipc').mkdir(mode=private_dirs.directory_mode(), parents=True)
        (target / 'inner').mkdir(mode=private_dirs.directory_mode())
        inner_link = nested_state / 'ipc' / 'linked'
        inner_link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BoardError) as caught:
            rpc_config._validate_path(inner_link / 'inner', boundary=nested_state)
        self.assertEqual(caught.exception.message, 'IPC path contains a linked or unsafe component')
        self.assertEqual(caught.exception.details.get('path'), str(inner_link))
        with self.assertRaises(BoardError) as caught:
            rpc_config.configure_client(self.state / 'missing' / '..')
        self.assertEqual(caught.exception.details.get('path'), str(self.state / 'missing' / '..'))

    @unittest.skipUnless(os.name == 'nt', 'Windows state root path policy')
    def test_windows_selection_preserves_spelling_and_refuses_a_linked_root(self):
        alias = self.work / 'linked-state'
        self.assertEqual(state_root.get_state_dir(alias), alias)
        with patch.object(private_dirs, 'linked', side_effect=lambda path: Path(path) == alias), \
                self.assertRaises(BoardError) as refused:
            state_root.check_state_root(alias, create=True)
        self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(refused.exception.details['path'], str(alias))
        self.assertFalse(alias.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX state mode repair')
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
        temporary.assert_called_once_with(prefix='hey-my-buddy-ipc-')

    @unittest.skipIf(os.name == 'nt', 'POSIX state mode repair')
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
                if os.name == 'nt':
                    assert_inherited_private_acl(self, selected)
                else:
                    self.assertEqual(stat.S_IMODE(selected.stat().st_mode), 0o700)

    def test_path04_preflight_preserves_structural_error_object(self):
        self.state.mkdir(mode=private_dirs.directory_mode())
        error = BoardError('PRIVATE_PATH_UNSAFE', 'specific structural reason',
                           path=str(self.state if os.name == 'nt' else self.state / 'ipc'))
        with patch.object(rpc_config, 'configure_local_endpoint', side_effect=error):
            with self.assertRaises(BoardError) as caught:
                transport._cold_start_preflight(self.state)
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.payload(), error.payload())

    def test_path04_os_write_and_ipc_denial_only_map_access_denied(self):
        self.state.mkdir(mode=private_dirs.directory_mode())
        boundaries = ('write', 'named-pipe') if os.name == 'nt' else ('write', 'ipc')
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                sockets = Mock()
                sockets.return_value.bind.side_effect = PermissionError('injected local IPC denial')
                patcher = (patch.object(private_dirs, 'open_regular_fd',
                                        side_effect=PermissionError('injected state write denial'))
                           if boundary == 'write' else
                           patch.object(rpc_config, 'configure_local_endpoint',
                                        side_effect=PermissionError('injected Named Pipe denial'))
                           if boundary == 'named-pipe' else patch.object(transport.socket, 'socket', sockets))
                with patcher, self.assertRaises(transport.ServiceError) as caught:
                    transport._cold_start_preflight(self.state)
                self.assertEqual(caught.exception.code, 'LAUNCH_ACCESS_DENIED')
                self.assertIsInstance(caught.exception.__cause__, PermissionError)

    @unittest.skipIf(os.name == 'nt', 'POSIX IPC directory path')
    def test_path04_actual_cli_reports_structure_code_message_path(self):
        self.state.mkdir(mode=private_dirs.directory_mode())
        target = self.work / 'target'
        target.mkdir(mode=private_dirs.directory_mode())
        (self.state / 'ipc').symlink_to(target, target_is_directory=True)
        with self.assertRaises(BoardError) as caught:
            transport._cold_start_preflight(self.state)
        self.assertEqual(caught.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(caught.exception.message, 'IPC path contains a linked or unsafe component')
        self.assertEqual(caught.exception.details.get('path'), str(self.state / 'ipc'))
        child, receipt = cli_health(self.state, _child_environment(self.work))
        (self.work / 'cli-process.json').write_text(json.dumps(receipt, indent=2), encoding="utf-8", newline="\n")
        self.assertEqual(child.returncode, 1, receipt)
        self.assertEqual(json.loads(receipt['stdout'])['error'], caught.exception.payload())


class RealPathStateTests(unittest.TestCase):
    def case(self, name):
        work = Path(tempfile.mkdtemp(prefix='path-real-')).resolve()
        environment = _child_environment(work)
        sentinel = native_forbidden_executable(work, work / 'native-invoked')
        environment.update({'HMB_' + name + '_CLI': str(sentinel) for name in ('CLAUDE', 'CODEX', 'DSH', 'ZCODE')})
        environment.update(HMB_MAX_CONCURRENT='1', HMB_CHECKS_TMPDIR=os.environ['HMB_CHECKS_TMPDIR'])
        command = [sys.executable, str(Path(__file__).resolve()), '--case', name, str(work)]
        child = subprocess.Popen(command, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
        stdout, stderr = child.communicate(timeout=100)
        (work / 'case-process.json').write_text(json.dumps({'pid': child.pid, 'command': command,
                                                          'waitExit': child.returncode}, indent=2), encoding="utf-8", newline="\n")
        (work / 'case.log').write_text(stdout + stderr, encoding="utf-8", newline="\n")
        self.assertEqual(child.returncode, 0, f'{name}: {work}\n{stdout}\n{stderr}')

    @unittest.skipIf(os.name == 'nt', 'Windows rejects linked state roots')
    def test_path01_linked_cli_health_and_canonical_native_root(self):
        self.case('linked-cli')

    @unittest.skipIf(os.name == 'nt', 'POSIX state mode repair')
    def test_path03_real_cold_start_repairs_755(self):
        self.case('cold-755')

    @unittest.skipIf(os.name == 'nt', 'POSIX state mode repair')
    def test_path03_real_direct_daemon_repairs_755(self):
        self.case('daemon-755')

    @unittest.skipUnless(os.name == 'nt', 'Windows profile ACL')
    def test_windows_real_daemon_uses_inherited_state_acl_and_named_pipe_domain(self):
        self.case('windows-acl')


class ReadonlyPathStateTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX directory mode mutation')
    def test_path03_500_state_real_ping_peer_has_no_mutations(self):
        # Reuse the existing tiny native peer, without SQLite permission effects.
        fixture = attach_fixture.RpcReadOnlySetupTests('test_explicit_state_reaches_rpc_setup')
        fixture.setUp()
        evidence = {'layer': 'native ping transport, no SQLite', 'state': str(fixture.directory)}
        try:
            from protocol import test_rpc_config as probe_fixture
            catalog = write_catalog_fixture(fixture.directory)
            invoked = fixture.directory / 'native-invoked'
            sentinel = native_forbidden_executable(fixture.directory, invoked)
            original_environment = probe_fixture.private_environment

            def offline_environment(root, **overrides):
                environment = original_environment(root, **overrides)
                environment['HMB_MODEL_CATALOG_FILE'] = str(catalog)
                environment.update({'HMB_' + harness + '_CLI': str(sentinel)
                                    for harness in ('CLAUDE', 'CODEX', 'DSH', 'ZCODE')})
                return environment

            with patch.object(probe_fixture, 'private_environment', side_effect=offline_environment):
                endpoint, received = fixture.native_ping_peer()
            # Establish the client role before observing attach-only I/O.
            self.assertEqual(transport.call_service('ping', state_dir=fixture.directory), {'token': endpoint['token']})
            received_before = len(received.read_text(encoding="utf-8").splitlines())
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
            self.assertEqual(len(received.read_text(encoding="utf-8").splitlines()), received_before + 2)
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
            Path(os.environ['HMB_CHECKS_TMPDIR'], 'readonly-path-evidence.json').write_text(json.dumps(evidence, indent=2), encoding="utf-8", newline="\n")
            self.assertTrue(clean, 'native peer cleanup failed')
            self.assertEqual(evidence['peerReceipt'].get('waitExit'), 0)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--case':
        run_path_case(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
