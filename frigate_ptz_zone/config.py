"""Settings from the environment, and the camera list from cameras.json."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import jsonfile


@dataclass(frozen=True)
class Settings:
    data_dir: Path      # cameras.json, keyframes, and what the service learns
    frigate_url: str
    editor_port: int
    cameras_file: Path
    mqtt_host: str = ""     # set to publish each zone's on/off state to MQTT
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_prefix: str = "frigate"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        data_dir = Path(env.get("DATA_DIR", "."))
        return cls(data_dir=data_dir,
                   frigate_url=env.get("FRIGATE_API", "http://frigate:5000"),
                   editor_port=int(env.get("EDITOR_PORT", "8781")),
                   cameras_file=Path(env.get("CAMERAS", data_dir / "cameras.json")),
                   mqtt_host=env.get("MQTT_HOST", ""), mqtt_port=int(env.get("MQTT_PORT", "1883")),
                   mqtt_user=env.get("MQTT_USER", ""), mqtt_prefix=env.get("MQTT_TOPIC_PREFIX", "frigate"))


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
    zones: tuple[str, ...]      # the Frigate zones that follow the camera
    keyframes: Path
    model: ZoneModel | None     # None until fit_shift has measured the camera
    adapter: str = "reolink"    # module in adapters/
    options: dict[str, Any] = field(default_factory=dict)  # for the adapter, e.g. host
    masks: dict[str, int] = field(default_factory=dict)  # sky masks: Frigate motion mask -> the tilt from which it is on
    default_zone: str = ""      # the zone of keyframes saved before zones had names
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
        old = d.pop("zone", None)  # before several zones: one zone, one mask
        zones = tuple(d.pop("zones", [old] if old else []))
        masks = dict(d.pop("masks", {}))
        if "mask" in d:
            masks[d.pop("mask")] = d.pop("mask_tilt")
        return cls(name=name, zones=zones, keyframes=data_dir / d.pop("keyframes", f"keyframes-{name}.json"),
                   model=model, adapter=d.pop("type", cls.adapter), masks=masks,
                   default_zone=old or (zones[0] if zones else ""), pan_range=pan_range, options=d)


def save_roles(settings: Settings, name: str, zones: list[str], masks: dict[str, int]) -> None:
    """Write one camera's followed zones and sky masks to cameras.json, keeping its other keys."""
    entries = json.loads(settings.cameras_file.read_text())
    entry = entries[name]
    for old in ("zone", "mask", "mask_tilt"):
        entry.pop(old, None)
    entry["zones"], entry["masks"] = zones, masks
    jsonfile.write(settings.cameras_file, entries, indent=2)


def load_cameras(settings: Settings) -> dict[str, CameraConfig]:
    entries = json.loads(settings.cameras_file.read_text())
    return {name: CameraConfig.from_dict(name, e, settings.data_dir) for name, e in entries.items()}
