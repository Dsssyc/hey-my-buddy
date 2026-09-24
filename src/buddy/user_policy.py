"""User-owned field patches; never a source of model or catalog facts."""
from __future__ import annotations

import json

from . import schemas
from .db import canonical_json
from .errors import BoardError

PATCH_FIELDS = frozenset({'profileSettings', 'preferenceChanges', 'annotationChanges', 'configuration'})
GRANT_FIELDS = ('writerId', 'generation', 'writerToken', 'expectedRevision')


def require_writer_kind(kind: str) -> None:
    # Authentication has already established this context. No caller-provided role
    # or consoleAuthority flag can create a console session here.
    from .workflow import current_scope, CONSOLE_KIND, SERVICE_KIND
    scope = current_scope() or {'kind': SERVICE_KIND}
    required = CONSOLE_KIND if kind == 'human' else SERVICE_KIND
    if scope.get('kind') != required:
        raise BoardError('FORBIDDEN', 'User policy requires the authenticated console; assessments require a maintenance Host')


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
    annotations = _changes(params, 'annotationChanges', {'profileId', 'text'})
    profiles = {}
    for entry in settings + preferences + annotations:
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
    for entry in preferences:
        if 'mode' not in entry or entry['mode'] not in (None, 'pin', 'prefer', 'exclude'):
            raise BoardError('INVALID_ARGUMENT', 'mode must be pin, prefer, exclude or null')
        profile_id, mode = entry['profileId'], entry['mode']
        reason = entry.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 500:
            raise BoardError('INVALID_ARGUMENT', 'reason must be a string of at most 500 characters')
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
            connection.execute('DELETE FROM evaluation_annotations WHERE profile_id=?', (entry['profileId'],))
        else:
            connection.execute('INSERT INTO evaluation_annotations(profile_id,text,revision,updated_at) VALUES(?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at', (entry['profileId'], text, revision, now))
    configuration_revision = int(evaluation._state(connection)['configuration_revision'])
    if 'configuration' in provided:
        configuration = evaluation._validate_configuration(params['configuration'])
        profile_id = configuration['decisionProfileId']
        if profile_id is not None:
            profile = connection.execute('SELECT * FROM evaluation_profiles WHERE profile_id=?', (profile_id,)).fetchone()
            if profile is None or not profile['available'] or not profile['enabled']:
                raise BoardError('CONFIGURATION_UNAVAILABLE', 'A new selector must be available and enabled')
            if 'decision' not in json.loads(profile['capabilities_json']):
                raise BoardError('UNSUPPORTED', 'This configuration has no verified decision capability')
        configuration_revision += 1
        connection.execute('UPDATE evaluation_state SET decision_profile_id=?,configuration_revision=? WHERE id=1', (profile_id, configuration_revision))
    counts = {'profileSettings': len(settings), 'preferenceChanges': len(preferences), 'annotationChanges': len(annotations), 'provided': sorted(provided)}
    connection.execute('INSERT INTO evaluation_revisions(revision,kind,writer_id,actor,counts_json,created_at) VALUES(?,?,?,?,?,?)', (revision, 'human', writer['writer_id'], writer['writer_id'], canonical_json(counts), now))
    connection.execute('UPDATE evaluation_state SET table_revision=?,updated_at=? WHERE id=1', (revision, now))
    return {'revision': revision, 'configurationRevision': configuration_revision, 'counts': counts}
