"""The protected administrative package imports nothing from the application."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "admin_executor"
APPLICATION = {"backend", "bridge", "mcp_server", "hooks", "tests"}


def imported_roots(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def test_no_application_imports():
    offending = {
        f"{path.name}: {root}"
        for path in sorted(PACKAGE.glob("*.py"))
        for root in imported_roots(path.read_text())
        if root in APPLICATION
    }
    assert not offending, f"admin_executor must stay independent of the application: {sorted(offending)}"


def test_the_check_sees_an_application_import():
    assert "backend" in set(imported_roots("from backend.app import main"))
    assert "bridge" in set(imported_roots("import bridge.usage"))
    assert not set(imported_roots("from .execution import ACTIONS")) & APPLICATION
