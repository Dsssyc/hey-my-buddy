"""Test-only process boundary: native harnesses must come from test materials.

This is loaded in checks children, including Python role controllers, through
the tests' sitecustomize. It has no product import and no real-harness opt-in.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import sys

HARNESS_NAMES = {"dsh", "codex", "claude", "zcode"}
PACKAGE_NAMES = HARNESS_NAMES | {f"{name}.app" for name in HARNESS_NAMES} | {"claude-code", "deepseek-harness"}


def _name(path: str) -> str:
    return Path(path).name.lower().removesuffix(".exe").removesuffix(".cmd").removesuffix(".bat").removesuffix(".cjs").removesuffix(".mjs").removesuffix(".js")


def check_launch(executable, argv, cwd, environment, *, private_root: Path, checkout: Path) -> None:
    """Refuse installed native CLIs before a process exists; allow fake files."""
    environment = os.environ if environment is None else environment
    executable = os.fsdecode(executable)
    arguments = shlex.split(argv) if isinstance(argv, str) else [os.fsdecode(value) for value in argv]
    candidates = []
    base = Path(cwd) if cwd else Path.cwd()
    resolved_executable = Path(shutil.which(executable, path=environment.get("PATH", "")) or executable)
    resolved_executable = (resolved_executable if resolved_executable.is_absolute() else base / resolved_executable).resolve()
    name = _name(str(resolved_executable))
    if name in HARNESS_NAMES:
        candidates.append(shutil.which(executable, path=environment.get("PATH", "")) or executable)
    if name in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}:
        for option in ("-c", "/c", "-Command"):
            if option in arguments:
                tokens = shlex.split(arguments[arguments.index(option) + 1])
                head = tokens[1] if tokens and tokens[0] == "exec" else tokens[0] if tokens else ""
                if _name(head) in HARNESS_NAMES:
                    candidates.append(shutil.which(head, path=environment.get("PATH", "")) or head)
                arguments.extend(tokens)
    # Existing native fixtures may wrap node in sandbox-exec or an owned shell
    # script. Inspect only the test's own shell wrapper, never installed source.
    if resolved_executable.is_relative_to(private_root) or resolved_executable.is_relative_to(checkout):
        with resolved_executable.open("rb") as stream:
            header = stream.readline(256)
            if header.startswith((b"#!/bin/sh", b"#!/bin/bash", b"#!/usr/bin/env sh", b"#!/usr/bin/env bash")):
                for line in stream:
                    arguments.extend(shlex.split(line.decode()))
    for argument in arguments[1:]:
        if argument.startswith("-"):
            continue
        path = Path(argument)
        if _name(argument) in HARNESS_NAMES or any(part.lower() in PACKAGE_NAMES for part in path.parts):
            candidates.append(argument)
    for candidate in candidates:
        path = Path(candidate)
        path = (path if path.is_absolute() else base / path).resolve()
        if not path.is_relative_to(private_root) and not path.is_relative_to(checkout):
            raise AssertionError(f"check suite refused an installed harness: {path}; use a fake or tests/probes")


def install(private_root: Path, checkout: Path) -> None:
    private_root, checkout = private_root.resolve(), checkout.resolve()

    def audit(event, arguments):
        if event == "subprocess.Popen":
            check_launch(*arguments, private_root=private_root, checkout=checkout)

    sys.addaudithook(audit)
