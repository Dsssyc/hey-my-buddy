"""The published package's installation-only command.

Daily commands use the launcher in the materialized shared skill and stable runtime.
"""
from __future__ import annotations

import io
import json
import sys


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n")
    args = list(sys.argv[1:] if argv is None else argv)
    from .launcher import bootstrap_contract_version
    if args != ["install"]:
        print(json.dumps({"contractVersion": bootstrap_contract_version(), "error": {"code": "PACKAGE_INSTALL_USAGE",
                                    "message": "Run hey-my-buddy install"}}))
        return 2
    from .launcher import main as launch

    return launch(["install"])


if __name__ == "__main__":
    raise SystemExit(main())
