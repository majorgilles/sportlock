"""The dependency rules of docs/architecture-decision-records/2026_10_09_feature_first_clean_architecture.md,
checked on every module's imports (AST, so nothing is executed)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sportlock"
FEATURES = {"athlete", "calendar", "coaching", "diagnostics", "exercises", "locks", "progression", "recovery",
            "settings", "training"}
# Which features' domains a feature's domain may use (the context map). Everything else crosses
# features in the application layer.
DOMAIN_CONTEXT_MAP = {
    "coaching": {"exercises"},
    "locks": {"settings"},
    "progression": {"exercises"},
    "recovery": {"training"},
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


def test_domain__imports_only_its_feature_the_shared_kernel_and_the_context_map():
    def rule(module, imported):
        feature, layer = _layer(module)
        if layer != "domain":
            return None
        top = imported.split(".")[0]
        if top != "sportlock":
            if top in sys.stdlib_module_names or top in THIRD_PARTY_IN_DOMAIN:
                return None
            return "the domain uses only the standard library and pydantic"
        other, other_layer = _layer(imported)
        if other == "shared_kernel" and other_layer == "domain":
            return None
        if other_layer != "domain":
            return f"the domain must not depend on {other_layer}"
        if other != feature and other not in DOMAIN_CONTEXT_MAP.get(feature, set()):
            return f"{feature}'s domain may not use {other}'s domain (add it to the context map with a reason)"
        return None

    assert _violations(rule) == []


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
