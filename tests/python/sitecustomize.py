"""Install the native-process test boundary only inside a checks temp tree.

TMPDIR survives the product's native environment cleaning, so Python role
controllers inherit the same boundary. Explicit probes outside checks trees
are unaffected. There is no environment switch to run real harness tests.
"""
import os
from pathlib import Path

from checks_guard import install

temporary = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp").resolve()
for root in (temporary, *temporary.parents):
    if root.name.startswith("buddy-checks-"):
        install(root, Path(__file__).resolve().parents[2])
        break
