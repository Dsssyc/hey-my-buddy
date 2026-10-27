"""The execution identity carried across the board, holder and controller."""
from typing import Optional

from .internal_models import Hex64, Identifier, InternalModel, NonNegativeInt, OptionalText


class RunIdentity(InternalModel):
    """The frozen execution identity shared by a request and its result."""

    task_id: Identifier
    attempt_id: Identifier
    generation: NonNegativeInt
    invocation_id: Identifier
    turn_id: OptionalText(128) = None
    input_sha256: Optional[Hex64] = None
