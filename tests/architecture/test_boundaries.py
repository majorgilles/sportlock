"""The dependency rules of docs/architecture-decision-records/2026_10_09_feature_first_clean_architecture.md,
checked on every module's imports (AST, so nothing is executed)."""

from __future__ import annotations

import ast
import re
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


def test_domain__any_module__imports_only_its_feature_the_shared_kernel_and_the_context_map():
    assert _violations(_domain_rule) == []


def test_application__any_module__never_imports_infrastructure_or_the_app():
    def rule(module, imported):
        if _layer(module)[1] != "application" or not imported.startswith("sportlock."):
            return None
        other, other_layer = _layer(imported)
        if other_layer in ("infrastructure", "app"):
            return "use cases depend on ports; the composition root injects adapters"
        return None

    assert _violations(rule) == []


def test_infrastructure__any_module__never_imports_application_or_the_app():
    def rule(module, imported):
        if _layer(module)[1] != "infrastructure" or not imported.startswith("sportlock."):
            return None
        if _layer(imported)[1] in ("application", "app"):
            return "adapters implement ports; they don't call use cases"
        return None

    assert _violations(rule) == []


def test_app__imported_from_elsewhere__only_by_the_app_package():
    def rule(module, imported):
        if imported.startswith("sportlock.app") and not module.startswith("sportlock.app"):
            return "the composition root and entry points sit on top of everything"
        return None

    assert _violations(rule) == []


def test_shared_kernel__any_module__depends_on_no_feature():
    def rule(module, imported):
        if _layer(module)[0] != "shared_kernel" or not imported.startswith("sportlock."):
            return None
        return None if _layer(imported)[0] == "shared_kernel" else "the shared kernel is the bottom layer"

    assert _violations(rule) == []


def test_features__every_package__is_a_known_feature():
    packages = {p.name for p in PACKAGE.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert packages == FEATURES | {"shared_kernel", "app"}


def _is_dataclass(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return ast.unparse(target).split(".")[-1] == "dataclass"


def test_domain__any_class__is_not_a_dataclass():
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


def test_tests__any_module__imports_no_mocks():
    offenders = [str(path) for path in TESTS.rglob("*.py") if _imports_mock(path)]

    assert offenders == [], "fake the outside world through its ports (tests/world.py)"


def _snake(name: str) -> str:
    """A type's name as a variable name: WarningPopupProtocol is warning_popup, LingQProtocol is lingq."""
    name = name.removesuffix("Protocol")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z][a-z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _classes() -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for _, path in _modules()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ClassDef)
    }


def _own_type(annotation: ast.expr | None, classes: dict[str, ast.ClassDef]) -> str | None:
    text = ast.unparse(annotation).replace(" | None", "").strip("'\"") if annotation else ""
    return text if text in classes else None


def _names_for(class_name: str, classes: dict[str, ast.ClassDef]) -> set[str]:
    """The class's own name and the ports it implements, as variable names."""
    bases = [ast.unparse(base) for base in classes[class_name].bases]
    return {_snake(class_name)} | {_snake(base) for base in bases if base.endswith("Protocol")}


def _built_class(value: ast.expr, classes: dict[str, ast.ClassDef]) -> str | None:
    if isinstance(value, ast.BoolOp):
        value = value.values[-1]
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in classes:
        return value.func.id
    return None


def _naming_problems(cls: ast.ClassDef, classes: dict[str, ast.ClassDef]) -> list[str]:
    problems = []
    for function in [node for node in cls.body if isinstance(node, ast.FunctionDef)]:
        if function.name == "__init__":
            for argument in function.args.args[1:] + function.args.kwonlyargs:
                kind = _own_type(argument.annotation, classes)
                if kind and not argument.arg.endswith(_snake(kind)):
                    problems.append(f"{cls.name}({argument.arg}: {kind}) should be named {_snake(kind)}")
        if any(ast.unparse(decorator).endswith("cached_property") for decorator in function.decorator_list):
            kind = _own_type(function.returns, classes)
            if kind and not any(function.name.endswith(name) for name in _names_for(kind, classes)):
                problems.append(
                    f"{cls.name}.{function.name} -> {kind} should end with one of {sorted(_names_for(kind, classes))}"
                )
        for node in ast.walk(function):
            target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
            if not (isinstance(target, ast.Attribute) and ast.unparse(target.value) == "self"):
                continue
            kind = _built_class(node.value, classes)
            if (
                kind
                and not target.attr.startswith("_")
                and not any(target.attr.endswith(name) for name in _names_for(kind, classes))
            ):
                problems.append(
                    f"{cls.name}.{target.attr} = {kind}(...) should end with one of {sorted(_names_for(kind, classes))}"
                )
    return problems


def test_dependencies__constructor_parameters_and_attributes__are_named_after_their_type():
    classes = _classes()

    problems = [problem for cls in classes.values() for problem in _naming_problems(cls, classes)]

    assert problems == [], "see 'Names say the type' in ~/.agents/AGENTS.md"


CRUD_CAPABILITIES = {
    "CreateRepositoryProtocol",
    "GetRepositoryProtocol",
    "FindRepositoryProtocol",
    "UpdateRepositoryProtocol",
    "DeleteRepositoryProtocol",
    "GetSingletonRepositoryProtocol",
    "UpdateSingletonRepositoryProtocol",
}


def _crud_problems(cls: ast.ClassDef) -> list[str]:
    bases = {ast.unparse(base).split("[")[0] for base in cls.bases} - {"Protocol"}
    problems = [f"{cls.name} extends {sorted(bases - CRUD_CAPABILITIES)}"] if bases - CRUD_CAPABILITIES else []
    if not bases:
        problems.append(f"{cls.name} extends no CRUD capability")
    methods = [node.name for node in cls.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
    if methods:
        problems.append(f"{cls.name} declares its own methods {methods}")
    return problems


def test_repositories__every_repository_protocol__is_made_only_of_crud_capabilities():
    repositories = [
        node
        for module, path in _modules()
        if not module.endswith("shared_kernel.repositories")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ClassDef) and node.name.endswith("RepositoryProtocol")
    ]

    problems = [problem for cls in repositories for problem in _crud_problems(cls)]

    assert problems == [], "see 'Repositories are CRUD' in ~/.agents/AGENTS.md"
