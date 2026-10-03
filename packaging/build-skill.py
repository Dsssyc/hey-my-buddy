#!/usr/bin/env python3
"""Build the shared ``buddy`` Agent Skill from this checkout (ADR-015).

    uv run --frozen python packaging/build-skill.py --destination <dir>/buddy

The destination must be a directory named ``buddy``; it is replaced by rename after
the inventory is verified. To install for the current user, run the skill's own
``scripts/buddy install`` (or this checkout's ``skills/buddy/scripts/buddy install``).
"""
import argparse
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / "src"))

from hey_my_buddy.install.skill_package import build  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(build(SOURCE_ROOT, args.destination))


if __name__ == "__main__":
    main()
