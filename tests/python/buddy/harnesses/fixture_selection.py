"""Select a Python protocol fixture without relying on executable scripts."""

import json
import sys
from pathlib import Path


def select_fixture(environment: dict[str, str], directory: Path, harness: str,
                   fixture: Path) -> None:
    """Write the existing selected-command record for a test's private root."""
    select_command(environment, directory, harness, [sys.executable, str(fixture)])


def select_command(environment: dict[str, str], directory: Path, harness: str,
                   command: list[str]) -> None:
    """Select an explicit argv, including deliberately unstartable commands."""
    environment.pop(f"HMB_{harness.upper()}_CLI", None)
    record = Path(environment["HMB_HARNESS_RECORD_FILE"]) if environment.get("HMB_HARNESS_RECORD_FILE") else \
        directory / "harness-record.json"
    records = json.loads(record.read_text()) if record.exists() else {}
    records[harness] = {"harness": harness, "status": "ready", "command": command}
    record.write_text(json.dumps(records))
    environment["HMB_HARNESS_RECORD_FILE"] = str(record)
