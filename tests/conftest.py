"""Test setup: point every XDG directory at a sandbox before any sportlock module computes its
paths, so tests never touch the real data, config, state or log files."""

from __future__ import annotations

import os
import tempfile

_sandbox = tempfile.mkdtemp(prefix="sportlock-tests-")
for _var in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
    os.environ[_var] = os.path.join(_sandbox, _var.lower())
    os.makedirs(os.environ[_var], exist_ok=True)

import pytest  # noqa: E402

from sportlock.exercises.domain.catalogue import Catalogue  # noqa: E402
from sportlock.exercises.infrastructure.details_repository import load_catalogue  # noqa: E402
from tests.world import World  # noqa: E402


@pytest.fixture(scope="session")
def catalogue() -> Catalogue:
    """The real exercise catalogue (read-only)."""
    return load_catalogue()


@pytest.fixture
def world(tmp_path, catalogue) -> World:
    """A whole app on a temporary database and config, with fakes for the outside world."""
    return World(tmp_path, catalogue)
