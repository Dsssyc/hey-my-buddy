"""Real service/transport with the explicitly supplied synthetic native catalog."""
import json
import os
import sys
from pathlib import Path

from hey_my_buddy.blackboard.service.daemon import Daemon, main
from hey_my_buddy.blackboard.service.harness_health import HARNESSES
from hey_my_buddy.buddy.harnesses.runtime_selection import bound

original = Daemon.service


def service(daemon):
    result = original(daemon)
    result.automatic_discovery = False
    # Native discovery is the fixture boundary, just like MODEL_CATALOG_FILE.
    # No private service integration test inspects the user's native login.
    with daemon.store.db.write() as db:
        for name in HARNESSES:
            executable = os.environ.get('BUDDY_' + name.upper() + '_CLI')
            command = [executable or 'fixture-' + name]
            if executable and Path(executable).suffix == '.py':
                command.insert(0, sys.executable)
            record = {'adapter': name, 'status': 'ready', 'available': True, 'command': command,
                      'executable': executable, 'version': 'fixture', 'source': 'fixture'}
            db.execute("INSERT OR REPLACE INTO harness_health(adapter,status,record_json) VALUES(?,'ready',?)", (name, json.dumps(record)))
    result.harnesses.refresh = lambda name, **kwargs: result.harnesses.get(name)
    # The file-catalog branch disables automatic discovery. Bind availability
    # reads to the same synthetic records, including the capabilities operation,
    # so an unbound adapter cannot fall back to the user's installed native CLI.
    original_guard = result._guard

    def guard(*args, **kwargs):
        with bound(result.harnesses.all()):
            return original_guard(*args, **kwargs)

    result._guard = guard
    return result


if __name__ == '__main__':
    state = Path(os.environ['BUDDY_STATE_DIR']).resolve()
    assert Path(os.environ['BUDDY_MODEL_CATALOG_FILE']).resolve().is_relative_to(state)
    Daemon.service = service
    raise SystemExit(main())
