"""User-owned field patches; never a source of model or catalog facts."""
from __future__ import annotations

import json

from . import schemas
from .db import canonical_json
from .errors import BoardError

PATCH_FIELDS = frozenset({'profileSettings', 'preferenceChanges', 'familyPreferenceChanges', 'familyAnnotationChanges',
                          'configuration', 'modelConcurrency'})
FAMILY_FIELDS = ('adapter', 'provider', 'model')
GRANT_FIELDS = ('writerId', 'generation', 'writerToken', 'expectedRevision')

#: The per-family limit a user may set, and the fields one patch entry accepts.
#: ``active`` and every other derived field are rejected: occupancy is observation,
#: never input. There is no effort dimension — effort variants share one family.
MODEL_CONCURRENCY_FIELDS = frozenset({'adapter', 'provider', 'model', 'limit'})


def _model_concurrency_changes(params: dict) -> list[dict]:
    """Validate the ``modelConcurrency`` patch: family tuples and limits only."""
    values = params.get('modelConcurrency', [])
    if not isinstance(values, list) or len(values) > 200:
        raise BoardError('INVALID_ARGUMENT', 'modelConcurrency must contain at most 200 patches')
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', 'modelConcurrency entries must be objects')
        schemas.reject_unknown(value, MODEL_CONCURRENCY_FIELDS, 'modelConcurrency')
        family = tuple(
            schemas.required_string(value, name, max_length=256)
            for name in ('adapter', 'provider', 'model')
        )
        if family in seen:
            raise BoardError('INVALID_ARGUMENT', 'modelConcurrency repeats a model family')
        seen.add(family)
        limit = value.get('limit')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
            raise BoardError('INVALID_ARGUMENT', 'limit must be an integer between 1 and 32')
    return values


def require_writer_kind(kind: str) -> None:
    # Authentication has already established this context. No caller-provided role
    # or consoleAuthority flag can create a console session here.
    from .workflow import current_scope, CONSOLE_KIND, SERVICE_KIND
    scope = current_scope() or {'kind': SERVICE_KIND}
    required = CONSOLE_KIND if kind == 'human' else SERVICE_KIND
    if scope.get('kind') != required:
        raise BoardError('FORBIDDEN', 'User policy requires the authenticated console; assessments require a maintenance Host')


def _family_changes(params: dict, field: str, fields: set[str]) -> list[dict]:
    """Validate a family-keyed patch: one entry per ``(adapter, provider, model)``."""
    values = params.get(field, [])
    if not isinstance(values, list) or len(values) > 200:
        raise BoardError('INVALID_ARGUMENT', f'{field} must contain at most 200 patches')
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            raise BoardError('INVALID_ARGUMENT', f'{field} entries must be objects')
        schemas.reject_unknown(value, fields, field)
        family = tuple(schemas.required_string(value, name, max_length=256) for name in FAMILY_FIELDS)
        if family in seen:
            raise BoardError('INVALID_ARGUMENT', f'{field} repeats a model family')
        seen.add(family)
    return values


def _family(entry: dict) -> tuple[str, str, str]:
    return tuple(entry[name] for name in FAMILY_FIELDS)


def _reason(entry: dict) -> str:
    reason = entry.get('reason', '')
    if not isinstance(reason, str) or len(reason) > 500:
        raise BoardError('INVALID_ARGUMENT', 'reason must be a string of at most 500 characters')
    return reason


def policy_view(connection, profile_ids: list[str]) -> dict:
    """User policy for these profiles and their families, as the console reads it.

    ``preferences`` is the effective policy from the ``effective_preferences`` view;
    ``familyPreferences`` and ``preferenceOverrides`` are what the user stored.
    """
    marks = ','.join('?' for _ in profile_ids) or 'NULL'
    in_families = (f'EXISTS(SELECT 1 FROM evaluation_profiles p WHERE p.adapter=f.adapter AND p.provider=f.provider'
                   f' AND p.model=f.model AND p.profile_id IN ({marks}))')
    return {
        'familyPreferences': [
            {'adapter': row['adapter'], 'provider': row['provider'], 'model': row['model'],
             'mode': row['mode'], 'reason': row['reason']}
            for row in connection.execute(f'SELECT * FROM family_preferences f WHERE {in_families}'
                                          ' ORDER BY f.adapter, f.provider, f.model', profile_ids)],
        'preferenceOverrides': [
            {'profileId': row['profile_id'], 'mode': row['mode'], 'reason': row['reason']}
            for row in connection.execute(f'SELECT * FROM evaluation_preferences WHERE profile_id IN ({marks})'
                                          ' ORDER BY profile_id', profile_ids)],
        'preferences': effective_preferences(connection, profile_ids),
        'familyAnnotations': family_annotations(connection, profile_ids),
    }


def effective_preferences(connection, profile_ids: list[str]) -> list[dict]:
    marks = ','.join('?' for _ in profile_ids) or 'NULL'
    return [{'profileId': row['profile_id'], 'mode': row['mode'], 'reason': row['reason'], 'source': row['source']}
            for row in connection.execute(f'SELECT * FROM effective_preferences WHERE profile_id IN ({marks})'
                                          ' ORDER BY profile_id', profile_ids)]


def family_annotations(connection, profile_ids: list[str]) -> list[dict]:
    marks = ','.join('?' for _ in profile_ids) or 'NULL'
    return [{'adapter': row['adapter'], 'provider': row['provider'], 'model': row['model'], 'text': row['text'],
             'revision': int(row['revision']), 'updatedAt': row['updated_at']}
            for row in connection.execute(
                'SELECT * FROM family_annotations f WHERE EXISTS(SELECT 1 FROM evaluation_profiles p'
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


def publish(evaluation, connection, *, revision: int, writer, now: str, params: dict) -> dict:
    """Apply only requested human fields in the caller's publication transaction."""
    require_writer_kind('human')
    provided = set(params) & PATCH_FIELDS
    if not provided:
        raise BoardError('INVALID_ARGUMENT', 'A user publication must contain a field patch')
    settings = _changes(params, 'profileSettings', {'profileId', 'enabled'})
    preferences = _changes(params, 'preferenceChanges', {'profileId', 'mode', 'reason'})
    family_preferences = _family_changes(params, 'familyPreferenceChanges', {*FAMILY_FIELDS, 'mode', 'reason'})
    annotations = _family_changes(params, 'familyAnnotationChanges', {*FAMILY_FIELDS, 'text'})
    model_limits = _model_concurrency_changes(params)
    profiles = {}
    for entry in settings + preferences:
        profile_id = entry['profileId']
        if profile_id not in profiles:
            row = connection.execute('SELECT * FROM evaluation_profiles WHERE profile_id=?', (profile_id,)).fetchone()
            if row is None:
                raise BoardError('NOT_FOUND', 'Unknown profileId', profileId=profile_id)
            profiles[profile_id] = dict(row)
    for entry in settings:
        if type(entry.get('enabled')) is not bool:
            raise BoardError('INVALID_ARGUMENT', 'enabled must be a boolean')
        profile = profiles[entry['profileId']]
        if entry['enabled'] and not profile['available']:
            raise BoardError('CONFIGURATION_UNAVAILABLE', 'This configuration is not currently available', profileId=entry['profileId'])
        profile['enabled'] = int(entry['enabled'])
        connection.execute('UPDATE evaluation_profiles SET enabled=?, updated_revision=? WHERE profile_id=?', (profile['enabled'], revision, entry['profileId']))
    families = {}
    for entry in family_preferences + annotations:
        family = _family(entry)
        if family not in families:
            members = connection.execute('SELECT available, enabled FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=?', family).fetchall()
            if not members:
                raise BoardError('NOT_FOUND', 'Unknown model family')
            families[family] = members
    for entry in family_preferences:
        if 'mode' not in entry or entry['mode'] not in (None, 'pin', 'prefer', 'exclude'):
            raise BoardError('INVALID_ARGUMENT', 'mode must be pin, prefer, exclude or null')
        family, mode, reason = _family(entry), entry['mode'], _reason(entry)
        previous = connection.execute('SELECT mode FROM family_preferences WHERE adapter=? AND provider=? AND model=?', family).fetchone()
        if mode == 'pin' and (previous is None or previous['mode'] != 'pin') and not any(row['available'] and row['enabled'] for row in families[family]):
            raise BoardError('CONFIGURATION_UNAVAILABLE', 'A new pin requires an available enabled configuration', family=list(family))
        if mode is None:
            connection.execute('DELETE FROM family_preferences WHERE adapter=? AND provider=? AND model=?', family)
        else:
            connection.execute('INSERT INTO family_preferences(adapter,provider,model,mode,reason,updated_revision) VALUES(?,?,?,?,?,?) ON CONFLICT(adapter,provider,model) DO UPDATE SET mode=excluded.mode,reason=excluded.reason,updated_revision=excluded.updated_revision', (*family, mode, reason, revision))
    for entry in preferences:
        if 'mode' not in entry or entry['mode'] not in (None, 'pin', 'prefer', 'exclude', 'none'):
            raise BoardError('INVALID_ARGUMENT', 'mode must be pin, prefer, exclude, none or null')
        profile_id, mode, reason = entry['profileId'], entry['mode'], _reason(entry)
        previous = connection.execute('SELECT mode FROM evaluation_preferences WHERE profile_id=?', (profile_id,)).fetchone()
        if mode == 'pin' and (previous is None or previous['mode'] != 'pin') and not (profiles[profile_id]['available'] and profiles[profile_id]['enabled']):
            raise BoardError('CONFIGURATION_UNAVAILABLE', 'A new pin requires an available enabled configuration', profileId=profile_id)
        if mode is None:
            connection.execute('DELETE FROM evaluation_preferences WHERE profile_id=?', (profile_id,))
        else:
            connection.execute('INSERT INTO evaluation_preferences(profile_id,mode,reason,updated_revision) VALUES(?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET mode=excluded.mode,reason=excluded.reason,updated_revision=excluded.updated_revision', (profile_id, mode, reason, revision))
    for entry in annotations:
        text = entry.get('text')
        if not isinstance(text, str) or len(text) > 4000:
            raise BoardError('INVALID_ARGUMENT', 'text must be a string of at most 4000 characters')
        if not text:
            connection.execute('DELETE FROM family_annotations WHERE adapter=? AND provider=? AND model=?', _family(entry))
        else:
            connection.execute('INSERT INTO family_annotations(adapter,provider,model,text,revision,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(adapter,provider,model) DO UPDATE SET text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at', (*_family(entry), text, revision, now))
    configuration_revision = int(evaluation._state(connection)['configuration_revision'])
    for entry in model_limits:
        if connection.execute(
            'SELECT 1 FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=? LIMIT 1',
            (entry['adapter'], entry['provider'], entry['model']),
        ).fetchone() is None:
            raise BoardError('NOT_FOUND', 'Unknown model family')
        # A family setting is independent of profile availability: it survives
        # discovery marking a model unavailable and applies again when the family
        # returns. Only the named families are touched; unrelated settings and
        # fields keep their stored values.
        connection.execute(
            'INSERT INTO model_concurrency(adapter,provider,model,concurrency_limit,updated_revision,created_at,updated_at)'
            ' VALUES(?,?,?,?,?,?,?)'
            ' ON CONFLICT(adapter,provider,model) DO UPDATE SET concurrency_limit=excluded.concurrency_limit,'
            ' updated_revision=excluded.updated_revision, updated_at=excluded.updated_at',
            (
                entry['adapter'],
                entry['provider'],
                entry['model'],
                entry['limit'],
                revision,
                now,
                now,
            ),
        )
    if 'configuration' in provided:
        configuration = evaluation._validate_configuration(params['configuration'])
        profile_id = configuration.get('decisionProfileId', evaluation._state(connection)['decision_profile_id'])
        if 'decisionProfileId' in configuration and profile_id is not None:
            profile = connection.execute('SELECT * FROM evaluation_profiles WHERE profile_id=?', (profile_id,)).fetchone()
            if profile is None or not profile['available'] or not profile['enabled']:
                raise BoardError('CONFIGURATION_UNAVAILABLE', 'A new selector must be available and enabled')
            from .adapters import adapter
            native = adapter(profile['adapter'])
            if ('decision' not in json.loads(profile['capabilities_json']) or not native.read_only_structured
                    or not native.read_only_structured_verified):
                raise BoardError('UNSUPPORTED', 'This configuration has no verified decision capability')
        if 'routingBudget' in configuration:
            connection.execute("INSERT INTO meta(key,value) VALUES('router_budget_preset',?)"
                               " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (configuration['routingBudget'],))
            evaluation.board._append_event(connection, 'evaluation.routing_budget_changed',
                                           payload={'preset': configuration['routingBudget'], 'revision': revision})
        configuration_revision += 1
        connection.execute('UPDATE evaluation_state SET decision_profile_id=?,configuration_revision=? WHERE id=1', (profile_id, configuration_revision))
    counts = {
        'profileSettings': len(settings),
        'preferenceChanges': len(preferences),
        'familyPreferenceChanges': len(family_preferences),
        'familyAnnotationChanges': len(annotations),
        'modelConcurrency': len(model_limits),
        'provided': sorted(provided),
    }
    connection.execute('INSERT INTO evaluation_revisions(revision,kind,writer_id,actor,counts_json,created_at) VALUES(?,?,?,?,?,?)', (revision, 'human', writer['writer_id'], writer['writer_id'], canonical_json(counts), now))
    connection.execute('UPDATE evaluation_state SET table_revision=?,updated_at=? WHERE id=1', (revision, now))
    return {'revision': revision, 'configurationRevision': configuration_revision, 'counts': counts}
