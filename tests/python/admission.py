"""In-process tests call the same internal service admission as the daemon."""


def admit_execution(client, nonce, *, task_id=None):
    return client._call.__self__.service.admit_execution(nonce, task_id)


def admit_store(store, params):
    return store.admit_execution(params['nonce'], params.get('taskId') or params.get('runId'))


def release_before_spawn(client, attempt_id, reason="execution-process-not-created"):
    return client._call.__self__.store.release_execution(attempt_id, reason=reason)


def admit_board(board, params):
    return board.service.admit_execution(params['nonce'], params.get('taskId') or params.get('runId'))


def run_next(client, nonce='test-execution-nonce-000000000000'):
    from hey_my_buddy.buddy.runtime.execution import Execution
    from hey_my_buddy.protocol import process_identity
    from unittest.mock import patch
    claim = admit_execution(client, nonce)['claim']
    if claim is None:
        return 'idle'
    # Production has one journal per execution process. This in-process fixture
    # gives each simulated process that same isolated module lifetime.
    with patch.object(process_identity, '_active_native_journal', None):
        Execution(claim, client.state_dir, nonce, client=client,
                  session_record_path=claim["sessionRecordPath"]).run()
    return 'ran'
