"""Mechanical conformance of every name in HARNESS_NAMES, declarations only.

Each check confirms one part of a harness integration from the declarations and
structures the source already carries: run-seam and adapter registration, the
capability surface, the run contract's four tool scopes as per-harness
declarations, the interfaces each role call condition needs, inquiry delivery,
the catalog and health entry points, the configuration variables that reach
every child environment, the stored health form, the protocol name lists and
the console's marks. No harness runs and no product code changes. Per ADR-023
the harnesses differ by declaration: a check confirms that every harness
declares each item explicitly, that a declared support has the interfaces its
call path consumes, and that name lists agree in both directions — never that
every harness supports everything. What this file cannot verify is recorded in
the integration reference's closing section.
"""
import ast
import inspect
import os
import re
import sqlite3
from temporary import TemporaryDirectory
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.service.harness_health import HARNESSES as HEALTH_HARNESSES
from hey_my_buddy.blackboard.store.db import HARNESS_SCHEMA
from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.discovery import native_environment
from hey_my_buddy.buddy.harnesses.live import EXISTING_CAPABILITIES
from hey_my_buddy.buddy.harnesses.registry import (
    BUILT_IN, RUN_SEAMS, adapters as built_in_adapters, fast_evidence_is_top_level, worker_format,
)
from hey_my_buddy.buddy.harnesses.runtime_selection import controller_environment
from hey_my_buddy.buddy.harnesses.run_contract import (
    HARNESS_NAMES, PrivateStatePaths, RunBudget, RunBuddy, RunIdentity, RunRequest,
)
from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_dirs import ADAPTERS as PRIVATE_STATE_ADAPTERS
from hey_my_buddy.protocol.capabilities import ADAPTER_CAPABILITIES, PERMISSION_LEVELS, adapter_capabilities
from hey_my_buddy.protocol.schemas import CODING_HARNESSES
from hey_my_buddy.protocol.tool_evidence import ACP_CATEGORIES, NATIVE_TOOL_CATEGORIES

ROOT = Path(__file__).resolve().parents[4]
DELIVERY_MODES = ("realtime", "cooperative-checkpoint", "unsupported")
ENFORCEMENTS = ("enforced", "convention")
PERMISSIONS = ("none", "read", "write", "full")
#: The surface every coding harness shares; `native-session` is deliberately
#: not here, because it tracks each adapter's own native-resume declaration.
SHARED_CAPABILITIES = ("inquiry", "workspace", "cancel", "artifacts", "deadline")
#: Each harness's own configuration-directory variable, or None when it keeps
#: its configuration under the shared home directories only. The mapping is
#: maintained here because the product carries no declaration for it; every
#: child-environment builder below must pass the variable through.
CONFIGURATION_DIRECTORY_VARIABLES = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR",
                                     "zcode": "ZCODE_DATA_BASE_DIR", "dsh": "DSH_HOME"}
#: The native request controls the role controller may ask a run module about
#: (buddy/roles/controller.py); a module declares the subset it supports.
REQUEST_CONTROLS = ("resume_checkpoint",)
#: The run-module operations every Worker turn calls unconditionally, with the
#: positional/keyword shape the role callers use (buddy/roles/run_execution.py).
UNCONDITIONAL_OPERATIONS = {"run": (1, ("observer", "services", "cancelled")),
                            "run_discovery": (0, ()),
                            "check_preparation": (2, ()),
                            "session_facts": (2, ()),
                            "validate_turn_provenance": (1, ())}
#: Session-tool delivery (worker_format(name) is None) binds these keywords.
SESSION_TOOL_KEYWORDS = ("invocation_root", "identity", "input_sha256", "attention_path", "session_tools",
                         "completion_tool", "validate_outcome", "inquiry", "inquiry_tools", "native_stderr",
                         "delegation_authority")
#: Schema delivery (worker_format(name) is not None) binds these keywords when
#: a Host inquiry rides the turn.
INQUIRY_KEYWORDS = ("invocation_root", "identity", "input_sha256", "attention_path", "inquiry", "answer_schema")


def _request(harness):
    return RunRequest(
        identity=RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                             invocation_id="invocation"),
        harness=harness,
        buddy=RunBuddy(provider="provider", model="model", effort="effort"),
        cwd=str(ROOT),
        private_state=PrivateStatePaths(state_dir=str(ROOT), invocation_root=str(ROOT), native_root=str(ROOT)),
        input_text="conformance",
        permission="read",
        output_schema={"type": "object"},
        budget=RunBudget(timeout_seconds=60),
        session_record_path=str(ROOT / "session.jsonl"),
    )


def _unregistered_name():
    """A generated negative name, proven absent from every registered set."""
    taken = (set(HARNESS_NAMES) | {"command", "decision", "node"} | set(RUN_SEAMS)
             | set(ADAPTER_CAPABILITIES) | set(CODING_HARNESSES) | set(PRIVATE_STATE_ADAPTERS)
             | set(EXISTING_CAPABILITIES) | set(discovery.HARNESS_NAMES) | set(HEALTH_HARNESSES))
    name = "conformance-unregistered"
    while name in taken:
        name += "-x"
    return name


def _binds(operation, module, *, positionals, keywords):
    """One module operation accepts the positional and keyword shape its caller uses."""
    function = getattr(module, operation, None)
    if not callable(function):
        return f"{operation} is missing or not callable"
    parameters = inspect.signature(function).parameters
    accepted = sum(parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                      inspect.Parameter.POSITIONAL_OR_KEYWORD)
                   for parameter in parameters.values())
    if accepted < positionals:
        return f"{operation} accepts fewer than {positionals} positional arguments"
    for keyword in keywords:
        if keyword not in parameters or parameters[keyword].kind not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            return f"{operation} does not accept the {keyword} keyword"
    return None


def _string_collections(source):
    """Every tuple/set/list literal of plain strings in one parsed module."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Tuple, ast.Set, ast.List)) and node.elts and all(
                isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts):
            yield {item.value for item in node.elts}


def _balanced_block(source, anchor, opener, closer):
    """The first balanced block after one declaration anchor, whatever the layout."""
    index = source.find(anchor)
    if index < 0:
        raise AssertionError(f"{anchor!r} is not declared in the console source")
    start = source.find(opener, index)
    if start < 0:
        raise AssertionError(f"{anchor!r} has no {opener} initializer")
    depth = 0
    for position in range(start, len(source)):
        character = source[position]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return source[start + 1:position]
    raise AssertionError(f"{anchor!r} has an unbalanced initializer")


def _split_entries(block):
    """Comma-separated entries of one flat block, ignoring nested content and strings."""
    entries, current, depth, quote = [], [], 0, None
    for character in block:
        if quote:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in "\"'":
            quote = character
            current.append(character)
        elif character in "{[(":
            depth += 1
            current.append(character)
        elif character in "}])":
            depth -= 1
            current.append(character)
        elif character == "," and depth == 0:
            entries.append("".join(current))
            current = []
        else:
            current.append(character)
    entries.append("".join(current))
    return [entry.strip() for entry in entries if entry.strip()]


def _identifier(value):
    return value.strip() if value else None


class HarnessConformanceTests(unittest.TestCase):
    def test_the_run_seam_registry_covers_exactly_the_harness_names(self):
        self.assertEqual(set(RUN_SEAMS), set(HARNESS_NAMES))
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                module = RUN_SEAMS[name]
                for operation in ("run", "run_discovery"):
                    self.assertTrue(callable(getattr(module, operation, None)),
                                    f"{name} has no callable {operation} on its registered run seam")

    def test_the_adapter_descriptions_cover_every_harness(self):
        registry = built_in_adapters()
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                self.assertIn(name, registry)
                instance = registry[name]
                self.assertEqual(instance.name, name)
                self.assertEqual(tuple(instance.capabilities), adapter_capabilities(name))

    def test_capability_declarations_carry_the_shared_worker_surface(self):
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                instance = built_in_adapters()[name]
                capabilities = tuple(instance.capabilities)
                for capability in (name, *SHARED_CAPABILITIES):
                    self.assertIn(capability, capabilities,
                                  f"{name} does not declare the shared capability {capability}")
                self.assertEqual(("native-session" in capabilities), instance.native_resume,
                                 f"{name}'s native-session capability and native-resume declaration disagree")

    def test_permission_levels_are_shared_by_roles(self):
        self.assertEqual(set(PERMISSION_LEVELS), set(HARNESS_NAMES))
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                declaration = PERMISSION_LEVELS[name]
                self.assertTrue(declaration, f"{name} declares no permission level at all")
                self.assertLessEqual(set(declaration), set(PERMISSIONS))
                capabilities = adapter_capabilities(name)
                for level in PERMISSIONS:
                    if level in declaration:
                        self.assertIn(declaration[level], ENFORCEMENTS)
                    self.assertEqual(f"permission:{level}" in capabilities, level in declaration)
                self.assertEqual("none" in declaration, name != "claude")

    def test_every_harness_declares_the_no_tool_scope_and_carriers_match(self):
        """The fourth scope ``none`` is a per-harness declaration, not a universal one.

        A declared support must carry the interfaces the fast call path actually
        drives (`start_fast`/`fast_request`: `check_preparation` before the
        launch, `run` under the none scope, `native_evidence` when evidence
        capture is on); a declared absence is refused by fast routing per that
        declaration, which the routing suite guards.
        """
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                if "none" not in PERMISSION_LEVELS[name]:
                    continue
                module = RUN_SEAMS[name]
                for operation, positionals in (("check_preparation", 2), ("native_evidence", 1)):
                    self.assertIsNone(_binds(operation, module, positionals=positionals, keywords=()),
                                      f"{name} declares the no-tool scope but {operation} is unusable")

    def test_the_run_module_provides_the_interfaces_each_call_condition_needs(self):
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                module = RUN_SEAMS[name]
                for operation, (positionals, keywords) in UNCONDITIONAL_OPERATIONS.items():
                    self.assertIsNone(_binds(operation, module, positionals=positionals, keywords=keywords),
                                      f"{name} cannot serve the unconditional {operation} call")
                controls = getattr(module, "supported_request_controls", ())
                self.assertLessEqual(set(controls), set(REQUEST_CONTROLS),
                                     f"{name} declares an unknown native request control")
                if worker_format(name) is None:
                    self.assertIsNone(_binds("prepare_services", module, positionals=0,
                                             keywords=SESSION_TOOL_KEYWORDS),
                                      f"{name} cannot bind the session-tool delivery factory")
                else:
                    self.assertIsNone(_binds("prepare_inquiry_services", module, positionals=0,
                                             keywords=INQUIRY_KEYWORDS),
                                      f"{name} cannot bind the schema-delivery inquiry factory")
                instance = built_in_adapters()[name]
                if "none" in PERMISSION_LEVELS[name] or fast_evidence_is_top_level(name):
                    self.assertIsNone(_binds("native_evidence", module, positionals=1, keywords=()),
                                      f"{name} can be asked for captured evidence but has no projection")
                for flag in ("native_resume", "interrupted_resume", "native_resume_permission_change",
                             "model_discovery"):
                    self.assertIsInstance(getattr(instance, flag, None), bool,
                                          f"{name}'s {flag} declaration is not a boolean")
                if instance.interrupted_resume:
                    self.assertTrue(instance.native_resume,
                                    f"{name} declares interrupted resume without native resume")

    def test_every_harness_declares_its_inquiry_delivery_consistently(self):
        self.assertEqual(set(EXISTING_CAPABILITIES), set(HARNESS_NAMES))
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                mode = EXISTING_CAPABILITIES[name].inquiry_delivery
                self.assertIn(mode, DELIVERY_MODES)
                has_inquiry = "inquiry" in adapter_capabilities(name)
                self.assertEqual(has_inquiry, mode != "unsupported",
                                 f"{name}'s inquiry capability and delivery mode disagree")

    def test_catalog_and_discovery_entry_points_exist(self):
        self.assertEqual(set(discovery.HARNESS_NAMES), set(HARNESS_NAMES))
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                self.assertTrue(discovery._discovery_spec(name).command)
                instance = built_in_adapters()[name]
                self.assertTrue(instance.model_discovery,
                                f"{name} does not declare model discovery")
                for operation in ("discover_models", "discovery_available", "available"):
                    self.assertTrue(callable(getattr(instance, operation, None)),
                                    f"{name} has no callable {operation} entry point")

    def test_the_health_entry_point_and_stored_form_admit_every_harness(self):
        self.assertEqual(set(HEALTH_HARNESSES), set(HARNESS_NAMES))
        connection = sqlite3.connect(":memory:")
        negative = _unregistered_name()
        try:
            connection.executescript(HARNESS_SCHEMA)
            for name in HARNESS_NAMES:
                with self.subTest(harness=name):
                    connection.execute("INSERT INTO harness_health(harness) VALUES (?)", (name,))
            with self.subTest(harness=negative):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO harness_health(harness) VALUES (?);", (negative,))
        finally:
            connection.close()

    def test_the_run_contract_admits_every_harness_name(self):
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                self.assertEqual(_request(name).harness, name)
        with self.assertRaises(BoardError):
            _request(_unregistered_name())

    def test_name_lists_are_bidirectional_and_unique(self):
        built_in_names = [cls.name for cls in BUILT_IN]
        self.assertEqual(len(built_in_names), len(set(built_in_names)), "an adapter name is registered twice")
        self.assertEqual(set(built_in_names), set(HARNESS_NAMES) | {"command", "decision"})
        self.assertEqual(set(CODING_HARNESSES), set(HARNESS_NAMES))
        self.assertEqual(set(NATIVE_TOOL_CATEGORIES), set(HARNESS_NAMES))
        self.assertLessEqual(set(HARNESS_NAMES) | {"command"}, set(PRIVATE_STATE_ADAPTERS))
        self.assertLessEqual(set(HARNESS_NAMES), set(ADAPTER_CAPABILITIES))
        # Any literal name collection in the service and the execution process
        # that names one harness must name them all: a partial admission,
        # refresh or cleanup list is how a new harness gets silently missed.
        service_source = (ROOT / "src/hey_my_buddy/blackboard/service/service.py").read_text(encoding="utf-8")
        execution_source = (ROOT / "src/hey_my_buddy/buddy/runtime/execution.py").read_text(encoding="utf-8")
        admitted = set(HARNESS_NAMES) | {"command", "decision", "node"}
        for label, source in (("service", service_source), ("execution process", execution_source)):
            for index, collection in enumerate(_string_collections(source)):
                if collection & set(HARNESS_NAMES) and collection <= admitted:
                    self.assertLessEqual(set(HARNESS_NAMES), collection,
                                         f"a {label} name list omits {sorted(set(HARNESS_NAMES) - collection)}")

    def test_tool_classification_tables_are_well_formed(self):
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                table = NATIVE_TOOL_CATEGORIES[name]
                self.assertIsInstance(table, dict, f"{name}'s classification table is not a mapping")
                for tool, category in table.items():
                    self.assertTrue(isinstance(tool, str) and tool,
                                    f"{name} classifies a tool under an empty name")
                    self.assertIn(category, ACP_CATEGORIES,
                                  f"{name} maps {tool} to the unknown category {category!r}")

    def test_every_harness_configuration_variable_reaches_every_child_environment(self):
        """A harness's own configuration directory travels through every environment builder."""
        self.assertEqual(set(CONFIGURATION_DIRECTORY_VARIABLES), set(HARNESS_NAMES))
        variables = [value for value in CONFIGURATION_DIRECTORY_VARIABLES.values() if value is not None]
        parent = {**{variable: "/fixture/daily" for variable in variables},
                  "HMB_AGENT_CREDENTIAL_FILE": "/fixture/credential.json", "PATH": "/usr/bin"}
        native = native_environment(dict(parent))
        for variable in variables:
            with self.subTest(boundary="native allowlist", variable=variable):
                self.assertEqual(native.get(variable), "/fixture/daily")
        self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", native)
        with TemporaryDirectory() as directory:
            controller = controller_environment(Path(directory), dict(parent))
            readonly = controller_environment(Path(directory), dict(parent), read_only=True)
        for variable in variables:
            with self.subTest(boundary="controller", variable=variable):
                self.assertEqual(controller.get(variable), "/fixture/daily")
                self.assertEqual(readonly.get(variable), "/fixture/daily")
        self.assertEqual(controller.get("HMB_AGENT_CREDENTIAL_FILE"), "/fixture/credential.json")
        self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", readonly)
        with TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {**{variable: "/users/fixture/daily" for variable in variables},
                                             "HMB_AGENT_CREDENTIAL_FILE": "/fixture/credential.json",
                                             "UV_CACHE_DIR": directory}):
            from hey_my_buddy.cli.checks import child_environment
            values = child_environment(Path(directory) / "root", Path(directory) / "private",
                                       home_root=Path(directory) / "home")
            for variable in variables:
                with self.subTest(boundary="check runner", variable=variable):
                    self.assertTrue(Path(values[variable]).resolve()
                                    .is_relative_to((Path(directory) / "home").resolve()),
                                    f"the check runner does not give {variable} a private home")
            self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", values)
            import support
            fixture_root = Path(directory) / "fixture"
            fixture_root.mkdir(parents=True)
            fixture = support._child_environment(fixture_root)
            for variable in variables:
                with self.subTest(boundary="test fixture", variable=variable):
                    self.assertTrue(Path(fixture[variable]).resolve()
                                    .is_relative_to((Path(directory) / "fixture").resolve()),
                                    f"the fixture environment does not override {variable} privately")
            with self.assertRaises(ValueError):
                support._child_environment(fixture_root,
                                           {variables[0]: "/outside/the/fixture"})
            self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", fixture)

    def test_the_console_lists_a_mark_and_a_name_for_every_harness(self):
        """A static read of the console sources; it proves the records exist, not what renders.

        The console is being reworked; once it settles, a frontend test should
        assert the rendered name and mark instead of this source-level check.
        """
        logo = (ROOT / "apps/console/src/buddies/HarnessLogo.tsx").read_text(encoding="utf-8")
        names = (ROOT / "apps/console/src/buddy-display.ts").read_text(encoding="utf-8")
        order = (ROOT / "apps/console/src/api.ts").read_text(encoding="utf-8")
        imported = set(re.findall(r"import\s+([A-Za-z_$][\w$]*)\s+from", logo))
        marks, display = {}, {}
        for entry in _split_entries(_balanced_block(logo, "const logos", "{", "}")):
            key, separator, value = entry.partition(":")
            marks[key.strip().strip("\"'")] = _identifier(value) if separator else key.strip().strip("\"'")
        for entry in _split_entries(_balanced_block(names, "const HARNESS_NAMES", "{", "}")):
            key, separator, value = entry.partition(":")
            self.assertTrue(separator, f"the display-name record has an entry without a value: {entry}")
            display[key.strip().strip("\"'")] = value.strip().strip("\"'")
        ranking = [value.strip("\"'") for value in
                   _split_entries(_balanced_block(order, "const HARNESS_ORDER", "[", "]"))]
        self.assertEqual(len(ranking), len(set(ranking)), "the buddy page order lists a harness twice")
        self.assertEqual(set(ranking), set(HARNESS_NAMES))
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                self.assertIn(name, marks, f"the console logo record has no mark for {name}")
                self.assertIn(marks[name], imported,
                              f"the logo record's mark for {name} is not an imported asset")
                self.assertIn(name, display, f"the console display-name record has no name for {name}")
                self.assertTrue(display[name], f"the display name for {name} is empty")


if __name__ == "__main__":
    unittest.main()
