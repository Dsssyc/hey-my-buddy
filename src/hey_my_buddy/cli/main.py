"""One JSON-object CLI for the Host loop, user operations, and scoped roles."""
from __future__ import annotations

import argparse
import json
import sys

from ..errors import BoardError
from ..install.launcher import agent_credential_document, credential_state_dir, take_agent_credential_document
from ..json_codec import decode_strict_json
from ..protocol import transport
from ..protocol.requests import (HOST_REQUESTS, OPERATIONS_REQUESTS, ROLE_REQUESTS,
                                 dump_request, parse_output, parse_request)
from ..protocol.transport import _call_service
from ..state_root import get_state_dir
from ..text import configure_utf8_output
from . import cli_help, cli_views

METHODS = tuple(dict.fromkeys((*HOST_REQUESTS, *OPERATIONS_REQUESTS, *ROLE_REQUESTS)))
ROLE_ONLY = frozenset(ROLE_REQUESTS) - frozenset(HOST_REQUESTS)
ROLE_OPERATIONS = frozenset({"health", "harnesses"})
MAX_PARAMS_BYTES = transport.MAX_MESSAGE_BYTES
_NO_CREDENTIAL_DOCUMENT = object()


def _agent_credential(document=_NO_CREDENTIAL_DOCUMENT) -> str | None:
    if document is _NO_CREDENTIAL_DOCUMENT:
        document = agent_credential_document()
    return document["token"].strip() if document is not None else None


def _dumps(value) -> str:
    from ..protocol.contracts import CONTRACT_VERSION
    if isinstance(value, dict):
        value = {"contractVersion": CONTRACT_VERSION, **value}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _usage_error(code: str, message: str, **details) -> None:
    print(_dumps({"error": {"code": code, "message": message, **details}}))
    raise SystemExit(2)


def _read_params_source(source: str) -> str:
    label = "standard input" if source == "-" else f"params file {source!r}"
    try:
        if source == "-":
            raw = sys.stdin.buffer.read(MAX_PARAMS_BYTES + 1)
        else:
            with open(source, "rb") as handle:
                raw = handle.read(MAX_PARAMS_BYTES + 1)
    except OSError as error:
        raise BoardError("INVALID_ARGUMENT", f"The {label} could not be read: {error}") from error
    if len(raw) > MAX_PARAMS_BYTES:
        raise BoardError("INVALID_ARGUMENT", f"The {label} exceeds {MAX_PARAMS_BYTES} bytes")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise BoardError("INVALID_ARGUMENT", f"The {label} is not valid UTF-8") from error


def _parameters_text(args) -> str:
    if args.params_file is not None and args.params is not None:
        _usage_error("INVALID_ARGUMENT", "Pass the JSON object as an argument or --params-file, not both")
    if args.params_file is not None:
        return _read_params_source(args.params_file)
    if args.params is not None:
        return _read_params_source("-") if args.params == "-" else args.params
    return "{}"


def _parse_parameters(text: str) -> dict:
    try:
        value = decode_strict_json(text)
    except ValueError as error:
        raise BoardError("INVALID_ARGUMENT", f"The method parameters are not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise BoardError("INVALID_ARGUMENT", "params must be a JSON object")
    return value


def _help_command(arguments: list[str]) -> int:
    try:
        print(cli_help.render(arguments))
        return 0
    except BoardError as error:
        _usage_error("INVALID_ARGUMENT", error.message, **error.details)


def _audience(method: str, credential: str | None) -> str:
    if credential is None:
        if method in ROLE_ONLY:
            raise BoardError("FORBIDDEN", f"{method} requires an execution credential")
        return "operations" if method in OPERATIONS_REQUESTS else "host"
    if method in OPERATIONS_REQUESTS:
        if method not in ROLE_OPERATIONS:
            raise BoardError("FORBIDDEN", f"An execution credential cannot call {method}")
        return "operations"
    if method not in ROLE_REQUESTS:
        raise BoardError("FORBIDDEN", f"An execution credential cannot call {method}")
    return "role"


def main(argv: list[str] | None = None, *, credential_document=_NO_CREDENTIAL_DOCUMENT) -> int:
    configure_utf8_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["help"]:
        return _help_command(argv[1:])
    if argv and not argv[0].startswith("-") and argv[0] not in METHODS:
        _usage_error("UNKNOWN_METHOD", f"Unknown method {argv[0]!r}",
                     didYouMean=cli_help.nearest_methods(argv[0], METHODS))
    parser = argparse.ArgumentParser(description="hey-my-buddy: Host delegation and user operations")
    parser.add_argument("method", metavar="METHOD", help="run `hey-my-buddy help` for Host commands")
    parser.add_argument("params", nargs="?", help='one JSON object or "-" for standard input')
    parser.add_argument("--params-file", metavar="PATH", help='read one JSON object from PATH or "-"')
    args = parser.parse_args(argv)
    try:
        params = _parse_parameters(_parameters_text(args))
        if credential_document is _NO_CREDENTIAL_DOCUMENT:
            credential_document = take_agent_credential_document()
        credential = _agent_credential(credential_document)
        audience = _audience(args.method, credential)
        state_dir = credential_state_dir(credential_document) or get_state_dir()
        if args.method == "console":
            from . import console_cli
            result = console_cli.run(params, state_dir=state_dir, credential=credential)
            mode = "full"
        else:
            params, mode = parse_output(params)
            parsed = parse_request(args.method, params, audience=audience)
            wire = dump_request(parsed)
            if args.method == "install":
                from ..install.skill_install import install
                result = install(wire)
            elif args.method == "paths":
                from ..install.skill_install import paths
                result = paths(wire)
            elif args.method == "await":
                from .blocking import WaitAbandoned, await_run
                def wait_service(operation, parameters, directory):
                    request = {**parameters, **({"credential": credential} if credential is not None else {})}
                    return _call_service(operation, request, directory)
                try:
                    result = await_run(parsed, state_dir=state_dir, service=wait_service, scoped=credential is not None)
                except WaitAbandoned as abandoned:
                    result = {"error": {"code": "WAIT_ABANDONED", "message": str(abandoned)},
                              "runId": abandoned.run_id, "actions": ["get", "await"]}
            else:
                if credential is not None:
                    wire["credential"] = credential
                result = _call_service(args.method, wire, state_dir)
        print(_dumps(cli_views.render(args.method, result, mode)))
        return 1 if isinstance(result, dict) and result.get("error") else 0
    except Exception as error:  # noqa: BLE001 - one structured CLI error envelope
        payload = error.payload() if isinstance(error, BoardError) else {"code": "SERVICE_ERROR", "message": str(error)}
        print(_dumps({"error": payload}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
