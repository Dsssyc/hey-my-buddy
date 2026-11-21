"""Tracked files never name the machine they were written on.

Records, fixtures and saved evidence are public. A home directory or the layout of a
project directory identifies one person's machine, so they are written as ``~`` or a
placeholder. The check below runs on whichever machine runs the suite and refuses a
tracked file that carries that machine's own paths.
"""
from __future__ import annotations

import ast
from collections import Counter
from importlib.util import resolve_name
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[3]


def personal_markers(home: PurePath, main_checkout: PurePath | None) -> dict[str, str]:
    """The strings that identify one machine: its home and its project directory."""
    markers: dict[str, str] = {}
    # A bare "/root" or "/" is too short to be distinguishing.
    if len(home.parts) >= 3:
        for separator in ("/", "\\"):
            markers[home.as_posix().replace("/", separator)] = "the absolute home directory"
    if main_checkout is None:
        return markers
    try:
        relative = main_checkout.parent.relative_to(home)
    except ValueError:
        return markers
    # One level ("src/") is generic, and a hidden tool directory is not a personal
    # layout; two visible levels are.
    if len(relative.parts) >= 2 and not relative.parts[0].startswith("."):
        for separator in ("/", "\\"):
            markers[separator.join(relative.parts[:2]) + separator] = "the project directory layout"
    return markers


def offending_files(root: Path, names: list[str], markers: dict[str, str]) -> list[str]:
    """Text files below ``root`` that contain a marker, as ``name: meaning`` lines."""
    offenders: list[str] = []
    for name in names:
        try:
            data = (root / name).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for marker, meaning in markers.items():
            if marker.encode("utf-8", errors="surrogateescape") in data:
                offenders.append(f"{name}: {meaning}")
    return sorted(set(offenders))


def _git(*arguments: str) -> str | None:
    try:
        completed = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="surrogateescape")


class PersonalMarkerTests(unittest.TestCase):
    def test_home_and_two_visible_project_levels_are_markers(self):
        for home in (PurePosixPath("/Users/alice"), PureWindowsPath("C:/Users/alice")):
            with self.subTest(home=home):
                self.assertEqual(
                    personal_markers(home, home / "Desktop/code/project"),
                    {
                        home.as_posix(): "the absolute home directory",
                        str(home).replace("/", "\\"): "the absolute home directory",
                        "Desktop/code/": "the project directory layout",
                        "Desktop\\code\\": "the project directory layout",
                    },
                )

    def test_generic_hidden_or_foreign_locations_add_no_layout_marker(self):
        for home in (PurePosixPath("/Users/alice"), PureWindowsPath("C:/Users/alice")):
            for checkout in (
                home / "project",
                home / "src/project",
                home / ".tool/worktrees/name/project",
                type(home)("/srv/build/checkouts/project"),
                None,
            ):
                with self.subTest(home=home, checkout=checkout):
                    self.assertEqual(personal_markers(home, checkout), {
                        home.as_posix(): "the absolute home directory",
                        str(home).replace("/", "\\"): "the absolute home directory",
                    })

    def test_a_short_home_is_not_a_marker(self):
        for home in (PurePosixPath("/root"), PureWindowsPath("C:/root")):
            with self.subTest(home=home):
                self.assertEqual(personal_markers(home, None), {})

    def test_only_text_files_carrying_a_marker_are_reported(self):
        directory = Path(tempfile.mkdtemp(prefix="hygiene-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for home in (PurePosixPath("/Users/alice"), PureWindowsPath("C:/Users/alice")):
            with self.subTest(home=home):
                (directory / "clean.md").write_text("The archive is under ~/.local/share/example.\n", encoding="utf-8", newline="\n")
                (directory / "leaky.md").write_text(home.as_posix() + "/data\n", encoding="utf-8", newline="\n")
                (directory / "backslashes.md").write_text(str(home).replace("/", "\\") + "\\data\n", encoding="utf-8", newline="\n")
                (directory / "layout.ts").write_text('const path = "~/Desktop/code/project";\n', encoding="utf-8", newline="\n")
                (directory / "binary.bin").write_bytes(b"\0\1" + str(home).encode() + b"/Desktop/code/")
                names = ["clean.md", "leaky.md", "backslashes.md", "layout.ts", "binary.bin", "missing.md"]
                markers = personal_markers(home, home / "Desktop/code/project")
                self.assertEqual(
                    offending_files(directory, names, markers),
                    ["backslashes.md: the absolute home directory", "layout.ts: the project directory layout",
                     "leaky.md: the absolute home directory"],
                )


class TrackedFileTests(unittest.TestCase):
    def test_tracked_files_do_not_name_this_machine(self):
        listed = _git("ls-files", "-z")
        if listed is None:
            self.skipTest("not a Git checkout")
        common = _git("rev-parse", "--git-common-dir")
        main_checkout = (ROOT / common.strip()).resolve().parent if common and common.strip() else None
        markers = personal_markers(Path.home(), main_checkout)
        if not markers:
            self.skipTest("this machine has no distinguishing home or project directory")
        offenders = offending_files(ROOT, [name for name in listed.split("\0") if name], markers)
        self.assertEqual(offenders, [], "write ~ or a placeholder instead of this machine's own paths")


_ALLOWED_SIDE_IMPORTS = Counter([
    ("blackboard/catalog/catalog.py", "discover", "hey_my_buddy.buddy.harnesses.registry", ("adapters",)),
    ("blackboard/catalog/catalog.py", "discovery_available", "hey_my_buddy.buddy.harnesses.registry", ("adapters",)),
    ("blackboard/catalog/catalog.py", "validate_buddy", "hey_my_buddy.buddy.harnesses.registry", ("adapter",)),
    ("blackboard/routing/decision.py", "DecisionCoordinator._adapter_available",
     "hey_my_buddy.buddy.roles.router", ("DecisionAdapter",)),
    ("blackboard/service/harness_health.py", "HarnessHealth._codex_account_read",
     "hey_my_buddy.buddy.harnesses.codex.account_probe", ("read",)),
    ("blackboard/service/harness_health.py", "_discover", "hey_my_buddy.buddy.harnesses.discovery", ("discover",)),
    ("blackboard/service/service.py", "<module>", "hey_my_buddy.buddy.harnesses.registry", ("capability_report",)),
    ("blackboard/service/service.py", "BoardService._refresh_harness_catalog",
     "hey_my_buddy.buddy.harnesses.registry", ("adapter",)),
    ("blackboard/service/service.py", "BoardService.capabilities.handler",
     "hey_my_buddy.buddy.harnesses.registry", ("local_capabilities",)),
    ("blackboard/tasks/macro_task_deletion.py", "_delete_sessions", "hey_my_buddy.buddy.harnesses.registry", ("adapter",)),
    ("blackboard/tasks/workflow.py", "WorkflowCoordinator._execution_adapter",
     "hey_my_buddy.buddy.harnesses.registry", ("adapter",)),
    ("blackboard/tasks/workflow.py", "WorkflowCoordinator.interrupted_native_facts",
     "hey_my_buddy.buddy.harnesses.dsh.session_records", ("session_record_facts",)),
    ("buddy/roles/router_input.py", "<module>", "hey_my_buddy.blackboard.tasks", ("workspace",)),
    ("buddy/roles/turn_io.py", "seal_workspace", "hey_my_buddy.blackboard.tasks.workflow", ("workspace_module",)),
    ("buddy/roles/turn_io.py", "verify_workspace", "hey_my_buddy.blackboard.tasks.workflow", ("workspace_module",)),
    ("buddy/runtime/partial_outputs.py", "capture", "hey_my_buddy.blackboard.tasks.workflow", ("workspace_module",)),
    ("buddy/runtime/partial_outputs.py", "enrich", "hey_my_buddy.blackboard.tasks.workflow", ("workspace_module",)),
])
_SIDE_NAMES = {"blackboard", "buddy"}
_DYNAMIC_IMPORTERS = {"import_module", "__import__"}
_ROOT_PACKAGE = "hey_my_buddy"


def _reference_name(expression):
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        parent = _reference_name(expression.value)
        if parent:
            return f"{parent}.{expression.attr}"
    return None


def _importer(expression, aliases):
    if isinstance(expression, ast.Attribute) and expression.attr in _DYNAMIC_IMPORTERS:
        return expression.attr
    if (isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name)
            and expression.func.id == "getattr" and len(expression.args) >= 2):
        name = expression.args[1]
        if isinstance(name, ast.Constant) and name.value in _DYNAMIC_IMPORTERS:
            return name.value
    return aliases.get(_reference_name(expression))


def _import_aliases(tree):
    # Include local imports and simple assignments, even when a call appears
    # before the definition of the alias in source order.
    aliases = {name: name for name in _DYNAMIC_IMPORTERS}
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module in {"importlib", "builtins"}:
            for name in node.names:
                if name.name in _DYNAMIC_IMPORTERS:
                    aliases[name.asname or name.name] = name.name
        if isinstance(node, ast.Assign):
            assignments.extend((_reference_name(target), node.value) for target in node.targets)
        if isinstance(node, ast.AnnAssign):
            assignments.append((_reference_name(node.target), node.value))
    while True:
        discovered = {name: importer for name, value in assignments
                      if name and name not in aliases and (importer := _importer(value, aliases))}
        if not discovered:
            return aliases
        aliases.update(discovered)


def _dynamic_target(node, importer, package):
    def argument(index, name, default=None):
        if index < len(node.args):
            return node.args[index]
        return keywords.get(name, ast.Constant(default))

    def literal(expression, kind):
        if isinstance(expression, ast.Constant) and type(expression.value) is kind:
            return expression.value
        raise ValueError("expected a literal " + kind.__name__)

    if any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords):
        raise ValueError("argument unpacking can hide the target")
    keywords = {kw.arg: kw.value for kw in node.keywords}
    module = literal(argument(0, "name"), str)
    if importer == "import_module":
        if module.startswith("."):
            module = resolve_name(module, literal(argument(1, "package"), str))
        return module, ()

    level = literal(argument(4, "level", 0), int)
    if level:
        namespace = argument(1, "globals")
        if not (isinstance(namespace, ast.Call) and isinstance(namespace.func, ast.Name)
                and namespace.func.id == "globals" and not namespace.args and not namespace.keywords):
            raise ValueError("relative __import__ requires the current globals()")
        module = resolve_name("." * level + module, package)
    fromlist = argument(3, "fromlist")
    if isinstance(fromlist, ast.Constant) and fromlist.value is None:
        return module, ()
    if not isinstance(fromlist, (ast.List, ast.Tuple)):
        raise ValueError("expected a literal fromlist")
    return module, tuple(literal(name, str) for name in fromlist.elts)


class _SideImports(ast.NodeVisitor):
    def __init__(self, path, tree):
        self.path = path
        self.package = ".".join((_ROOT_PACKAGE, *path.parent.parts))
        self.source_side = path.parts[0] if len(path.parts) > 1 else None
        self.scope = []
        self.aliases = _import_aliases(tree)
        self.references = Counter()
        self.errors = []

    def _record(self, module, names=()):
        if module == _ROOT_PACKAGE:
            for name in names:
                if name.partition(".")[0] in _SIDE_NAMES:
                    self._record(f"{module}.{name}", (name,))
            return
        root, _, submodule = module.partition(".")
        target_side = submodule.partition(".")[0]
        if root == _ROOT_PACKAGE and target_side in _SIDE_NAMES and target_side != self.source_side:
            self.references[(self.path.as_posix(), ".".join(self.scope) or "<module>",
                             module, tuple(sorted(names)))] += 1

    def _visit_definition(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _visit_definition

    def visit_Import(self, node):
        for name in node.names:
            self._record(name.name)

    def visit_ImportFrom(self, node):
        module = resolve_name("." * node.level + (node.module or ""), self.package)
        self._record(module, tuple(name.name for name in node.names))

    def visit_Call(self, node):
        importer = _importer(node.func, self.aliases)
        if importer:
            try:
                self._record(*_dynamic_target(node, importer, self.package))
            except (ImportError, ValueError) as error:
                self.errors.append(f"{self.path}:{node.lineno}: cannot resolve {importer}: {error}")
        self.generic_visit(node)


class ArchitectureBoundaryTests(unittest.TestCase):
    def assert_boundaries(self, sources, allowed):
        found = Counter()
        errors = []
        for name, source in sources:
            path = PurePosixPath(name)
            if len(path.parts) > 1 and path.parts[0] not in _SIDE_NAMES | {"protocol"}:
                continue
            tree = ast.parse(source, filename=name)
            visitor = _SideImports(path, tree)
            visitor.visit(tree)
            found.update(visitor.references)
            errors.extend(visitor.errors)
        self.assertFalse(errors, "\n".join(errors))
        if found != allowed:
            self.fail(f"Unexpected imports: {dict(found - allowed)}\n"
                      f"Missing imports: {dict(allowed - found)}")

    def test_repository_imports_match_the_architecture_boundary(self):
        package = ROOT / "src" / _ROOT_PACKAGE
        sources = ((path.relative_to(package).as_posix(), path.read_bytes())
                   for path in sorted(package.rglob("*.py")))
        self.assert_boundaries(sources, _ALLOWED_SIDE_IMPORTS)

    def test_cross_side_imports_keep_exact_counts_and_qualified_names(self):
        path = "blackboard/service/example.py"
        source = ("class Service:\n"
                  "    async def method(self):\n"
                  "        from ...buddy.harnesses import registry as adapters, discovery\n")
        key = (path, "Service.method", "hey_my_buddy.buddy.harnesses", ("discovery", "registry"))
        self.assert_boundaries([(path, source)], Counter({key: 1}))
        for changed in (
            source + "        from ...buddy.harnesses import discovery, registry\n",
            source + "        import hey_my_buddy.buddy.harnesses.registry\n",
        ):
            with self.subTest(source=changed), self.assertRaisesRegex(AssertionError, "Unexpected imports"):
                self.assert_boundaries([(path, changed)], Counter({key: 1}))

    def test_shared_modules_cannot_import_either_side(self):
        for path, source in (
            ("protocol/example.py", "from .. import buddy"),
            ("protocol/example.py", "import hey_my_buddy.blackboard.tasks"),
            ("example.py", "from . import blackboard"),
            ("__init__.py", "from hey_my_buddy.buddy.harnesses import discovery"),
            ("example.py", "__import__('hey_my_buddy', fromlist=['blackboard.tasks'])"),
        ):
            with self.subTest(path=path, source=source), self.assertRaisesRegex(AssertionError, "hey_my_buddy"):
                self.assert_boundaries([(path, source)], Counter())

    def test_dynamic_imports_contribute_literal_targets(self):
        path = "buddy/runtime/example.py"
        source = ("from importlib import import_module as load\n"
                  "load('.tasks', package='hey_my_buddy.blackboard')\n"
                  "import importlib as loader\n"
                  "alias = loader.import_module\n"
                  "alias('hey_my_buddy.blackboard.tasks')\n"
                  "__import__('hey_my_buddy.blackboard', fromlist=['store', 'tasks'])\n"
                  "__import__('blackboard', globals(), level=3)\n")
        self.assert_boundaries([(path, source)], Counter({
            (path, "<module>", "hey_my_buddy.blackboard.tasks", ()): 2,
            (path, "<module>", "hey_my_buddy.blackboard", ("store", "tasks")): 1,
            (path, "<module>", "hey_my_buddy.blackboard", ()): 1,
        }))

    def test_nonliteral_dynamic_targets_are_rejected(self):
        for source in (
            "import_module(var)",
            "importlib.import_module('.module', var)",
            "from importlib import import_module as load\nload(name=var)",
            "__import__(var)",
            "from builtins import __import__ as load\nload(var)",
            "import builtins as native\nnative.__import__(var)",
            "alias = importlib.import_module\nalias(var)",
            "self.load = importlib.import_module\nself.load(var)",
            "getattr(importlib, 'import_module')(var)",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(AssertionError, "expected a literal str"):
                self.assert_boundaries([("blackboard/example.py", source)], Counter())


if __name__ == "__main__":
    unittest.main()
