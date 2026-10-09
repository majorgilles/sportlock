"""The lock screen and the warning popup, as Quickshell processes.

The lock screen (holding the Wayland session lock) reads the state file once a second and
releases the lock when `locked` turns false or the file stops being refreshed, so stopping the
service always unlocks.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import override

from sportlock.shared_kernel.ports import LockScreenProtocol, WarningPopupProtocol

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
LOCK_SCREEN_DIR = UI_DIR / "lock_screen"
WARNING_POPUP_DIR = UI_DIR / "warning_popup"
log = logging.getLogger("sportlock")


class QuickshellLockScreen(LockScreenProtocol):
    """`qs -p locks/ui/lock_screen`, relaunched if it exits while a lock is active."""

    def __init__(self, state_path: Path, cli: Path) -> None:
        self.state_path = state_path
        self.cli = cli
        self.process: subprocess.Popen | None = None

    @override
    def ensure_shown(self) -> None:
        if self.process and self.process.poll() is None:
            return
        if self.process is not None:
            log.warning("lock screen exited with code %s while a lock is active; relaunching", self.process.returncode)
        env = dict(os.environ, SPORTLOCK_STATE=str(self.state_path), SPORTLOCK_BIN=str(self.cli))
        self.process = subprocess.Popen(["qs", "-p", str(LOCK_SCREEN_DIR)], env=env, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)


class QuickshellWarningPopup(WarningPopupProtocol):
    """`qs -p locks/ui/warning_popup`, with what to show passed as JSON in $SPORTLOCK_POPUP."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None

    @override
    def show(self, content: dict) -> None:
        self.close()
        env = dict(os.environ, SPORTLOCK_POPUP=json.dumps(content))
        try:
            self.process = subprocess.Popen(["qs", "-p", str(WARNING_POPUP_DIR)], env=env, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            log.warning("could not show the warning popup: %s", error)

    @override
    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.process = None
