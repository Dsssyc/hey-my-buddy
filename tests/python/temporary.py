"""Temporary test trees use the runner's guarded filesystem deletion boundary."""
import tempfile
from pathlib import Path

class TemporaryDirectory(tempfile.TemporaryDirectory):
    @classmethod
    def _rmtree(cls, name, ignore_errors=False, repeated=False):
        from hey_my_buddy.cli.checks import remove_private_test_root

        # Keep ordinary paths as fixture evidence; extend them only at deletion.
        # Windows sharing violations retry within the boundary's time limit;
        # final failures still propagate and preserve the remaining tree.
        remove_private_test_root(Path(name))
