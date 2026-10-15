"""Persistence ports for locks."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from sportlock.locks.domain.locks import (
    ActiveLock,
    DesktopToRestore,
    GivenWarning,
    LockEvent,
    LockOutcome,
    LockPlan,
    Override,
)
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.repositories import (
    CreateRepositoryProtocol,
    FindRepositoryProtocol,
    GetRepositoryProtocol,
    GetSingletonRepositoryProtocol,
    UpdateRepositoryProtocol,
    UpdateSingletonRepositoryProtocol,
)


class LockEventFilters(ValueObject):
    """Which lock events to load: by outcome and by the day their window starts.

    In insertion order, or newest first (by when they began) with `newest_first`; `latest` keeps the first
    `latest` of that order.
    """

    outcomes: tuple[LockOutcome, ...] | None = None
    first_day: date | None = None
    last_day: date | None = None
    newest_first: bool = False
    latest: int | None = None


class OverrideFilters(ValueObject):
    """Which override countdowns to load: for one lock, still running only, the latest `latest`; newest first."""

    lock_key: str | None = None
    pending: bool = False
    latest: int | None = None


class LockPlanFilters(ValueObject):
    """Which lock plans to load: all the kept ones (the latest 40)."""


class GivenWarningFilters(ValueObject):
    """Which given warnings to load: those about one window, or all the kept ones (the latest 50)."""

    window_key: str | None = None


class LockEventRepositoryProtocol(
    CreateRepositoryProtocol[LockEvent],
    GetRepositoryProtocol[LockEvent, str],
    FindRepositoryProtocol[LockEvent, LockEventFilters],
    UpdateRepositoryProtocol[LockEvent],
    Protocol,
):
    """When each lock began and how it ended, by key; creating a lock that already began is a no-op."""


class OverrideRepositoryProtocol(
    CreateRepositoryProtocol[Override],
    FindRepositoryProtocol[Override, OverrideFilters],
    UpdateRepositoryProtocol[Override],
    Protocol,
):
    """Override countdowns."""


class LockPlanRepositoryProtocol(
    CreateRepositoryProtocol[LockPlan],
    GetRepositoryProtocol[LockPlan, str],
    FindRepositoryProtocol[LockPlan, LockPlanFilters],
    Protocol,
):
    """Plans decided for scheduled locks, by window key; only the latest 40 are kept."""


class ManualLockRepositoryProtocol(
    GetSingletonRepositoryProtocol[ActiveLock], UpdateSingletonRepositoryProtocol[ActiveLock | None], Protocol
):
    """The lock started with `sportlock start`; `update(None)` forgets it."""


class GivenWarningRepositoryProtocol(
    CreateRepositoryProtocol[GivenWarning], FindRepositoryProtocol[GivenWarning, GivenWarningFilters], Protocol
):
    """Warnings already given; only the latest 50 are kept."""


class DesktopToRestoreRepositoryProtocol(
    GetSingletonRepositoryProtocol[DesktopToRestore],
    UpdateSingletonRepositoryProtocol[DesktopToRestore | None],
    Protocol,
):
    """The desktop state to restore when the lock ends; `update(None)` forgets it once restored."""


class LockRuntimeProtocol(Protocol):
    """In-memory state of the running service (lost on restart, by design)."""

    current: ActiveLock | None  # lock the screen is under
    test_lock: ActiveLock | None
    waiting_for_omarchy: bool
