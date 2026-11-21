"""The single projection of harness, model-service and effort facts."""
from __future__ import annotations

import json

from ...json_codec import canonical_json
from ...protocol.reasons import reason, reason_from_json


def _project(buddy, model, health, current) -> dict:
    value = dict(buddy)
    cause = None
    content = None
    if value['retired']:
        cause = reason('catalog.effort-retired')
    elif model['confirmed_absent_at'] is not None:
        cause = reason('catalog.model-absent')
    elif model['unavailable_code'] is not None:
        cause = reason_from_json(model['unavailable_code'], model['unavailable_params_json'])
        content = model['unavailable_content']
    catalog_available = cause is None
    if health['status'] != 'ready':
        if not health['reason']:
            raise AssertionError('An unavailable harness must have a reason')
        if cause is None:
            cause = health['reason']
    value.update({
        'catalog_available': int(catalog_available),
        'available': int(cause is None),
        'capabilities_json': canonical_json([
            f"execution:{value['harness']}", f"effort:{value['effort']}",
            *[f'input:{item}' for item in json.loads(model['input_modalities_json'])],
        ]),
        'context_window': model['context_window'],
        'description': model['description'],
        'source': (f"catalog:{value['harness']}:{model['source_sequence']}"
                   if model['source_sequence'] is not None else ''),
        'unavailable_code': cause['code'] if cause else None,
        'unavailable_params_json': canonical_json(cause['params']) if cause else None,
        'unavailable_content': content,
        'pending_since': model['pending_since'],
        'confirmed_absent_at': model['confirmed_absent_at'],
        'catalog_status': ('pending' if model['pending_since'] is not None else
                           'available' if catalog_available else 'unavailable'),
        'input_modalities_json': model['input_modalities_json'],
        'harness_status': health['status'],
        'catalog_state': current['status'] if current is not None and current['status'] != 'unread' else 'unknown',
        'catalog_reason': current['reason'] if current else None,
    })
    return value


def compose(db, buddy) -> dict:
    """Compose a buddy's current facts; health never rewrites catalog facts."""
    from ..service.harness_health import read_health

    model = db.execute('SELECT * FROM model_services WHERE harness=? AND provider=? AND model=?',
                       tuple(buddy[key] for key in ('harness', 'provider', 'model'))).fetchone()
    health = read_health(db, buddy['harness'])
    current = db.execute('SELECT status,reason FROM catalog_current WHERE harness=?',
                         (buddy['harness'],)).fetchone()
    return _project(buddy, model, health, current)


def rows(db, *, harness=None) -> list[dict]:
    from ..service.harness_health import read_health

    where, params = (' WHERE harness=?', (harness,)) if harness is not None else ('', ())
    buddies = db.execute('SELECT * FROM buddies' + where + ' ORDER BY rowid', params).fetchall()
    models = {(row['harness'], row['provider'], row['model']): row for row in
              db.execute('SELECT * FROM model_services' + where, params)}
    current = {row['harness']: row for row in db.execute('SELECT * FROM catalog_current' + where, params)}
    health = {name: read_health(db, name) for name in {row['harness'] for row in buddies}}
    return [_project(buddy, models[tuple(buddy[key] for key in ('harness', 'provider', 'model'))],
                     health[buddy['harness']], current.get(buddy['harness'])) for buddy in buddies]


def get(db, buddy_id) -> dict | None:
    buddy = db.execute('SELECT * FROM buddies WHERE buddy_id=?', (buddy_id,)).fetchone()
    return compose(db, buddy) if buddy is not None else None
