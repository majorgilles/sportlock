"""The dependency rules of docs/architecture-decision-records/2026_10_09_feature_first_clean_architecture.md,
checked on every module's imports (AST, so nothing is executed)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sportlock"
TESTS = Path(__file__).resolve().parents[1]
FEATURES = {
    "athlete",
    "calendar",
    "coaching",
    "diagnostics",
    "exercises",
    "locks",
    "music",
    "progression",
    "recovery",
    "reporting",
    "settings",
    "training",
}
# Which features' domains a feature's domain may use (the context map). Everything else crosses
# features in the application layer.
DOMAIN_CONTEXT_MAP = {
    "coaching": {"exercises"},
    "locks": {"settings"},
    "progression": {"exercises"},
    "recovery": {"training"},
    "settings": {"music"},
    "training": {"exercises"},
}
THIRD_PARTY_IN_DOMAIN = {"pydantic"}


def _modules() -> list[tuple[str, Path]]:
    return [(".".join(p.relative_to(PACKAGE.parent).with_suffix("").parts), p) for p in PACKAGE.rglob("*.py")]


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level:
            raise AssertionError(f"{path}: relative import; use absolute sportlock.* imports")
    return found


def _layer(module: str) -> tuple[str, str]:
    """(feature, layer) of a sportlock module, e.g. ("locks", "domain")."""
    parts = module.split(".")
    if parts[1] == "shared_kernel":
        return "shared_kernel", "infrastructure" if "infrastructure" in parts else "domain"
    if parts[1] == "app":
        return "app", "app"
    return parts[1], parts[2] if len(parts) > 2 else "package"


def _violations(rule) -> list[str]:
    problems = []
    for module, path in _modules():
        for imported in _imports(path):
            message = rule(module, imported)
            if message:
                problems.append(f"{module} imports {imported}: {message}")
    return problems


def _external_domain_import(imported: str) -> str | None:
    top = imported.split(".", maxsplit=1)[0]
    if top in sys.stdlib_module_names or top in THIRD_PARTY_IN_DOMAIN:
        return None
    return "the domain uses only the standard library and pydantic"


def _domain_rule(module: str, imported: str) -> str | None:
    feature, layer = _layer(module)
    if layer != "domain":
        return None
    if imported.split(".", maxsplit=1)[0] != "sportlock":
        return _external_domain_import(imported)
    other, other_layer = _layer(imported)
    if other == "shared_kernel" and other_layer == "domain":
        return None
    if other_layer != "domain":
        return f"the domain must not depend on {other_layer}"
    if other != feature and other not in DOMAIN_CONTEXT_MAP.get(feature, set()):
        return f"{feature}'s domain may not use {other}'s domain (add it to the context map with a reason)"
    return None


def test_domain__imports_only_its_feature_the_shared_kernel_and_the_context_map():
    assert _violations(_domain_rule) == []


def test_application__never_imports_infrastructure_or_the_app():
    def rule(module, imported):
        if _layer(module)[1] != "application" or not imported.startswith("sportlock."):
            return None
        other, other_layer = _layer(imported)
        if other_layer in ("infrastructure", "app"):
            return "use cases depend on ports; the composition root injects adapters"
        return None

    assert _violations(rule) == []


def test_infrastructure__never_imports_application_or_the_app():
    def rule(module, imported):
        if _layer(module)[1] != "infrastructure" or not imported.startswith("sportlock."):
            return None
        if _layer(imported)[1] in ("application", "app"):
            return "adapters implement ports; they don't call use cases"
        return None

    assert _violations(rule) == []


def test_app__only_the_app_package_imports_it():
    def rule(module, imported):
        if imported.startswith("sportlock.app") and not module.startswith("sportlock.app"):
            return "the composition root and entry points sit on top of everything"
        return None

    assert _violations(rule) == []


def test_shared_kernel__depends_on_no_feature():
    def rule(module, imported):
        if _layer(module)[0] != "shared_kernel" or not imported.startswith("sportlock."):
            return None
        return None if _layer(imported)[0] == "shared_kernel" else "the shared kernel is the bottom layer"

    assert _violations(rule) == []


def test_features__every_package_is_a_known_feature():
    packages = {p.name for p in PACKAGE.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert packages == FEATURES | {"shared_kernel", "app"}


def _is_dataclass(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return ast.unparse(target).split(".")[-1] == "dataclass"


def test_domain__has_no_dataclasses():
    offenders = [
        str(path)
        for path in PACKAGE.rglob("domain/*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ClassDef) and any(_is_dataclass(decorator) for decorator in node.decorator_list)
    ]

    assert offenders == [], "domain models are pydantic models from shared_kernel/base.py"


def _imports_mock(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import) and any(alias.name.startswith("unittest.mock") for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("unittest.mock"):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "unittest"
            and any(alias.name == "mock" for alias in node.names)
        ):
            return True
    return False


def test_tests__use_fakes_not_mocks():
    offenders = [str(path) for path in TESTS.rglob("*.py") if _imports_mock(path)]

    assert offenders == [], "fake the outside world through its ports (tests/world.py)"
