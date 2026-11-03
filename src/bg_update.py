"""'Update all data' in a background thread, so the dashboard stays usable.

One update per dashboard process (every open browser tab sees the same
one). Progress messages are kept in memory for the sidebar to show; when
the run ends, the page's cached data is cleared once so charts pick up the
new numbers.
"""
from __future__ import annotations

import threading
import time
import traceback

_lock = threading.Lock()
_state = {"thread": None, "messages": [], "started": None, "finished": None, "error": None, "cleared": True}


def running() -> bool:
    t = _state["thread"]
    return t is not None and t.is_alive()


def start(run, **kwargs) -> bool:
    """Start run(progress=..., **kwargs) in the background. False if one is already running."""
    with _lock:
        if running():
            return False
        _state.update(messages=[], started=time.time(), finished=None, error=None, cleared=False)

        def say(msg) -> None:
            _state["messages"].append(str(msg))

        def target() -> None:
            try:
                run(progress=say, **kwargs)
            except Exception as e:  # noqa: BLE001 - shown in the sidebar
                _state["error"] = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            finally:
                _state["finished"] = time.time()

        t = threading.Thread(target=target, daemon=True, name="update-all-data")
        _state["thread"] = t
        t.start()
        return True


def status() -> dict:
    s = dict(_state)
    s["running"] = running()
    s["messages"] = list(_state["messages"])
    return s


def take_finished() -> bool:
    """True exactly once after a run ends (the caller then clears caches)."""
    with _lock:
        if _state["finished"] is not None and not _state["cleared"]:
            _state["cleared"] = True
            return True
        return False
