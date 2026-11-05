"""A fixed none result references the execution's original private receipt."""
from pathlib import Path

from ..errors import BoardError
from .execution import read_execution_receipt
from .workspace_manifest import _private_path


def result_document(state_dir: Path, row) -> dict:
    relative = Path(row['record_path'])
    path = _private_path(state_dir, relative)
    try:
        receipt = read_execution_receipt(path.parent,
            binding={'taskId': row['run_id'], 'attemptId': row['attempt_id'], 'generation': row['generation']},
            sha256=row['record_sha256'])
    except (ValueError, UnicodeError) as error:
        raise BoardError('ARTIFACT_MISMATCH', 'The fixed result is malformed') from error
    if receipt is None:
        raise BoardError('ARTIFACT_MISSING', 'The fixed result receipt is missing', path=str(path))
    return receipt
