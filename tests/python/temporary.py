"""Temporary test trees use the runner's guarded filesystem deletion boundary."""
import tempfile
from pathlib import Path

class TemporaryDirectory(tempfile.TemporaryDirectory):
    @classmethod
    def _rmtree(cls, name, ignore_errors=False, repeated=False):
        from hey_my_buddy.cli.checks import remove_private_test_root

        # Keep ordinary paths as fixture evidence; extend them only at deletion.
        # Deletion failures always propagate, including sharing violations.
        remove_private_test_root(Path(name))
