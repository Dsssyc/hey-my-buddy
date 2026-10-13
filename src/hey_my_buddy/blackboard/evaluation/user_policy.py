"""User-owned field patches; never a source of model or catalog facts."""
from __future__ import annotations

from ...protocol import schemas
from ...errors import BoardError

PATCH_FIELDS = frozenset({'profileSettings', 'preferenceChanges', 'modelServiceNoteChanges',
                          'configuration', 'modelServiceConcurrency'})
MODEL_SERVICE_FIELDS = ('adapter', 'provider', 'model')

#: The per-model-service limit a user may set, and the fields one patch entry accepts.
#: ``active`` and every other derived field are rejected: occupancy is observation,
#: never input. There is no effort dimension — effort variants share one model service.
MODEL_SERVICE_CONCURRENCY_FIELDS = frozenset({'adapter', 'provider', 'model', 'limit'})


def _model_service_concurrency_changes(params: dict) -> list[dict]:
    """Validate the ``modelServiceConcurrency`` patch: model service tuples and limits only."""
    values = params.get('modelServiceConcurrency', [])
    if not isinstance(values, list) or len(values) > 200:
        raise BoardError('INVALID_ARGUMENT', 'modelServiceConcurrency must contain at most 200 patches')
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', 'modelServiceConcurrency entries must be objects')
        schemas.reject_unknown(value, MODEL_SERVICE_CONCURRENCY_FIELDS, 'modelServiceConcurrency')
        model_service = tuple(
            schemas.required_string(value, name, max_length=256)
            for name in ('adapter', 'provider', 'model')
        )
        if model_service in seen:
            raise BoardError('INVALID_ARGUMENT', 'modelServiceConcurrency repeats a model service')
        seen.add(model_service)
        limit = value.get('limit')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
            raise BoardError('INVALID_ARGUMENT', 'limit must be an integer between 1 and 32')
    return values


def require_console() -> None:
    from ..tasks.workflow import current_scope, CONSOLE_KIND, SERVICE_KIND
    scope = current_scope() or {'kind': SERVICE_KIND}
    if scope.get('kind') != CONSOLE_KIND:
        raise BoardError('FORBIDDEN', 'User policy requires the authenticated console')


def _model_service_changes(params: dict, field: str, fields: set[str]) -> list[dict]:
    """Validate a model-service-keyed patch: one entry per ``(adapter, provider, model)``."""
    values = params.get(field, [])
    if not isinstance(values, list) or len(values) > 200:
        raise BoardError('INVALID_ARGUMENT', f'{field} must contain at most 200 patches')
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', f'{field} entries must be objects')
        schemas.reject_unknown(value, fields, field)
        model_service = tuple(schemas.required_string(value, name, max_length=256) for name in MODEL_SERVICE_FIELDS)
        if model_service in seen:
            raise BoardError('INVALID_ARGUMENT', f'{field} repeats a model service')
        seen.add(model_service)
    return values


def _model_service(entry: dict) -> tuple[str, str, str]:
    return tuple(entry[name] for name in MODEL_SERVICE_FIELDS)


def _reason(entry: dict) -> str:
    reason = entry.get('reason', '')
    if not isinstance(reason, str) or len(reason) > 500:
        raise BoardError('INVALID_ARGUMENT', 'reason must be a string of at most 500 characters')
    return reason


def policy_view(connection, profile_ids: list[str]) -> dict:
    """User-owned buddy preferences and model annotations for the console."""
    return {
        'preferences': preferences(connection, profile_ids),
        'modelServiceNotes': model_service_notes(connection, profile_ids),
    }


def preferences(connection, profile_ids: list[str]) -> list[dict]:
    marks = ','.join('?' for _ in profile_ids) or 'NULL'
    return [{'profileId': row['profile_id'], 'mode': row['mode'], 'reason': row['reason']}
            for row in connection.execute(f'SELECT * FROM evaluation_preferences WHERE profile_id IN ({marks})'
                                          ' ORDER BY profile_id', profile_ids)]


def model_service_notes(connection, profile_ids: list[str]) -> list[dict]:
    marks = ','.join('?' for _ in profile_ids) or 'NULL'
    return [{'adapter': row['adapter'], 'provider': row['provider'], 'model': row['model'], 'text': row['text'],
             'updatedAt': row['updated_at']}
            for row in connection.execute(
                'SELECT * FROM model_service_notes f WHERE EXISTS(SELECT 1 FROM evaluation_profiles p'
                ' WHERE p.adapter=f.adapter AND p.provider=f.provider AND p.model=f.model'
                f' AND p.profile_id IN ({marks})) ORDER BY f.adapter, f.provider, f.model', profile_ids)]


def _changes(params: dict, field: str, fields: set[str]) -> list[dict]:
    values = params.get(field, [])
    if not isinstance(values, list) or len(values) > 200:
        raise BoardError('INVALID_ARGUMENT', f'{field} must contain at most 200 patches')
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', f'{field} entries must be objects')
        schemas.reject_unknown(value, fields, field)
        profile_id = schemas.required_string(value, 'profileId', max_length=128, pattern=schemas.IDENTIFIER_PATTERN)
        if profile_id in seen:
            raise BoardError('INVALID_ARGUMENT', f'{field} repeats a profileId')
        seen.add(profile_id)
    return values


def publish(evaluation, connection, *, now: str, params: dict) -> dict:
    """Apply only requested human fields in the caller's publication transaction."""
    from ..service.harness_health import read_health
    provided = set(params) & PATCH_FIELDS
    if not provided:
        raise BoardError('INVALID_ARGUMENT', 'A user publication must contain a field patch')
    settings = _changes(params, 'profileSettings', {'profileId', 'enabled'})
    preferences = _changes(params, 'preferenceChanges', {'profileId', 'mode', 'reason'})
    annotations = _model_service_changes(params, 'modelServiceNoteChanges', {*MODEL_SERVICE_FIELDS, 'text'})
    model_limits = _model_service_concurrency_changes(params)
    enabled_model_services: list[tuple[str, str, str]] = []
    profiles = {}
    for entry in settings + preferences:
        profile_id = entry['profileId']
        if profile_id not in profiles:
            row = connection.execute('SELECT * FROM evaluation_profiles WHERE profile_id=?', (profile_id,)).fetchone()
            if row is None:
                raise BoardError('NOT_FOUND', 'Unknown profileId', profileId=profile_id)
            profiles[profile_id] = dict(row)
            profiles[profile_id]['available'] = bool(row['available']) and read_health(connection, row['adapter'])['available']
    touched_model_services = {tuple(profiles[entry["profileId"]][name] for name in MODEL_SERVICE_FIELDS) for entry in settings}
    initially_enabled = {
        model_service: connection.execute(
            "SELECT 1 FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=? AND enabled=1 LIMIT 1",
            model_service,
        ).fetchone() is not None
        for model_service in touched_model_services
    }
    for entry in settings:
        if type(entry.get('enabled')) is not bool:
            raise BoardError('INVALID_ARGUMENT', 'enabled must be a boolean')
        profile = profiles[entry['profileId']]
        if entry['enabled'] and not profile['available']:
            raise BoardError('CONFIGURATION_UNAVAILABLE', 'This configuration is not currently available', profileId=entry['profileId'])
        profile['enabled'] = int(entry['enabled'])
        connection.execute('UPDATE evaluation_profiles SET enabled=? WHERE profile_id=?', (profile['enabled'], entry['profileId']))
    # Compare the whole publication's before/after state. Moving the enabled
    # effort within one model must not make a transient disable trigger a fetch.
    for model_service in sorted(touched_model_services):
        if not initially_enabled[model_service] and connection.execute(
            "SELECT 1 FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=? AND enabled=1 LIMIT 1",
            model_service,
        ).fetchone() is not None:
            enabled_model_services.append(model_service)
    for entry in annotations:
        model_service = _model_service(entry)
        if connection.execute('SELECT 1 FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=? LIMIT 1',
                              model_service).fetchone() is None:
            raise BoardError('NOT_FOUND', 'Unknown model service')
    for entry in preferences:
        if 'mode' not in entry or entry['mode'] not in (None, 'pin', 'prefer', 'exclude'):
            raise BoardError('INVALID_ARGUMENT', 'mode must be pin, prefer, exclude or null')
        profile_id, mode, reason = entry['profileId'], entry['mode'], _reason(entry)
        previous = connection.execute('SELECT mode FROM evaluation_preferences WHERE profile_id=?', (profile_id,)).fetchone()
        if mode == 'pin' and (previous is None or previous['mode'] != 'pin') and not (profiles[profile_id]['available'] and profiles[profile_id]['enabled']):
            raise BoardError('CONFIGURATION_UNAVAILABLE', 'A new pin requires an available enabled configuration', profileId=profile_id)
        if mode is None:
            connection.execute('DELETE FROM evaluation_preferences WHERE profile_id=?', (profile_id,))
        else:
            connection.execute('INSERT INTO evaluation_preferences(profile_id,mode,reason) VALUES(?,?,?) ON CONFLICT(profile_id) DO UPDATE SET mode=excluded.mode,reason=excluded.reason', (profile_id, mode, reason))
    for entry in annotations:
        text = entry.get('text')
        if not isinstance(text, str) or len(text) > 4000:
            raise BoardError('INVALID_ARGUMENT', 'text must be a string of at most 4000 characters')
        if not text:
            connection.execute('DELETE FROM model_service_notes WHERE adapter=? AND provider=? AND model=?', _model_service(entry))
        else:
            connection.execute('INSERT INTO model_service_notes(adapter,provider,model,text,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(adapter,provider,model) DO UPDATE SET text=excluded.text,updated_at=excluded.updated_at', (*_model_service(entry), text, now))
    for entry in model_limits:
        if connection.execute(
            'SELECT 1 FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=? LIMIT 1',
            (entry['adapter'], entry['provider'], entry['model']),
        ).fetchone() is None:
            raise BoardError('NOT_FOUND', 'Unknown model service')
        # A model service setting is independent of profile availability: it survives
        # discovery marking a model unavailable and applies again when the model service
        # returns. Only the named model services are touched; unrelated settings and
        # fields keep their stored values.
        connection.execute(
            'INSERT INTO model_service_concurrency(adapter,provider,model,concurrency_limit,created_at,updated_at)'
            ' VALUES(?,?,?,?,?,?)'
            ' ON CONFLICT(adapter,provider,model) DO UPDATE SET concurrency_limit=excluded.concurrency_limit,'
            ' updated_at=excluded.updated_at',
            (
                entry['adapter'],
                entry['provider'],
                entry['model'],
                entry['limit'],
                now,
                now,
            ),
        )
    if 'configuration' in provided:
        configuration = evaluation._validate_configuration(params['configuration'])
        from ..routing import router
        current = router.configuration(connection)
        merged = {**current, **configuration}
        if 'routerProfileIds' in configuration:
            # Every listed ID must reference a published buddy with a complete
            # identity. Enabled, availability, harness health, quota and mode
            # eligibility are deliberately not save requirements: the resolver
            # explains them per item, and a temporarily unhealthy first item never
            # blocks saving the whole list.
            for profile_id in merged['routerProfileIds']:
                row = connection.execute('SELECT * FROM evaluation_profiles WHERE profile_id=?',
                                         (profile_id,)).fetchone()
                if row is None:
                    raise BoardError('NOT_FOUND', 'Unknown profileId', profileId=profile_id)
                if any(not row[key] for key in ('adapter', 'provider', 'model', 'effort')):
                    raise BoardError('INVALID_ARGUMENT',
                                     'routerProfileIds entries must reference published buddies with complete '
                                     'harness/provider/model/effort identities', profileId=profile_id)
        router._write_settings(connection, configuration)
        if 'routingBudget' in configuration:
            evaluation.board._append_event(connection, 'evaluation.routing_budget_changed',
                                           payload={'preset': configuration['routingBudget']})
    counts = {
        'profileSettings': len(settings),
        'preferenceChanges': len(preferences),
        'modelServiceNoteChanges': len(annotations),
        'modelServiceConcurrency': len(model_limits),
        'provided': sorted(provided),
    }
    connection.execute('UPDATE evaluation_state SET updated_at=? WHERE id=1', (now,))
    # ``enabledModelServices`` is an internal caller value, never a published response
    # field: the facts refresh runs after the publication transaction commits and is
    # not part of the idempotent receipt.
    return {'counts': counts,
            'enabledModelServices': enabled_model_services}
