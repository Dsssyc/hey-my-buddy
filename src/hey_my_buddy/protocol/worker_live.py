"""Private holder registration frames; these are not public CLI requests.

The board receives only the Worker's endpoint and its own narrow capability.
Controller addresses and capabilities never belong in these frames.
"""
from .internal_models import Hex64, Identifier, InternalModel, NonNegativeInt, Text
from .run_identity import RunIdentity
from pydantic import Field


class WorkerLiveActor(InternalModel):
    worker_id: Identifier
    attempt_id: Identifier
    generation: NonNegativeInt
    nonce: Text(256) = Field(repr=False)
    worker_instance: Text(128)


class WorkerLiveAttach(WorkerLiveActor):
    identity: RunIdentity
    address: Text(256)
    name: Text(128)
    instance_id: Hex64
    live_token: Hex64 = Field(repr=False)


class WorkerLiveDetach(WorkerLiveActor):
    identity: RunIdentity
    instance_id: Hex64
