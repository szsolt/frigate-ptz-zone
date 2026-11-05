"""Camera adapters: the only camera-specific code. One module per camera type, each with a Camera class.

The type comes from the camera's "type" in cameras.json (default "reolink"). Keys the service does
not use itself, such as "host", go to the adapter as its options. To add a camera type, copy
reolink.py and fill in the methods of PTZ below.
"""
from __future__ import annotations

import importlib
import threading
from typing import Any

OPS = ("Left", "Right", "Up", "Down", "LeftUp", "LeftDown", "RightUp", "RightDown")


class PTZ:
    """What the zone service needs from a PTZ camera.

    Positions are the camera's own pan/tilt units, as integers, with tilt growing downwards.
    Speeds run from 1 (slowest) to 64 (fastest). Every request to the camera must hold self.lock:
    a timed move holds it until its Stop, so nothing else is sent in between.
    """

    def __init__(self, options: dict[str, Any]):
        self.options = options
        self.lock = threading.RLock()  # held across a timed move, so nothing else is sent before its Stop

    def position(self) -> tuple[int, int]:
        """(pan, tilt) now."""
        raise NotImplementedError

    def move(self, op: str, speed: int = 10) -> None:
        """Start moving in one of OPS until the next call, or stop with op "Stop"."""
        raise NotImplementedError

    def home(self) -> None:
        """Back to the camera's home view."""
        raise NotImplementedError

    def suspend(self) -> Any:
        """Turn off whatever moves the camera by itself (its own tracking, return to home) while the
        editor is in use. Returns what restore() needs, kept as JSON across restarts."""
        return None

    def restore(self, state: Any) -> None:
        pass

    def close(self) -> None:
        """Free the camera's session at exit."""


def open_camera(kind: str, options: dict[str, Any]) -> PTZ:
    return importlib.import_module(f"{__name__}.{kind}").Camera(options)
