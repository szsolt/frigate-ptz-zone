"""The few Frigate HTTP API calls the service makes."""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from typing import Any


class Frigate:
    def __init__(self, url: str, mqtt: Any = None, prefix: str = "frigate"):
        """mqtt: a connected paho client, to publish each zone's on/off state where Frigate would."""
        self.url = url.rstrip("/")
        self.mqtt, self.prefix = mqtt, prefix
        self._write = threading.Lock()  # one config write at a time

    def set_zones(self, camera: str, zones: dict[str, str | None]) -> None:
        """Write each zone's coordinates, or switch it off where they are None, in one change."""
        q = {}
        for zone, coords in zones.items():
            z = f"cameras.{camera}.zones.{zone}"
            q |= {f"{z}.enabled": "false"} if coords is None else {f"{z}.coordinates": coords, f"{z}.enabled": "true"}
        self._config_set(camera, q, "zones")
        if self.mqtt:
            # Frigate publishes this topic only at start and on MQTT commands, not on config writes.
            for zone, coords in zones.items():
                self.mqtt.publish(f"{self.prefix}/{camera}/zone/{zone}/state", "OFF" if coords is None else "ON",
                                  qos=1, retain=True)

    def set_mask(self, camera: str, mask: str, on: bool) -> None:
        """Switch a motion mask on or off. Its shape is drawn in Frigate's UI."""
        self._config_set(camera, {f"cameras.{camera}.motion.mask.{mask}.enabled": str(on).lower()}, "motion")

    def zone_coords(self, camera: str, zone: str) -> list[float]:
        """The zone as Frigate's config has it now."""
        coords = self._camera(camera)["zones"][zone]["coordinates"]
        return [float(v) for v in coords.split(",")]

    def names(self, camera: str) -> tuple[list[str], list[str]]:
        """The camera's zones and named motion masks."""
        cam = self._camera(camera)
        mask = cam["motion"].get("mask") or {}
        return list(cam["zones"]), list(mask) if isinstance(mask, dict) else []

    def mqtt_config(self) -> dict:
        """Frigate's mqtt section: host, port, user, topic_prefix. The password is redacted."""
        return dict(self._config()["mqtt"])

    def _camera(self, camera: str) -> dict:
        return dict(self._config()["cameras"][camera])

    def _config(self) -> dict:
        with urllib.request.urlopen(f"{self.url}/api/config", timeout=10) as r:
            return dict(json.loads(r.read()))

    def latest_jpg(self, camera: str, height: int = 540) -> bytes:
        with urllib.request.urlopen(f"{self.url}/api/{camera}/latest.jpg?height={height}", timeout=10) as r:
            return r.read()

    def _config_set(self, camera: str, query: dict[str, str], topic: str) -> None:
        """Frigate saves config.yml and applies the change live."""
        req = urllib.request.Request(
            f"{self.url}/api/config/set?{urllib.parse.urlencode(query, safe=',')}", method="PUT",
            data=json.dumps({"requires_restart": 0, "update_topic": f"config/cameras/{camera}/{topic}"}).encode(),
            headers={"Content-Type": "application/json"})
        with self._write, urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
        if not res.get("success"):
            raise RuntimeError(res)
