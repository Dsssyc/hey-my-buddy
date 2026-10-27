"""Regenerate .github/check-durations.json from the logs of one Checks run.

Usage: python .github/generate-check-durations.py <artifacts-directory> [--output <file>]

<artifacts-directory> holds the artifacts of one Checks run as `gh run
download` unpacks them: check-logs-<system>-shard-<n>/python-check.log for the
Python job's matrix systems. Each test file appears in exactly one shard's log
per system; a file whose run never finished leaves no line and is not recorded.
"""

import argparse
import json
import re
from pathlib import Path

# The Python job's matrix in .github/workflows/checks.yml; the artifacts of the
# other jobs (frontend, browser) do not carry python-check.log.
SYSTEMS = ("ubuntu-latest", "windows-latest", "macos-latest")

LINE = re.compile(r"^\[\d+/\d+\] ([\w.]+) (?:ok|FAILED \(exit -?\d+\)) ([\d.]+)s$")


def parse_log(log: Path, durations: dict[str, float]) -> None:
    text = re.sub(r"\x1b\[[0-9;]*m", "", log.read_text(encoding="utf-8", errors="replace"))
    parsed = 0
    for line in text.splitlines():
        match = LINE.match(line)
        if match is None:
            continue
        module, seconds = match.group(1), float(match.group(2))
        if module in durations:
            raise SystemExit(f"{log}: {module} appears twice; each test file runs in one shard per system")
        durations[module] = seconds
        parsed += 1
    if not parsed:
        raise SystemExit(f"{log}: no per-file progress lines; not a Python check log")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("artifacts", type=Path,
                        help="directory holding check-logs-<system>-shard-<n>/python-check.log")
    parser.add_argument("--output", type=Path, default=Path(".github/check-durations.json"))
    arguments = parser.parse_args()
    table: dict[str, dict[str, float]] = {}
    for system in SYSTEMS:
        logs = sorted(arguments.artifacts.glob(f"check-logs-{system}-shard-*/python-check.log"))
        shard_dirs = sorted(path for path in arguments.artifacts.glob(f"check-logs-{system}-shard-*") if path.is_dir())
        if len(logs) != len(shard_dirs) or not logs:
            missing = [str(path) for path in shard_dirs if not (path / "python-check.log").is_file()]
            raise SystemExit(f"{arguments.artifacts}: incomplete {system} logs; "
                             f"{len(logs)} python-check.log files for {len(shard_dirs)} shard directories; missing: {missing}")
        durations: dict[str, float] = {}
        for log in logs:
            parse_log(log, durations)
        table[system] = durations
    arguments.output.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = ", ".join(f"{system}: {len(durations)} files, {sum(durations.values()):.1f}s"
                        for system, durations in table.items())
    print(f"{arguments.output}: {summary}")


if __name__ == "__main__":
    main()
