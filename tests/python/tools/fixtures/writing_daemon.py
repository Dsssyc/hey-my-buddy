"""Real private daemon with two coordinated, atomic synthetic history writes.

Only catalog/harness discovery and the data being written are fixtures. The
service owns the store and its writer thread; no execution or harness is started.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from blackboard.service.fixtures.daemon_with_catalog import service as catalog_service
from hey_my_buddy.blackboard.service.daemon import Daemon, main

stop = threading.Event()
writers: list[threading.Thread] = []


def service(daemon):
    result = catalog_service(daemon)
    state = Path(os.environ['HMB_STATE_DIR']).resolve()
    control = Path(os.environ['HMB_TEST_WRITE_CONTROL']).resolve()
    if control.parent != state.parent or control == state:
        raise ValueError('Writer control must be a private sibling of the source')

    def write():
        try:
            with daemon.store.db.read() as connection:
                run_id = connection.execute('SELECT run_id FROM workflow_runs').fetchone()[0]
            (control / 'ready').touch()
            for version in (1, 2):
                request = control / f'write-{version}'
                while not request.exists():
                    if stop.wait(0.01):
                        return
                # These two facts must appear together in the exported snapshot.
                with daemon.store.db.write() as connection:
                    connection.execute('UPDATE tasks SET acceptance_note=? WHERE task_id=?',
                                       (f'fixture-version-{version}', run_id))
                    connection.execute('UPDATE workflow_runs SET title=? WHERE run_id=?',
                                       (f'fixture-version-{version}', run_id))
                    connection.execute(
                        'INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                        (run_id, 'fixture.concurrent_write', json.dumps({'version': version}), daemon.store.now()))
                (control / f'committed-{version}').touch()
        except BaseException as error:
            (control / 'error').write_text(repr(error), encoding='utf-8')

    thread = threading.Thread(target=write, name='synthetic-service-writer', daemon=True)
    writers.append(thread)
    thread.start()
    return result


if __name__ == '__main__':
    Daemon.service = service
    try:
        raise SystemExit(main())
    finally:
        stop.set()
        for thread in writers:
            thread.join(timeout=5)
