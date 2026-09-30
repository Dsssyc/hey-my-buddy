"""Real service/transport with the explicitly supplied synthetic native catalog."""
import json
import os
from pathlib import Path
import shutil

from buddy.daemon import Daemon, main
from buddy.harness_health import HARNESSES

original = Daemon.service


def service(daemon):
    result = original(daemon)
    result.automatic_discovery = False
    # Native discovery is the fixture boundary, just like MODEL_CATALOG_FILE.
    # No private service integration test inspects the user's native login.
    with daemon.store.db.write() as db:
        for name in HARNESSES:
            executable = os.environ.get('BUDDY_' + name.upper() + '_CLI')
            if name == 'dsh':
                executable = os.environ.get('BUDDY_RUNNER_PATH')
                command = [os.environ.get('BUDDY_NODE') or shutil.which('node') or 'node', executable or 'fixture-dsh']
            else:
                command = [executable or 'fixture-' + name]
            record = {'adapter': name, 'status': 'ready', 'available': True, 'command': command,
                      'executable': executable, 'version': 'fixture', 'source': 'fixture'}
            db.execute("INSERT OR REPLACE INTO harness_health(adapter,status,record_json) VALUES(?,'ready',?)", (name, json.dumps(record)))
    result.harnesses.refresh = lambda name, **kwargs: result.harnesses.get(name)
    return result


if __name__ == '__main__':
    state = Path(os.environ['BUDDY_STATE_DIR']).resolve()
    assert Path(os.environ['BUDDY_MODEL_CATALOG_FILE']).resolve().is_relative_to(state)
    Daemon.service = service
    raise SystemExit(main())
