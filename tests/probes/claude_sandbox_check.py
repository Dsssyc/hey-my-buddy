"""Copied into a disposable fixture; invoked once by Claude's sandboxed Bash."""
from __future__ import annotations

import errno
import json
from pathlib import Path
import subprocess


def head(url: str) -> dict:
    try:
        response = subprocess.run([
            "curl", "-q", "--head", "--silent", "--show-error", "--max-time", "6",
            "--output", "/dev/null", "--write-out", "%{http_code}|%{http_connect}|%{ssl_verify_result}", url,
        ], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {"observed": False}
    try:
        status, connect, tls = map(int, response.stdout.strip().split("|"))
    except ValueError:
        return {"observed": False}
    # No raw proxy diagnostics, account identifiers or headers leave this probe.
    return {"observed": True, "exitCode": response.returncode, "httpStatus": status,
            "connectStatus": connect, "tlsVerify": tls}


def main() -> None:
    cwd = Path.cwd().resolve()
    assert Path(__file__).resolve().parent == cwd
    assert cwd.name == "checkout" and cwd.parents[1].name == "workspaces" and cwd.parents[2].name == "state"
    config = json.loads((cwd / "sandbox-input.json").read_text())
    outside = Path(config["outside"]).resolve(strict=True)
    assert outside == cwd.parents[3] / "outside-sentinel.txt", "Only the Host-owned fixture sentinel may be targeted"
    assert outside.read_text() == "original\n"
    output = cwd / "probe-observations.json"
    assert not output.exists(), "The probe may not repeat network or write attempts"
    report = {"nonce": config["nonce"], "insideWritten": False, "outsideDenied": False}
    (cwd / "probe-output.txt").write_text("inside\n")
    report["insideWritten"] = (cwd / "probe-output.txt").read_text() == "inside\n"
    try:
        outside.write_text("forbidden\n")
    except OSError as error:
        report["outsideDenied"] = error.errno in (errno.EACCES, errno.EPERM, errno.EROFS)
        report["outsideErrno"] = error.errno
    report["registry"] = head("https://registry.npmjs.org/")
    report["nonRegistry"] = head("https://example.com/")
    output.write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
