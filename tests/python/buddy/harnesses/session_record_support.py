"""Read the execution-side JSONL display record in harness tests."""
import json
from pathlib import Path


def notifications(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"protocol": "acp", "protocolVersion": 1,
                                     "projectionVersion": 1}
    return [json.loads(line) for line in lines[1:]]


def updates(path: Path) -> list[dict]:
    return [item["update"] for item in notifications(path)]
