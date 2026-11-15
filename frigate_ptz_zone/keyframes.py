"""Keyframes: a zone as drawn at one camera position, stored per camera in a JSON file."""
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
    zone: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any], zone: str = "") -> Keyframe:
        return cls(pan=d["pan"], tilt=d["tilt"], coords=tuple(d["coords"]), label=d.get("label", ""), at=d.get("at", ""),
                   zone=d.get("zone", zone))

    def to_dict(self) -> dict[str, Any]:
        return {"zone": self.zone, "pan": self.pan, "tilt": self.tilt, "points": len(self.coords) // 2,
                "coords": list(self.coords), "label": self.label, "at": self.at}


class KeyframeStore:
    """One camera's keyframes, for all its zones. The follower reloads the file whenever it changes."""

    def __init__(self, path: Path, default_zone: str = ""):
        self.path, self.default_zone = path, default_zone  # the zone of keyframes saved without one
        # The set from before the last optimize
        self.backup = path.with_name("undo-" + path.name)
        self._lock = threading.Lock()

    def mtime(self) -> float:
        return self.path.stat().st_mtime if self.path.exists() else 0.0

    def load(self, zone: str | None = None) -> list[Keyframe]:
        """The keyframes of one zone, or of all; none before the first is saved."""
        if not self.path.exists():
            return []
        kfs = [Keyframe.from_dict(d, self.default_zone) for d in json.loads(self.path.read_text())]
        return kfs if zone is None else [k for k in kfs if k.zone == zone]

    def save(self, zone: str, pan: int, tilt: int, coords: Sequence[float], label: str,
             replace: tuple[int, int] | None = None) -> int:
        """Add the zone's keyframe, or overwrite its one at this position or at `replace`. Returns the zone's count."""
        kf = Keyframe(pan, tilt, tuple(coords), label, time.strftime("%Y-%m-%dT%H:%M:%S"), zone)
        with self._lock:
            kfs = [k for k in self.load() if k.zone != zone or (k.pan, k.tilt) not in ((pan, tilt), replace)]
            self._write(self.path, [*kfs, kf])
            self.backup.unlink(missing_ok=True)  # an undo now would lose this edit
            return sum(k.zone == zone for k in kfs) + 1

    def delete(self, zone: str, pan: int, tilt: int) -> None:
        with self._lock:
            self._write(self.path, [k for k in self.load() if (k.zone, k.pan, k.tilt) != (zone, pan, tilt)])
            self.backup.unlink(missing_ok=True)

    def keep_only(self, zone: str, kept: Iterable[Keyframe]) -> None:
        """Replace the zone's set, keeping the whole old file for undo() until the next edit."""
        with self._lock:
            kfs = self.load()
            self._write(self.backup, kfs)
            self._write(self.path, [*(k for k in kfs if k.zone != zone), *kept])

    def name_zones(self) -> None:
        """Write the default zone into keyframes saved without one, before cameras.json stops naming it."""
        with self._lock:
            if self.path.exists():
                self._write(self.path, self.load())

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
