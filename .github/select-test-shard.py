"""Select this Python check shard's test files from the measured durations.

Reads .github/check-durations.json and assigns every file under tests/python:
in descending duration, each file goes to the shard with the smallest running
total, so the same table and file list always produce the same split. A file
missing from the table counts as that system's median duration. With no files
for this shard the script fails; it never falls back to running the whole
suite.
"""

import json
import os
import statistics
from pathlib import Path


def main() -> None:
    system = os.environ["CHECK_OS"]
    shard = int(os.environ["CHECK_SHARD"])
    shard_count = int(os.environ["CHECK_SHARDS"])
    table = json.loads(Path(".github/check-durations.json").read_text(encoding="utf-8"))
    if system not in table:
        raise SystemExit(f".github/check-durations.json has no durations for {system}")
    measured = table[system]
    median_duration = statistics.median(measured.values())
    files = sorted(path.as_posix() for path in Path("tests/python").rglob("test_*.py") if path.is_file())

    def duration(path: str) -> float:
        module = ".".join(Path(path).relative_to("tests/python").with_suffix("").parts)
        return measured.get(module, median_duration)

    estimated = {path: duration(path) for path in files}
    totals = [0.0] * shard_count
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for path in sorted(files, key=lambda path: (-estimated[path], path)):
        target = min(range(shard_count), key=lambda index: (totals[index], index))
        shards[target].append(path)
        totals[target] += estimated[path]
    selected = shards[shard - 1]
    if not selected:
        raise SystemExit("The shard has no test files; refusing to run the whole suite.")
    manifest = {"system": system, "shard": shard, "shards": shard_count, "total": len(files),
                "estimated-seconds": round(totals[shard - 1], 1), "files": selected}
    logs = Path(os.environ["RUNNER_TEMP"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "python-shard.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
            output.write(f"HMB_CHECKS_JOBS={os.cpu_count()}\n")
    print(f"{system}: shard {shard}/{shard_count}, {len(selected)}/{len(files)} files, "
          f"estimated {totals[shard - 1]:.1f}s of measured file time, {os.cpu_count()} workers")


if __name__ == "__main__":
    main()
