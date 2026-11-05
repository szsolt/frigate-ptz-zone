"""Keyframes: the zone as drawn at one camera position, stored per camera in a JSON file."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import jsonfile


@dataclass(frozen=True)
class Keyframe:
    pan: int
    tilt: int
    coords: tuple[float, ...]   # normalised x, y pairs, as in Frigate's config
    label: str = ""
    at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Keyframe:
        return cls(pan=d["pan"], tilt=d["tilt"], coords=tuple(d["coords"]), label=d.get("label", ""), at=d.get("at", ""))

    def to_dict(self) -> dict[str, Any]:
        return {"pan": self.pan, "tilt": self.tilt, "points": len(self.coords) // 2, "coords": list(self.coords),
                "label": self.label, "at": self.at}


class KeyframeStore:
    """One camera's keyframes. The follower reloads the file whenever it changes."""

    def __init__(self, path: Path):
        self.path = path
        # The set from before the last optimize
        self.backup = path.with_name("undo-" + path.name)
        self._lock = threading.Lock()

    def mtime(self) -> float:
        return self.path.stat().st_mtime if self.path.exists() else 0.0

    def load(self) -> list[Keyframe]:
        """The keyframes; none before the first is saved."""
        if not self.path.exists():
            return []
        return [Keyframe.from_dict(d) for d in json.loads(self.path.read_text())]

    def save(self, pan: int, tilt: int, coords: Sequence[float], label: str,
             replace: tuple[int, int] | None = None) -> int:
        """Add the keyframe, or overwrite the one at its position or at `replace`. Returns the count."""
        kf = Keyframe(pan, tilt, tuple(coords), label, time.strftime("%Y-%m-%dT%H:%M:%S"))
        with self._lock:
            kfs = [k for k in self.load() if (k.pan, k.tilt) not in ((pan, tilt), replace)]
            self._write(self.path, [*kfs, kf])
            self.backup.unlink(missing_ok=True)  # an undo now would lose this edit
            return len(kfs) + 1

    def delete(self, pan: int, tilt: int) -> None:
        with self._lock:
            self._write(self.path, [k for k in self.load() if (k.pan, k.tilt) != (pan, tilt)])
            self.backup.unlink(missing_ok=True)

    def keep_only(self, kept: Iterable[Keyframe]) -> None:
        """Replace the set, keeping the old one for undo() until the next edit."""
        with self._lock:
            self._write(self.backup, self.load())
            self._write(self.path, kept)

    @property
    def can_undo(self) -> bool:
        return self.backup.exists()

    def undo(self) -> bool:
        with self._lock:
            if not self.backup.exists():
                return False
            self.backup.replace(self.path)
            return True

    @staticmethod
    def _write(path: Path, kfs: Iterable[Keyframe]) -> None:
        jsonfile.write(path, [k.to_dict() for k in sorted(kfs, key=lambda k: (k.tilt, k.pan))], indent=1)
