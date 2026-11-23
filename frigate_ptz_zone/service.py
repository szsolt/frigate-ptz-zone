"""Wires the cameras, followers and editor together and runs them."""
from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as paho

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
        store = KeyframeStore(cfg.keyframes, cfg.default_zone)
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


def _connect_mqtt(frigate: Frigate) -> None:
    """Publish zone states on Frigate's own broker, with its login and FRIGATE_MQTT_PASSWORD.

    Waits for Frigate: the followers can't write zones before it answers anyway."""
    while True:
        try:
            cfg = frigate.mqtt_config()
            break
        except OSError as e:
            log.warning("waiting for Frigate: %s", type(e).__name__)
            time.sleep(10)
    if not cfg.get("enabled") or not cfg.get("host"):
        return
    if cfg.get("user") and not os.environ.get("FRIGATE_MQTT_PASSWORD"):
        log.warning("MQTT: Frigate's broker needs a login; set FRIGATE_MQTT_PASSWORD to publish zone states")
        return
    client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="frigate-ptz-zone")
    if cfg.get("user"):
        client.username_pw_set(cfg["user"], os.environ["FRIGATE_MQTT_PASSWORD"])
    last: list[str] = [""]

    def connected(_c: object, _u: object, _f: object, reason: paho.ReasonCode, _p: object) -> None:
        if str(reason) != last[0]:  # once per change: paho retries a refused login every few minutes
            log.log(logging.WARNING if reason.is_failure else logging.INFO, "MQTT %s: %s", cfg["host"], reason)
            last[0] = str(reason)

    client.on_connect = connected
    client.connect_async(cfg["host"], int(cfg.get("port") or 1883))
    client.loop_start()
    frigate.mqtt, frigate.prefix = client, cfg.get("topic_prefix") or "frigate"


def run(settings: Settings) -> None:
    frigate = Frigate(settings.frigate_url)
    rigs = build_rigs(settings, frigate)
    for rig in rigs.values():
        atexit.register(rig.camera.close)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # docker stop: exit cleanly so atexit logs out
    editor = Editor(settings.editor_port, rigs, frigate, settings)
    threading.Thread(target=editor.serve_forever, daemon=True).start()
    threading.Thread(target=_watchdog, args=(rigs,), daemon=True).start()
    _connect_mqtt(frigate)
    threads = [threading.Thread(target=rig.follower.run, daemon=True) for rig in rigs.values()]
    for t in threads:
        t.start()
    if not threads:
        log.warning("no camera has a shift yet: idle until fit_shift is done and the service restarts")
        threading.Event().wait()
    for t in threads:
        t.join()


def add_keyframe(settings: Settings, name: str, zone: str, label: str = "") -> None:
    """Record the zone as drawn in Frigate now, at the camera's current position."""
    cfg = load_cameras(settings)[name]
    camera = open_camera(cfg.adapter, cfg.options)
    try:
        pan, tilt = camera.position()
    finally:
        camera.close()
    coords = Frigate(settings.frigate_url).zone_coords(name, zone)
    n = KeyframeStore(cfg.keyframes, cfg.default_zone).save(zone, pan, tilt, coords, label)
    print(f"recorded {name} {pan}/{tilt}; {n} keyframes")
