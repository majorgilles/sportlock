"""The running service's lock state, in memory (lost on restart, by design: a restart re-decides)."""

from __future__ import annotations

from sportlock.locks.domain.locks import ActiveLock
from sportlock.locks.domain.repositories import LockRuntimeProtocol


class InMemoryLockRuntime(LockRuntimeProtocol):
    """Plain attributes."""

    def __init__(self) -> None:
        self.current: ActiveLock | None = None
        self.test_lock: ActiveLock | None = None
        self.waiting_for_omarchy = False
