"""The reason seam: one constructor, one code table, and the static check of both.

The check is deliberately static: the constructor validates nothing at run time,
and this test is the one place that holds every source call to the declared
codes and parameter names. A dynamic code is allowed only through a module-level
mapping whose values are all string literals, and every value of that mapping is
checked.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

from hey_my_buddy.protocol.reasons import reason

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "hey_my_buddy"
CODES_FILE = SOURCE / "protocol" / "reason-codes.json"


def declared_codes(path: Path = CODES_FILE) -> dict[str, list[str]]:
    """The registered code -> parameter names, exactly as the table declares."""
    return json.loads(path.read_text(encoding="utf-8"))


def literal_mappings(module: ast.Module) -> dict[str, tuple[str, ...]]:
    """Module-level mappings whose values are all string literals."""
    mappings: dict[str, tuple[str, ...]] = {}
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        values: list[str] = []
        for key, item in zip(value.keys, value.values):
            if key is None or not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                break
            values.append(item.value)
        else:
            for target in targets:
                if isinstance(target, ast.Name):
                    mappings[target.id] = tuple(values)
    return mappings


def mapping_lookup(node: ast.expr, mappings: dict[str, tuple[str, ...]]) -> tuple[str, ...] | None:
    """The literal code values a mapping lookup can produce, if it is one."""
    if isinstance(node, ast.Subscript):
        source = node.value
        if isinstance(source, ast.Name):
            return mappings.get(source.id)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and len(node.args) == 1
        and not node.keywords
    ):
        return mappings.get(node.func.value.id)
    return None


def check_code(where: str, code: str, parameters: set[str], codes: dict[str, list[str]],
               violations: list[str]) -> None:
    declared = codes.get(code)
    if declared is None:
        violations.append(f"{where}: reason code {code!r} is not registered in reason-codes.json")
        return
    if set(declared) != parameters:
        violations.append(
            f"{where}: reason code {code!r} declares parameters {sorted(declared)}, "
            f"but the call names {sorted(parameters)}"
        )


def check_reason_call(call: ast.Call, mappings: dict[str, tuple[str, ...]], codes: dict[str, list[str]],
                      where: str, violations: list[str]) -> None:
    if not call.args:
        violations.append(f"{where}: reason() needs the code as its first positional argument")
        return
    if len(call.args) != 1:
        violations.append(f"{where}: reason() takes one positional argument (the code)")
        return
    names: list[str] = []
    for keyword in call.keywords:
        if keyword.arg is None:
            violations.append(f"{where}: reason() parameters must be named explicitly, not ** unpacked")
            return
        names.append(keyword.arg)
    parameters = set(names)

    source = call.args[0]
    if isinstance(source, ast.Constant):
        if not isinstance(source.value, str):
            violations.append(f"{where}: a reason code must be a string literal")
            return
        check_code(where, source.value, parameters, codes, violations)
        return

    candidates = mapping_lookup(source, mappings)
    if candidates is None:
        violations.append(
            f"{where}: a dynamic reason code must come from a module-level mapping "
            "whose values are all string literals"
        )
        return
    if not candidates:
        violations.append(f"{where}: the reason-code mapping has no values")
        return
    for code in candidates:
        check_code(where, code, parameters, codes, violations)


def reason_violations(root: Path, codes: dict[str, list[str]]) -> list[str]:
    """Every source call that does not match the declared code table."""
    violations: list[str] = []
    root = Path(root)
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        module = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        mappings = literal_mappings(module)
        constructors = {"reason"}
        for statement in ast.walk(module):
            if isinstance(statement, ast.ImportFrom) and (statement.module or "").endswith("protocol.reasons"):
                constructors.update(alias.asname or alias.name for alias in statement.names if alias.name == "reason")
        for node in ast.walk(module):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in constructors):
                continue
            check_reason_call(node, mappings, codes, f"{relative}:{node.lineno}", violations)
    return violations


class ReasonTests(unittest.TestCase):
    def test_reason_carries_the_code_and_exactly_the_named_parameters(self):
        self.assertEqual(
            reason("router-skip-window", buddyId="buddy-1", retryAt="2026-10-17T00:00:00Z"),
            {
                "code": "router-skip-window",
                "params": {"buddyId": "buddy-1", "retryAt": "2026-10-17T00:00:00Z"},
            },
        )
        self.assertEqual(reason("plain"), {"code": "plain", "params": {}})


class ReasonCodesTableTests(unittest.TestCase):
    def test_the_table_maps_codes_to_unique_parameter_names(self):
        codes = declared_codes()
        self.assertIsInstance(codes, dict)
        for code, parameters in codes.items():
            self.assertIsInstance(code, str)
            self.assertTrue(code, "a reason code cannot be empty")
            self.assertIsInstance(parameters, list)
            self.assertEqual(len(parameters), len(set(parameters)), f"duplicate parameter in {code!r}")
            for name in parameters:
                self.assertIsInstance(name, str)
                self.assertTrue(name.isidentifier(), f"{code!r} has a parameter that is not a keyword name: {name!r}")


class SourceReasonTests(unittest.TestCase):
    def test_every_source_call_matches_the_declared_table(self):
        self.assertEqual(reason_violations(SOURCE, declared_codes()), [])


REGISTERED = {
    "router-skip-window": ["buddyId", "retryAt"],
    "router-wait": ["buddyId", "retryAt"],
    "plain": [],
}


def scan(source: str, codes: dict[str, list[str]] = REGISTERED) -> list[str]:
    """Scan one source module written to a temporary tree, never to ``src``."""
    with tempfile.TemporaryDirectory(prefix="reason-codes-") as directory:
        root = Path(directory)
        (root / "package").mkdir()
        (root / "package" / "sample.py").write_text(source, encoding="utf-8")
        return reason_violations(root, codes)


class CounterexampleTests(unittest.TestCase):
    """Each failure mode is proved on temporary source, not on the package."""

    def test_registered_literals_and_literal_mapping_lookups_pass(self):
        source = '''
CODES = {"skip": "router-skip-window", "wait": "router-wait"}
reason("plain")
reason("router-skip-window", buddyId="buddy-1", retryAt="later")
reason(CODES[kind], buddyId="buddy-1", retryAt="later")
reason(CODES.get(kind), buddyId="buddy-1", retryAt="later")
UNRELATED = {"skip": "not-a-reason-code"}
'''
        self.assertEqual(scan(source), [])

    def test_an_unregistered_literal_code_fails(self):
        violations = scan('reason("not-registered")\n')
        self.assertTrue(
            any("not-registered" in item and "not registered" in item for item in violations),
            violations,
        )

    def test_an_import_alias_is_checked_against_the_same_table(self):
        prefix = "from hey_my_buddy.protocol.reasons import reason as make_reason\n"
        self.assertEqual(scan(prefix + 'make_reason("plain")\n'), [])
        self.assertTrue(scan(prefix + 'make_reason("not-registered")\n'))
        self.assertTrue(scan(prefix + 'make_reason("plain", detail="x")\n'))
        self.assertTrue(scan('def report():\n    from ...protocol.reasons import reason as make_reason\n    return make_reason("not-registered")\n'))

    def test_a_parameter_name_that_differs_from_the_table_fails(self):
        violations = scan('reason("plain", detail="x")\n')
        self.assertTrue(
            any("declares parameters []" in item and "detail" in item for item in violations),
            violations,
        )

    def test_a_missing_registered_parameter_fails(self):
        violations = scan('reason("router-skip-window", buddyId="buddy-1")\n')
        self.assertTrue(any("retryAt" in item for item in violations), violations)

    def test_a_dynamic_code_outside_a_literal_mapping_fails(self):
        for source in (
            "reason(code_for(kind), value=1)\n",
            "reason(kind, value=1)\n",
            'CODES = {"skip": code_for(kind)}\nreason(CODES[kind], buddyId="b", retryAt="r")\n',
        ):
            with self.subTest(source=source):
                self.assertTrue(scan(source), "a dynamic code must be refused")

    def test_every_value_of_a_used_mapping_is_checked(self):
        source = '''
CODES = {"skip": "router-skip-window", "other": "not-registered"}
reason(CODES["skip"], buddyId="b", retryAt="r")
'''
        violations = scan(source)
        self.assertTrue(
            any("not-registered" in item and "not registered" in item for item in violations),
            violations,
        )

    def test_a_mapped_code_with_mismatched_parameters_fails(self):
        source = '''
CODES = {"skip": "router-skip-window"}
reason(CODES[kind], buddyId="b")
'''
        violations = scan(source)
        self.assertTrue(any("retryAt" in item for item in violations), violations)


if __name__ == "__main__":
    unittest.main()
