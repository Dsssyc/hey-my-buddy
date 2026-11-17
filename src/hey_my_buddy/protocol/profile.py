"""Content-addressed material for one model profile run, separate from maintenance."""
from functools import partial
from . import maintenance

publish = partial(maintenance.publish, namespace="profiles")
read = partial(maintenance.read, namespace="profiles")
discard = partial(maintenance.discard, namespace="profiles")
discard_reference = partial(maintenance.discard_reference, namespace="profiles")
discard_orphans = partial(maintenance.discard_orphans, namespace="profiles")
