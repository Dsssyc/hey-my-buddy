"""Select this Python check shard's test files from the measured durations.

Reads .github/check-durations.json and assigns every file under tests/python:
in descending duration, each file goes to the shard with the earliest predicted
finish, accounting for parallel files in the runner's module order, planned
queue delays and the workflow's runner slowdown allowance. A file
missing from the table counts as that system's median duration. With no files
for this shard the script fails; it never falls back to running the whole
suite. CHECK_WORKERS comes from the workflow, so local CPU observations cannot
make different jobs allocate different copies of the same shard set.
"""

import heapq
import json
import os
import statistics
from pathlib import Path


def execution_seconds(files: list[str], estimated: dict[str, float], workers: int) -> float:
    """The runner submits modules alphabetically to its file worker pool."""
    available = [0.0] * workers
    for path in sorted(files, key=lambda path: path.removesuffix(".py").replace("/", ".")):
        heapq.heappush(available, heapq.heappop(available) + estimated[path])
    return max(available)


def main() -> None:
    system = os.environ["CHECK_OS"]
    shard = int(os.environ["CHECK_SHARD"])
    shard_count = int(os.environ["CHECK_SHARDS"])
    workers = int(os.environ["CHECK_WORKERS"])
    delays_json = os.environ.get("CHECK_START_DELAYS")
    start_delays = json.loads(delays_json) if delays_json else [0] * shard_count
    slowdown = float(os.environ.get("CHECK_SLOWDOWN", "1"))
    if (not 1 <= shard <= shard_count or workers < 1 or slowdown <= 0
            or len(start_delays) != shard_count or any(delay < 0 for delay in start_delays)):
        raise SystemExit("Invalid shard, worker count, slowdown or planned start delays.")
    table = json.loads(Path(".github/check-durations.json").read_text(encoding="utf-8"))
    if system not in table:
        raise SystemExit(f".github/check-durations.json has no durations for {system}")
    measured = table[system]
    median_duration = statistics.median(measured.values())
    files = sorted(path.as_posix() for path in Path("tests/python").rglob("test_*.py") if path.is_file())
    modules = {path: ".".join(Path(path).relative_to("tests/python").with_suffix("").parts) for path in files}
    separate = set(json.loads(os.environ.get("CHECK_SEPARATE_MODULES", "[]")))
    if len(separate) > shard_count or not separate <= set(modules.values()):
        raise SystemExit("Separated modules must exist and fit on distinct shards.")
    estimated = {path: measured.get(modules[path], median_duration) for path in files}
    totals = [0.0] * shard_count
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    occupied: set[int] = set()
    for path in sorted(files, key=lambda path: (-estimated[path], path)):
        targets = [index for index in range(shard_count) if modules[path] not in separate or index not in occupied]
        target = min(targets, key=lambda index: (
            start_delays[index] + slowdown * execution_seconds([*shards[index], path], estimated, workers),
            totals[index], index))
        shards[target].append(path)
        totals[target] += estimated[path]
        if modules[path] in separate:
            occupied.add(target)
    selected = shards[shard - 1]
    if not selected:
        raise SystemExit("The shard has no test files; refusing to run the whole suite.")
    manifest = {"system": system, "shard": shard, "shards": shard_count, "total": len(files),
                "estimated-seconds": round(totals[shard - 1], 1),
                "estimated-wall-seconds": round(execution_seconds(selected, estimated, workers), 1),
                "planned-delay-seconds": start_delays[shard - 1],
                "runner-slowdown": slowdown,
                "separated-modules": sorted(separate),
                "estimated-finish-seconds": round(start_delays[shard - 1]
                    + slowdown * execution_seconds(selected, estimated, workers), 1),
                "workers": workers, "files": selected}
    logs = Path(os.environ["RUNNER_TEMP"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "python-shard.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
            output.write(f"HMB_CHECKS_JOBS={workers}\n")
    print(f"{system}: shard {shard}/{shard_count}, {len(selected)}/{len(files)} files, "
          f"estimated {totals[shard - 1]:.1f}s of measured file time, "
          f"{manifest['estimated-wall-seconds']:.1f}s parallel execution, {workers} workers; "
          f"planned queue {start_delays[shard - 1]}s, slowdown {slowdown:g}, "
          f"finish {manifest['estimated-finish-seconds']:.1f}s excluding job setup")


if __name__ == "__main__":
    main()
