"""Content-addressed material for one model profile run, separate from maintenance."""
from functools import partial
from . import maintenance

publish = partial(maintenance.publish, namespace="profile")
read = partial(maintenance.read, namespace="profile")
discard = partial(maintenance.discard, namespace="profile")
discard_reference = partial(maintenance.discard_reference, namespace="profile")
discard_orphans = partial(maintenance.discard_orphans, namespace="profile")
