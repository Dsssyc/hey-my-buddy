"""Hold a real console read across shutdown in a disposable service process."""
from __future__ import annotations

import http.client
import json
import os
import threading
import traceback
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from blackboard.service.fixtures.daemon_with_catalog import service
from hey_my_buddy.blackboard.service.daemon import Daemon
from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
from hey_my_buddy.console import reads


def main():
    daemon = Daemon(Path(os.environ["HMB_STATE_DIR"]))
    started, reading, release, observed, closed = (threading.Event() for _ in range(5))
    failures, responses = [], []
    # The same instrumented lock also runs against the pre-fix Database, whose
    # lock had no wait method, so the regression reaches the actual close race.
    condition = threading.Condition(threading.Lock())
    daemon.store.db._connection_lock = condition
    wait, close = condition.wait, daemon.store.db.close
    start, maintenance_view = ExecutionScheduler.start, reads._maintenance_view

    def start_scheduler(scheduler):
        start(scheduler)
        started.set()

    def blocked_view(connection):
        result = maintenance_view(connection)
        reading.set()
        assert release.wait(30), "read was not released"
        assert connection.execute("SELECT 42").fetchone()[0] == 42
        return result

    def waiting():
        observed.set()
        return wait()

    def close_database():
        try:
            close()
        finally:
            closed.set()
            observed.set()

    def request():
        url = urlsplit(daemon.console.status()["baseUrl"])
        client = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
        try:
            client.request("GET", "/api/console")
            response = client.getresponse()
            responses.append((response.status, json.loads(response.read())["tasks"]))
        except BaseException:
            failures.append(traceback.format_exc())
        finally:
            client.close()

    def stop_during_read():
        request_thread = threading.Thread(target=request)
        try:
            assert started.wait(30), "service did not start"
            request_thread.start()
            assert reading.wait(30), "HTTP read did not enter its transaction"
            assert daemon.on_stop({"drainSeconds": 0})["stopped"]
            assert observed.wait(30), "database close did not start"
            assert not closed.is_set(), "database closed before HTTP read exited"
        except BaseException:
            failures.append(traceback.format_exc())
        finally:
            release.set()
            daemon.stopping.set()
            daemon.store.stop_waiters()
            if request_thread.ident is not None:
                request_thread.join(30)
                if request_thread.is_alive():
                    failures.append("HTTP client did not exit")

    controller = threading.Thread(target=stop_during_read)
    with patch.object(Daemon, "service", service), \
         patch.object(ExecutionScheduler, "start", start_scheduler), \
         patch.object(reads, "_maintenance_view", blocked_view), \
         patch.object(condition, "wait", waiting), \
         patch.object(daemon.store.db, "close", close_database):
        controller.start()
        result = daemon.run()
        controller.join(30)
    print(json.dumps({"result": result, "endpointRemoved": not daemon.endpoint_path.exists(),
                      "responses": responses, "failures": failures}))
    return int(bool(failures) or controller.is_alive())


if __name__ == "__main__":
    raise SystemExit(main())
