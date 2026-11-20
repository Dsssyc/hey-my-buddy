"""Keep Router failure copy in sync with codes produced by the service and harnesses.

Only source files are parsed: this check neither opens a board nor starts a native
harness. The native constructor set is a conservative bound over the currently
shared harness modules; some constructors belong to Worker or discovery paths.
"""
from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "src/hey_my_buddy/buddy/harnesses"
ROLES = ROOT / "src/hey_my_buddy/buddy/roles"
ROUTING = ROOT / "src/hey_my_buddy/blackboard/routing"
ERROR_CONSTRUCTORS = frozenset({
    "NativeError", "NativeRpcError", "CodexProtocolError", "ClaudeProtocolError", "ReceiptError",
})
BOARD_CONSTRUCTORS = frozenset({"make_reason", "reason", "unavailable", "refuse", "BoardError"})
# These native codes are translated before the service stores a Router result.
# buddy/roles/router.py maps them at lines 275-279.
TRANSLATED = frozenset({"no-tool-violation", "readonly-policy-unverified"})
# A successful direct selection is a decision reason, not failure copy.
NON_FAILURE = frozenset({"single-candidate"})
ENTRY = re.compile(r'^\s*"([^"\\]+)":\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$', re.MULTILINE)


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _strings(node: ast.AST) -> set[str]:
    """Literal branches in a code expression, excluding unrelated dynamic values."""
    if value := _string(node):
        return {value}
    if isinstance(node, ast.IfExp):
        return _strings(node.body) | _strings(node.orelse)
    return set()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_codes(path: Path, *, board: bool = False, result_dicts: bool = False) -> set[str]:
    codes: set[str] = set()
    constructors = ERROR_CONSTRUCTORS | (BOARD_CONSTRUCTORS if board else frozenset())
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in constructors and node.args:
                codes.update(_strings(node.args[0]))
            if isinstance(node.func, ast.Name) and node.func.id == "RunEnd":
                codes.update(_strings(next((kw.value for kw in node.keywords if kw.arg == "reason_code"), node)))
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"update", "finish"}:
                codes.update(_strings(next((kw.value for kw in node.keywords if kw.arg == "code"), node)))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "reason":
                    codes.update(_strings(node.value))
                elif isinstance(target, ast.Name) and target.id == "code":
                    codes.update(_strings(node.value))
                elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for item, value in zip(target.elts, node.value.elts):
                        if isinstance(item, ast.Attribute) and item.attr == "reason":
                            codes.update(_strings(value))
        if (board or result_dicts) and isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is not None and _string(key) == "code":
                    codes.update(_strings(value))
    # Upper-case BoardError protocol codes and parameter labels are not reason codes.
    return {code for code in codes if code and code[0].islower() and code not in TRANSLATED | NON_FAILURE}


def _assigned(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return node.value
    raise AssertionError(f"Producer constant {name} was not found")


def _dynamic_native_codes() -> set[str]:
    dsh = _tree(NATIVE / "dsh/native_run.py")
    stop = _assigned(dsh, "_END_REASONS")
    assert isinstance(stop, ast.Dict)
    values = {_string(value) for value in stop.values}
    codes = {value for value in values if value}
    codes |= _strings(_assigned(dsh, "UNREAD_STOP_REASON"))
    # Unknown native stop reasons are intentionally normalized to this one code.
    if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "get" and len(node.args) > 1 and _string(node.args[1]) == "native-stop"
               for node in ast.walk(dsh)):
        raise AssertionError("DSH native-stop normalization changed")
    codes.add("native-stop")
    inventory = _tree(NATIVE / "dsh/tool_inventory.py")
    codes |= _strings(_assigned(inventory, "UNREADABLE"))
    codes |= _strings(_assigned(inventory, "UNRECOGNIZED"))
    owned = _tree(ROOT / "src/hey_my_buddy/protocol/owned_process.py")
    launch_errors = {"WindowsCommandLineTooLong", "WorkingDirectoryRejected"}
    launch_error_classes = {node.name: node for node in owned.body
                            if isinstance(node, ast.ClassDef) and node.name in launch_errors}
    for name in launch_errors:
        codes |= _strings(_assigned(ast.Module(body=launch_error_classes[name].body, type_ignores=[]), "code"))
    return codes


def _producer_codes() -> set[str]:
    codes = set()
    for path in NATIVE.rglob("*.py"):
        codes |= _literal_codes(path)
    for path in ROLES.glob("*.py"):
        codes |= _literal_codes(path, result_dicts=True)
    for name in ("decision.py", "router.py", "maintenance.py", "profile.py"):
        codes |= _literal_codes(ROUTING / name, board=True)
    runtime = ROOT / "src/hey_my_buddy/buddy/runtime/execution.py"
    codes |= _literal_codes(runtime, result_dicts=True)
    codes |= _dynamic_native_codes()
    registry = ROOT / "src/hey_my_buddy/protocol/reason-codes.json"
    codes |= {code for code in json.loads(registry.read_text(encoding="utf-8"))
              if code.startswith(("router-", "profile-", "maintenance-", "routing-"))}
    return codes


def _copy(language: str) -> dict[str, str]:
    path = ROOT / f"apps/console/src/i18n/{language}/router-errors.ts"
    return dict(ENTRY.findall(path.read_text(encoding="utf-8")))


class RouterFailureCopyTests(unittest.TestCase):
    def test_current_failure_producers_have_bilingual_short_copy(self):
        produced = _producer_codes()
        self.assertIn("maintenance-answer-invalid", produced)
        self.assertIn("profile-answer-invalid", produced)
        self.assertIn("native-stop", produced)
        self.assertIn("harness.command-line-too-long", produced)
        self.assertIn("harness.working-directory-rejected", produced)
        for language in ("zh", "en"):
            with self.subTest(language=language):
                copy = _copy(language)
                missing = sorted(code for code in produced if not copy.get(code, "").strip())
                self.assertEqual(missing, [], f"Missing {language} Router failure copy: {missing}")
                unhelpful = sorted(code for code in produced if copy.get(code) == code)
                self.assertEqual(unhelpful, [], f"Untranslated {language} Router failure copy: {unhelpful}")
                parameterized = sorted(code for code in produced if "{{" in copy.get(code, ""))
                self.assertEqual(parameterized, [], f"Parameterized {language} Router failure copy: {parameterized}")
