"""Exit when the process that launched us is gone.

The overlay runs the engine in its own process group so that killing it also reaps the pw-record
capture child. The side effect is that a Ctrl+C aimed at the terminal never reaches the engine, so
an overlay that dies without running its shutdown path leaves the engine running and holding the
port. The next overlay launch probes that port, finds it served, and adopts the orphan, inheriting
whatever configuration it started with. That is invisible: the app looks fine and silently ignores
every settings change.

The overlay passes its pid in INTERVIEWR_PARENT_PID. Nothing happens when it is unset, which is the
case for a manually run engine.
"""

from __future__ import annotations

import logging
import os
import threading

_log = logging.getLogger(__name__)

ENV_VAR = "INTERVIEWR_PARENT_PID"
_POLL_SECONDS = 2.0


def is_alive(pid: int) -> bool:
    """Whether `pid` still exists. Signal 0 checks without delivering anything."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def parent_pid() -> int | None:
    """The launcher's pid, or None when we were not launched by one."""
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        _log.warning("ignoring malformed %s=%r", ENV_VAR, raw)
        return None
    return pid if pid > 1 else None


def watch(poll_seconds: float = _POLL_SECONDS) -> None:
    """Start a daemon thread that ends this process once the launcher is gone."""
    pid = parent_pid()
    if pid is None:
        return

    def loop() -> None:
        while is_alive(pid):
            if _stop.wait(poll_seconds):
                return
        _log.warning("overlay (pid %s) is gone; shutting the engine down", pid)
        # os._exit, not sys.exit: this is a daemon thread, and the point is to release the port
        # rather than unwind cleanly through a server that has no client left.
        os._exit(0)

    _stop = threading.Event()
    threading.Thread(target=loop, name="parent-watch", daemon=True).start()
