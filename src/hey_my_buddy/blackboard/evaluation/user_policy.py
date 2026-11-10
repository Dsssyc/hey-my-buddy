"""User-owned field patches; never a source of model or catalog facts."""
from __future__ import annotations

import unicodedata

from ...protocol import schemas
from ...protocol.reasons import reason
from ...errors import BoardError
from . import models

PATCH_FIELDS = frozenset({'buddySettings', 'preferenceChanges', 'modelCorrespondenceChanges',
                          'configuration', 'modelServiceConcurrency'})

#: The per-model-service limit a user may set, and the fields one patch entry accepts.
#: ``active`` and every other derived field are rejected: occupancy is observation,
#: never input. There is no effort dimension — effort variants share one model service.
MODEL_SERVICE_CONCURRENCY_FIELDS = frozenset({'harness', 'provider', 'model', 'limit'})
SQLITE_INTEGER_MAX = 2 ** 63 - 1


def _alias(value: object, buddy_id: str) -> str | None:
    """Validate the console's alias input once, before storing its trimmed form."""
    if value is None:
        return None
    if not isinstance(value, str):
        violation = 'type'
    elif any(unicodedata.category(char) == 'Cc' for char in value):
        violation = 'format'
    else:
        value = value.strip()
        if not value:
            violation = 'empty'
        elif len(value) > 64:
            violation = 'length'
        elif ' / ' in value or '[' in value or ']' in value:
            violation = 'format'
        elif value.casefold() == 'route':
            raise BoardError('ALIAS_RESERVED', 'Alias is reserved', buddyId=buddy_id,
                             reason=reason('alias.reserved'))
        else:
            return value
    raise BoardError('ALIAS_INVALID', 'Alias has an invalid form', buddyId=buddy_id,
                     reason=reason('alias.invalid', violation=violation))


def _model_service_concurrency_changes(params: dict) -> list[dict]:
    """Validate the ``modelServiceConcurrency`` patch: model service tuples and limits only."""
    values = params.get('modelServiceConcurrency', [])
    if not isinstance(values, list):
        raise BoardError('INVALID_ARGUMENT', 'modelServiceConcurrency must be a list')
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', 'modelServiceConcurrency entries must be objects')
        schemas.reject_unknown(value, MODEL_SERVICE_CONCURRENCY_FIELDS, 'modelServiceConcurrency')
        model_service = tuple(
            schemas.required_string(value, name, max_length=256)
            for name in ('harness', 'provider', 'model')
        )
        if model_service in seen:
            raise BoardError('INVALID_ARGUMENT', 'modelServiceConcurrency repeats a model service')
        seen.add(model_service)
        limit = value.get('limit')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= SQLITE_INTEGER_MAX:
            raise BoardError('INVALID_ARGUMENT', 'limit must be a positive SQLite integer')
    return values


def require_console() -> None:
    from ..tasks.workflow import current_scope, CONSOLE_KIND, SERVICE_KIND
    scope = current_scope() or {'kind': SERVICE_KIND}
    if scope.get('kind') != CONSOLE_KIND:
        raise BoardError('FORBIDDEN', 'User policy requires the authenticated console')


def policy_view(connection, buddy_ids: list[str]) -> dict:
    """User-owned buddy preferences and model correspondences for the console."""
    from ..routing.profile_data import console_model_view
    return {
        'preferences': preferences(connection, buddy_ids),
        'models': [console_model_view(connection, row[0]) for row in connection.execute(
            'SELECT model_id FROM models ORDER BY display_name,model_id')],
        'modelCorrespondences': models.correspondence_view(connection, buddy_ids),
    }


def preferences(connection, buddy_ids: list[str]) -> list[dict]:
    marks = ','.join('?' for _ in buddy_ids) or 'NULL'
    return [{'buddyId': row['buddy_id'], 'mode': row['mode'], 'note': row['note']}
            for row in connection.execute(f'SELECT * FROM evaluation_preferences WHERE buddy_id IN ({marks})'
                                          ' ORDER BY buddy_id', buddy_ids)]


def _changes(params: dict, field: str, fields: set[str]) -> list[dict]:
    values = params.get(field, [])
    if not isinstance(values, list):
        raise BoardError('INVALID_ARGUMENT', f'{field} must be a list')
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', f'{field} entries must be objects')
        schemas.reject_unknown(value, fields, field)
        buddy_id = schemas.required_string(value, 'buddyId', max_length=128, pattern=schemas.IDENTIFIER_PATTERN)
        if buddy_id in seen:
            raise BoardError('INVALID_ARGUMENT', f'{field} repeats a buddyId')
        seen.add(buddy_id)
    return values


def publish(evaluation, connection, *, now: str, params: dict) -> dict:
    """Apply only requested human fields in the caller's publication transaction."""
    from ..service.harness_health import read_health
    provided = set(params) & PATCH_FIELDS
    if not provided:
        raise BoardError('INVALID_ARGUMENT', 'A user publication must contain a field patch')
    settings = _changes(params, 'buddySettings', {'buddyId', 'enabled', 'alias'})
    preferences = _changes(params, 'preferenceChanges', {'buddyId', 'mode', 'note'})
    correspondence_count, lost_references = models.apply_user_changes(
        connection, params.get('modelCorrespondenceChanges', []), now)
    model_limits = _model_service_concurrency_changes(params)
    buddies = {}
    for entry in settings + preferences:
        buddy_id = entry['buddyId']
        if buddy_id not in buddies:
            row = connection.execute('SELECT * FROM buddies WHERE buddy_id=?', (buddy_id,)).fetchone()
            if row is None:
                raise BoardError('NOT_FOUND', 'Unknown buddyId', buddyId=buddy_id)
            buddies[buddy_id] = dict(row)
            buddies[buddy_id]['available'] = bool(row['available']) and read_health(connection, row['harness'])['status'] == 'ready'
    for entry in settings:
        buddy_id = entry['buddyId']
        if 'enabled' not in entry and 'alias' not in entry:
            raise BoardError('INVALID_ARGUMENT', 'buddySettings requires enabled or alias', buddyId=buddy_id)
        buddy = buddies[buddy_id]
        if 'enabled' in entry:
            if type(entry['enabled']) is not bool:
                raise BoardError('INVALID_ARGUMENT', 'enabled must be a boolean', buddyId=buddy_id)
            if entry['enabled'] and not buddy['available']:
                raise BoardError('BUDDY_UNAVAILABLE', 'This buddy is not currently available', buddyId=buddy_id)
            buddy['enabled'] = int(entry['enabled'])
            connection.execute('UPDATE buddies SET enabled=? WHERE buddy_id=?', (buddy['enabled'], buddy_id))
        if 'alias' in entry:
            connection.execute('UPDATE buddies SET alias=? WHERE buddy_id=?',
                               (_alias(entry['alias'], buddy_id), buddy_id))
    if any('alias' in entry for entry in settings):
        aliases: dict[str, str] = {}
        changed = {entry['buddyId'] for entry in settings if 'alias' in entry}
        for row in connection.execute('SELECT buddy_id,alias FROM buddies WHERE alias IS NOT NULL ORDER BY buddy_id'):
            folded = row['alias'].casefold()
            other = aliases.get(folded)
            if other is not None:
                buddy_id, conflicting = (row['buddy_id'], other) if row['buddy_id'] in changed else (other, row['buddy_id'])
                identity = connection.execute(
                    'SELECT harness,provider,model,effort FROM buddies WHERE buddy_id=?', (conflicting,)).fetchone()
                raise BoardError('ALIAS_CONFLICT', 'Alias is already used by another buddy',
                                 buddyId=buddy_id, conflictingBuddyId=conflicting,
                                 reason=reason('alias.conflict', conflictingBuddy=dict(identity)))
            aliases[folded] = row['buddy_id']
    for entry in preferences:
        if 'mode' not in entry or entry['mode'] not in (None, 'pin', 'prefer', 'exclude'):
            raise BoardError('INVALID_ARGUMENT', 'mode must be pin, prefer, exclude or null')
        buddy_id, mode = entry['buddyId'], entry['mode']
        note = (schemas.bounded_text(entry, 'note', max_bytes=schemas.MAX_NOTE_BYTES, allow_empty=True)[0]
                or None) if entry.get('note') is not None else None
        previous = connection.execute('SELECT mode FROM evaluation_preferences WHERE buddy_id=?', (buddy_id,)).fetchone()
        if mode == 'pin' and (previous is None or previous['mode'] != 'pin') and not (buddies[buddy_id]['available'] and buddies[buddy_id]['enabled']):
            raise BoardError('BUDDY_UNAVAILABLE', 'A new pin requires an available enabled buddy', buddyId=buddy_id)
        if mode is None:
            connection.execute('DELETE FROM evaluation_preferences WHERE buddy_id=?', (buddy_id,))
        else:
            connection.execute('INSERT INTO evaluation_preferences(buddy_id,mode,note) VALUES(?,?,?) ON CONFLICT(buddy_id) DO UPDATE SET mode=excluded.mode,note=excluded.note', (buddy_id, mode, note))
    if lost_references:
        # One check after every change of the save, in whatever order they came:
        # a model is deleted only once no reference remains for it.
        models.delete_unreferenced(connection, lost_references)
    for entry in model_limits:
        if connection.execute(
            'SELECT 1 FROM buddies WHERE harness=? AND provider=? AND model=? LIMIT 1',
            (entry['harness'], entry['provider'], entry['model']),
        ).fetchone() is None:
            raise BoardError('NOT_FOUND', 'Unknown model service')
        # A model service setting is independent of buddy availability: it survives
        # discovery marking a model unavailable and applies again when the model service
        # returns. Only the named model services are touched; unrelated settings and
        # fields keep their stored values.
        connection.execute(
            'INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)'
            ' VALUES(?,?,?,?)'
            ' ON CONFLICT(harness,provider,model) DO UPDATE SET concurrency_limit=excluded.concurrency_limit',
            (
                entry['harness'],
                entry['provider'],
                entry['model'],
                entry['limit'],
            ),
        )
    if 'configuration' in provided:
        from ..routing.router_settings import validate_router_settings_patch
        configuration = validate_router_settings_patch(params['configuration'])
        from ..routing import router
        current = router.configuration(connection)
        merged = {**current, **configuration}
        if 'routerBuddyIds' in configuration:
            # Every listed ID must reference a published buddy with a complete
            # identity. Enabled, availability, harness health, quota and mode
            # eligibility are deliberately not save requirements: the resolver
            # explains them per item, and a temporarily unhealthy first item never
            # blocks saving the whole list.
            for buddy_id in merged['routerBuddyIds']:
                row = connection.execute('SELECT * FROM buddies WHERE buddy_id=?',
                                         (buddy_id,)).fetchone()
                if row is None:
                    raise BoardError('NOT_FOUND', 'Unknown buddyId', buddyId=buddy_id)
        if configuration.get('maintenanceBuddyId') is not None:
            buddy_id = configuration['maintenanceBuddyId']
            if connection.execute('SELECT 1 FROM buddies WHERE buddy_id=?', (buddy_id,)).fetchone() is None:
                raise BoardError('NOT_FOUND', 'Unknown maintenance buddyId', buddyId=buddy_id)
        if configuration.get('profileBuddyId') is not None:
            buddy_id = configuration['profileBuddyId']
            if connection.execute('SELECT 1 FROM buddies WHERE buddy_id=?', (buddy_id,)).fetchone() is None:
                raise BoardError('NOT_FOUND', 'Unknown profile buddyId', buddyId=buddy_id)
        router._write_settings(connection, configuration)
    counts = {
        'buddySettings': len(settings),
        'preferenceChanges': len(preferences),
        'modelCorrespondenceChanges': correspondence_count,
        'modelServiceConcurrency': len(model_limits),
        'provided': sorted(provided),
    }
    return {'counts': counts}
