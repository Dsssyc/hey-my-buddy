"""Select this Python check shard's test files from the measured durations.

Reads .github/check-durations.json and assigns every file under tests/python:
in descending duration, each file goes to the shard with the least queued work,
accounting for planned queue delays and the workflow's slow-runner scenario.
Every file adds its full weight even when other files could run beside it:
parallel free slots must not concentrate heavy files on one runner. The check
runner preserves this descending file order inside each shard. A file
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
    """The runner submits files in the explicit manifest order."""
    available = [0.0] * workers
    for path in files:
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
    estimated = {path: measured.get(modules[path], median_duration) for path in files}
    totals = [0.0] * shard_count
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for path in sorted(files, key=lambda path: (-estimated[path], path)):
        target = min(range(shard_count), key=lambda index: (
            start_delays[index] + slowdown * (totals[index] + estimated[path]) / workers,
            totals[index], index))
        shards[target].append(path)
        totals[target] += estimated[path]
    selected = shards[shard - 1]
    if not selected:
        raise SystemExit("The shard has no test files; refusing to run the whole suite.")
    manifest = {"system": system, "shard": shard, "shards": shard_count, "total": len(files),
                "estimated-seconds": round(totals[shard - 1], 1),
                "estimated-wall-seconds": round(execution_seconds(selected, estimated, workers), 1),
                "planned-delay-seconds": start_delays[shard - 1],
                "runner-slowdown": slowdown,
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
