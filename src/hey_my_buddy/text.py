"""Shared UTF-8 output and byte-bounded text operations."""
import io
import sys


def configure_utf8_output() -> None:
    """Configure byte-backed output; caller-supplied text buffers need no encoding."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n")


def truncate_utf8(value: str, limit: int, *, errors: str = "strict") -> str:
    return value.encode("utf-8", errors=errors)[:limit].decode("utf-8", errors="ignore")
