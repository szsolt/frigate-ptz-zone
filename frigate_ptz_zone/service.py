"""Wires the cameras, followers and editor together and runs them."""
from __future__ import annotations

import atexit
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass

from .adapters import PTZ, open_camera
from .config import CameraConfig, Settings, ZoneModel, load_cameras
from .editor import Editor
from .follower import Follower
from .frigate import Frigate
from .keyframes import KeyframeStore
from .motion import Learned, Motion, Pauses

log = logging.getLogger(__name__)


@dataclass
class Rig:
    """One camera and everything that works it. The camera's one login is shared by all."""
    cfg: CameraConfig
    model: ZoneModel  # cfg.model, known to be set
    camera: PTZ
    keyframes: KeyframeStore
    follower: Follower
    motion: Motion


def build_rigs(settings: Settings, frigate: Frigate) -> dict[str, Rig]:
    """frigate is shared by all followers: its lock keeps their config writes one at a time."""
    learned = Learned(settings.data_dir / "learned.json")
    pauses = Pauses(settings.data_dir / "paused.json")  # a pause from before a restart still ends on time
    rigs = {}
    for name, cfg in load_cameras(settings).items():
        if cfg.model is None:
            log.warning("[%s] skipped: no shift yet, run python3 -m frigate_ptz_zone.fit_shift %s", name, name)
            continue
        camera = open_camera(cfg.adapter, cfg.options)
        store = KeyframeStore(cfg.keyframes)
        rigs[name] = Rig(cfg, cfg.model, camera, store, Follower(cfg, cfg.model, camera, store, frigate),
                         Motion(name, camera, learned, pauses))
    return rigs


def _watchdog(rigs: dict[str, Rig]) -> None:
    while True:
        time.sleep(0.2)
        for rig in rigs.values():
            try:
                rig.motion.tick()
            except Exception:  # the watchdog must keep running: it stops held arrows
                log.exception("[%s] watchdog", rig.cfg.name)


def run(settings: Settings) -> None:
    frigate = Frigate(settings.frigate_url)
    rigs = build_rigs(settings, frigate)
    for rig in rigs.values():
        atexit.register(rig.camera.close)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # docker stop: exit cleanly so atexit logs out
    editor = Editor(settings.editor_port, rigs, frigate)
    threading.Thread(target=editor.serve_forever, daemon=True).start()
    threading.Thread(target=_watchdog, args=(rigs,), daemon=True).start()
    threads = [threading.Thread(target=rig.follower.run, daemon=True) for rig in rigs.values()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def add_keyframe(settings: Settings, name: str, label: str = "") -> None:
    """Record the zone as drawn in Frigate now, at the camera's current position."""
    cfg = load_cameras(settings)[name]
    camera = open_camera(cfg.adapter, cfg.options)
    try:
        pan, tilt = camera.position()
    finally:
        camera.close()
    coords = Frigate(settings.frigate_url).zone_coords(name, cfg.zone)
    n = KeyframeStore(cfg.keyframes).save(pan, tilt, coords, label)
    print(f"recorded {name} {pan}/{tilt}; {n} keyframes")
