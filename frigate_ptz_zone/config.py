"""Settings from the environment, and the camera list from cameras.json."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Settings:
    data_dir: Path      # cameras.json, keyframes, and what the service learns
    frigate_url: str
    editor_port: int
    cameras_file: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        data_dir = Path(env.get("DATA_DIR", "."))
        return cls(data_dir=data_dir,
                   frigate_url=env.get("FRIGATE_API", "http://frigate:5000"),
                   editor_port=int(env.get("EDITOR_PORT", "8781")),
                   cameras_file=Path(env.get("CAMERAS", data_dir / "cameras.json")))


@dataclass(frozen=True)
class Lens:
    """The camera's turn as a rotation of the view through a lens with barrel distortion.

    f is the focal length in grid px, k1 the distortion, pan and tilt the degrees per camera unit.
    Fitted from the keyframes by fit_lens.
    """
    f: float
    pan: float
    tilt: float
    k1: float


@dataclass(frozen=True)
class ZoneModel:
    """How the picture moves when the camera turns, and how keyframes are combined.

    grid: (width, height) in px of the raster the zone is computed on.
    shift: (ax, bx, ay, by), the picture shift per pan/tilt unit on that grid, fitted from snapshot pairs:
        dx = ax * dpan + bx * dtilt, dy = ay * dpan + by * dtilt. Distances to keyframes are measured in this shift.
    lens: when set, warps zones by rotating the view instead of shifting it.
    max_dist: grid px from the nearest keyframe before the zone is switched off.
    agree: how many of the nearest keyframes (within max_dist) must all cover a spot. Where fewer are
        in range, those decide.
    """
    grid: tuple[int, int]
    shift: tuple[float, float, float, float]
    lens: Lens | None = None
    max_dist: float = 260
    agree: int = 2


@dataclass(frozen=True)
class CameraConfig:
    name: str
    zone: str                   # the Frigate zone that follows the camera
    keyframes: Path
    model: ZoneModel | None     # None until fit_shift has measured the camera
    adapter: str = "reolink"    # module in adapters/
    options: dict[str, Any] = field(default_factory=dict)  # for the adapter, e.g. host
    mask: str | None = None     # a Frigate motion mask that is on while the camera looks up
    mask_tilt: int | None = None  # ... at this tilt or higher up
    pan_range: tuple[int, int] | None = None  # outside it the camera is sent home

    @classmethod
    def from_dict(cls, name: str, entry: Mapping[str, Any], data_dir: Path) -> CameraConfig:
        """One cameras.json entry. Keys the service does not know go to the adapter."""
        d = dict(entry)
        lens, grid, shift = d.pop("lens", None), d.pop("grid", None), d.pop("shift", None)
        max_dist, agree = d.pop("max_dist", ZoneModel.max_dist), d.pop("agree", ZoneModel.agree)
        model = ZoneModel(grid=tuple(grid), shift=tuple(shift), lens=Lens(**lens) if lens else None,
                          max_dist=max_dist, agree=agree) if shift else None
        pan_range = (d.pop("pan_min"), d.pop("pan_max")) if "pan_min" in d else None
        if "mask" in d and "mask_tilt" not in d:
            raise ValueError(f"{name}: mask needs mask_tilt")
        return cls(name=name, zone=d.pop("zone"), keyframes=data_dir / d.pop("keyframes", f"keyframes-{name}.json"),
                   model=model, adapter=d.pop("type", cls.adapter), mask=d.pop("mask", None), mask_tilt=d.pop("mask_tilt", None),
                   pan_range=pan_range, options=d)


def load_cameras(settings: Settings) -> dict[str, CameraConfig]:
    entries = json.loads(settings.cameras_file.read_text())
    return {name: CameraConfig.from_dict(name, e, settings.data_dir) for name, e in entries.items()}
