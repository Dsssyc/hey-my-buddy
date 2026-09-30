"""Native account protocol fixture. Never calls the real CLI or a provider."""
import json
import os
from pathlib import Path
import sys

home = Path(os.environ['CODEX_HOME'])
home.mkdir(mode=0o700, parents=True, exist_ok=True)
auth = home / 'auth.json'
helpers = home / 'tmp' / 'arg0' / 'codex-arg0-fixture'
helpers.mkdir(mode=0o700, parents=True, exist_ok=True)
for name in ('apply_patch', 'applypatch', 'codex-execve-wrapper'):
    link = helpers / name
    if not link.is_symlink():
        link.symlink_to(sys.executable)
if 'login' in sys.argv:
    key = sys.stdin.read().strip()
    with auth.open('w') as output:
        json.dump({'type': 'apiKey', 'fixtureKey': key}, output)
    auth.chmod(0o600)
    raise SystemExit(0)


def emit(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if 'id' not in request:
        continue
    response = {}
    if method == 'account/read':
        response = {'account': {'type': json.loads(auth.read_text())['type']} if auth.exists() else None}
    elif method == 'account/login/start':
        response = {'type': 'chatgpt', 'loginId': 'native-fixture-id', 'authUrl': 'https://auth.openai.com/fixture-only'}
    elif method == 'account/login/cancel':
        response = {'status': 'canceled'}
    elif method == 'account/logout':
        auth.unlink(missing_ok=True)
    emit({'id': request['id'], 'result': response})
    if method == 'account/login/start' and '--complete' in sys.argv:
        auth.write_text(json.dumps({'type': 'chatgpt'}))
        auth.chmod(0o600)
        emit({'method': 'account/login/completed', 'params': {'loginId': 'native-fixture-id', 'success': True}})
